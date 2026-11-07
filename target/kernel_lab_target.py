#!/usr/bin/env python3
"""Root-owned, command-allowlisted Raspberry Pi target helper.

Install as /usr/local/libexec/kernel-lab-target (owner root, mode 0755).
Controller stages data as an ordinary SSH user, then invokes this helper
through a narrow sudoers rule. No arbitrary command execution subcommand.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

INCOMING = Path('/var/tmp/kernel-lab/incoming')
BOOT = Path('/boot/firmware')
MODULES = Path('/lib/modules')
PENDING = Path('/var/lib/kernel-lab/pending.json')
ALLOW = Path('/etc/kernel-lab/allow-rpi-tryboot')
SLUG = re.compile(r'^[a-z][a-z0-9-]{1,63}$')
RELEASE = re.compile(r'^[A-Za-z0-9.+_~-]{3,160}$')
MEASURE = re.compile(r'T:\s*\d+.*?Min:\s*(\d+).*?Avg:\s*(\d+(?:\.\d+)?).*?Max:\s*(\d+)', re.I)


def output(**kwargs):
    print(json.dumps(kwargs, sort_keys=True))


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def checked_id(value):
    if not SLUG.fullmatch(value):
        fail('invalid experiment id')
    return value


def check_release(value):
    if not RELEASE.fullmatch(value) or '/' in value or '..' in value:
        fail('invalid kernel release')
    return value


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def probe():
    os_release = Path('/etc/os-release').read_text()
    os_id = next((x.split('=', 1)[1].strip('"') for x in os_release.splitlines() if x.startswith('ID=')), 'unknown')
    model_path = Path('/proc/device-tree/model')
    model = model_path.read_bytes().replace(b'\x00', b'').decode(errors='replace') if model_path.exists() else 'unknown'
    cmdline = Path('/proc/cmdline').read_text().strip() if Path('/proc/cmdline').exists() else ''
    output(os_id=os_id, model=model, kernelrelease=os.uname().release,
           arch=os.uname().machine, boot_mounted=BOOT.is_mount(),
           boot_config=(BOOT / 'config.txt').exists(), cmdline=cmdline,
           tryboot_allowed=ALLOW.exists(),
           piboot_try_installed=shutil.which('piboot-try') is not None)


def prepare(exp_id):
    checked_id(exp_id)
    user = os.environ.get('SUDO_USER')
    if not user or user == 'root':
        fail('prepare must be invoked through sudo by the unprivileged lab user')
    account = pwd.getpwnam(user)
    INCOMING.mkdir(parents=True, exist_ok=True)
    path = INCOMING / exp_id
    if path.is_symlink():
        fail('symlink in staging directory')
    path.mkdir(mode=0o700, exist_ok=True)
    os.chown(path, account.pw_uid, account.pw_gid)
    path.chmod(0o700)
    output(path=str(path), ready=True)


def load_artifact(exp_id):
    checked_id(exp_id)
    path = INCOMING / exp_id
    if path.is_symlink() or not path.is_dir():
        fail('invalid staged path')
    manifest = json.loads((path / 'manifest.json').read_text())
    if manifest['experiment_id'] != exp_id:
        fail('manifest experiment id mismatch')
    release = check_release(manifest['kernelrelease'])
    for filename, key in (
        ('kernel.img', 'image_sha256'), ('modules.tar.gz', 'modules_sha256'),
        ('config', 'config_sha256'), ('dtbs.tar.gz', 'dtbs_sha256'),
    ):
        file = path / filename
        if not file.is_file() or file.is_symlink() or sha256(file) != manifest[key]:
            fail(f'artifact checksum mismatch: {filename}')
    if manifest['localversion'] not in release:
        fail('kernelrelease does not contain requested localversion')
    return path, manifest


def safe_extract_modules(archive, root, release):
    """Extract only regular files under lib/modules/<release>, no links or devices."""
    prefix = f'lib/modules/{release}/'
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            name = member.name.removeprefix('./')
            if name.rstrip('/') in ('lib', 'lib/modules', f'lib/modules/{release}'):
                continue
            if not name.startswith(prefix) or not (member.isdir() or member.isfile()):
                fail(f'unsafe module archive entry: {name}')
            relative = Path(name)
            if '..' in relative.parts or relative.is_absolute():
                fail(f'path traversal in modules: {name}')
            destination = root / relative
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as input_file, destination.open('wb') as output_file:
                    shutil.copyfileobj(input_file, output_file)
                destination.chmod(member.mode & 0o755)


def install_pios(exp_id):
    checked_id(exp_id)
    if not ALLOW.is_file():
        fail('boot not provisioned; operator must create /etc/kernel-lab/allow-rpi-tryboot')
    if not BOOT.is_mount() or not (BOOT / 'config.txt').is_file():
        fail('/boot/firmware must be a mounted boot filesystem with config.txt')
    if 'ID=raspbian' not in Path('/etc/os-release').read_text() and 'ID=debian' not in Path('/etc/os-release').read_text():
        fail('Pi OS installer does not support this distro')
    if PENDING.exists() or (BOOT / 'tryboot.txt').exists():
        fail('existing pending test boot or tryboot.txt; do not overwrite')
    staged, manifest = load_artifact(exp_id)
    release = check_release(manifest['kernelrelease'])
    module_dir = MODULES / release
    if module_dir.exists():
        fail('module directory already exists; refusing replacement')
    kernel_name = f'kernel-kl-{exp_id[-12:]}.img'
    ramdisk_name = f'initramfs-kl-{exp_id[-12:]}'
    for name in (kernel_name, ramdisk_name):
        if (BOOT / name).exists():
            fail(f'boot artifact already exists: {name}')
    if not shutil.which('mkinitramfs'):
        fail('mkinitramfs missing: install initramfs-tools first')
    if not shutil.which('depmod'):
        fail('depmod missing: install kmod first')
    config = (BOOT / 'config.txt').read_text()
    if not config.strip():
        fail('empty boot config refused')
    if shutil.disk_usage(BOOT).free < 128 * 1024 * 1024:
        fail('require >=128 MiB free on boot partition for staging')
    # Stage new modules before initramfs generation. A unique release avoids
    # modifying modules for the known-good kernel.
    with tempfile.TemporaryDirectory(prefix='kl-mods-') as temporary:
        safe_extract_modules(staged / 'modules.tar.gz', Path(temporary), release)
        src = Path(temporary) / 'lib/modules' / release
        if not src.is_dir():
            fail('modules archive does not include expected release')
        shutil.copytree(src, module_dir)
    subprocess.run(['depmod', '-a', release], check=True)
    # No existing boot asset is replaced. Explicit initramfs reference avoids
    # depending on different auto_initramfs defaults across Pi OS versions.
    kernel_tmp = BOOT / (kernel_name + '.tmp')
    initrd_tmp = BOOT / (ramdisk_name + '.tmp')
    try:
        shutil.copy2(staged / 'kernel.img', kernel_tmp)
        subprocess.run(['mkinitramfs', '-o', str(initrd_tmp), release], check=True)
        if not initrd_tmp.is_file() or initrd_tmp.stat().st_size == 0:
            fail('initramfs generation returned an empty image')
        os.replace(kernel_tmp, BOOT / kernel_name)
        os.replace(initrd_tmp, BOOT / ramdisk_name)
        # Condition at end overrides board sections in normal config; inspect
        # your board firmware/config semantics before enabling this adapter.
        try_text = config.rstrip() + ('\n\n# kernel-lab one-shot test boot\n'
            '[all]\n' f'kernel={kernel_name}\n'
            'auto_initramfs=0\n' f'initramfs {ramdisk_name} followkernel\n')
        pending = {'experiment_id': exp_id, 'kernelrelease': release,
                   'kernel': kernel_name, 'initramfs': ramdisk_name,
                   'installed_at': datetime.now(timezone.utc).isoformat()}
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        temp_config = BOOT / 'tryboot.txt.kl-tmp'
        temp_config.write_text(try_text)
        os.replace(temp_config, BOOT / 'tryboot.txt')
        PENDING.write_text(json.dumps(pending, indent=2) + '\n')
        output(installed=True, boot_activated=False, pending=pending)
    finally:
        kernel_tmp.unlink(missing_ok=True)
        initrd_tmp.unlink(missing_ok=True)


def boot_try(exp_id):
    checked_id(exp_id)
    if not PENDING.is_file():
        fail('no pending experiment')
    state = json.loads(PENDING.read_text())
    if state['experiment_id'] != exp_id:
        fail('pending experiment mismatch')
    if not (BOOT / 'tryboot.txt').is_file():
        fail('tryboot.txt missing')
    # Defer restart to permit SSH to receive the JSON acknowledgment.
    subprocess.run(['systemd-run', '--on-active=3s', '/usr/sbin/reboot', '0 tryboot'], check=True,
                   capture_output=True, text=True)
    output(scheduled=True, experiment_id=exp_id, boot_mode='one-shot tryboot')


def status():
    pending = json.loads(PENDING.read_text()) if PENDING.is_file() else None
    release = os.uname().release
    output(kernelrelease=release, pending=pending,
           candidate_running=bool(pending and pending['kernelrelease'] == release))


def capture(command):
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        return completed.stdout.strip() if completed.returncode == 0 else f'error ({completed.returncode}): {completed.stderr.strip()}'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def environment():
    return {
        'os_release': Path('/etc/os-release').read_text(),
        'uname': capture(['uname', '-a']),
        'cmdline': Path('/proc/cmdline').read_text().strip(),
        'governor': {p.parent.name: p.read_text().strip() for p in Path('/sys/devices/system/cpu/cpufreq').glob('policy*/scaling_governor')},
        'temperature': capture(['vcgencmd', 'measure_temp']),
        'throttling': capture(['vcgencmd', 'get_throttled']),
        'cyclictest_version': capture(['cyclictest', '--version']),
        'stress_ng_version': capture(['stress-ng', '--version']),
    }


def benchmark(exp_id, spec_json):
    checked_id(exp_id)
    spec = json.loads(spec_json)
    allowed = {'duration_seconds', 'repetitions', 'priority', 'interval_us', 'load', 'stress_workers'}
    if set(spec) != allowed:
        fail('invalid benchmark specification keys')
    duration, repeat, priority, interval, workers = [int(spec[k]) for k in
        ('duration_seconds', 'repetitions', 'priority', 'interval_us', 'stress_workers')]
    if not (5 <= duration <= 86400 and 1 <= repeat <= 100 and 1 <= priority <= 95 and
            50 <= interval <= 1_000_000 and 1 <= workers <= 32 and
            spec['load'] in ('idle', 'cpu-stress')):
        fail('benchmark arguments outside allowed bounds')
    if not shutil.which('cyclictest'):
        fail('cyclictest not installed (rt-tests package)')
    if spec['load'] == 'cpu-stress' and not shutil.which('stress-ng'):
        fail('stress-ng not installed')
    started = datetime.now(timezone.utc).isoformat()
    before = environment()
    outputs = []
    measurements = []
    for index in range(repeat):
        stress = None
        if spec['load'] == 'cpu-stress':
            stress = subprocess.Popen(['stress-ng', '--cpu', str(workers), '--timeout', f'{duration + 3}s'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            cmd = ['cyclictest', '-q', '-m', '-t1', '-p', str(priority), '-i', str(interval),
                   '-D', f'{duration}s']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 45, check=False)
            output_row = {'repetition': index + 1, 'command': cmd, 'returncode': result.returncode,
                          'stdout': result.stdout[-100000:], 'stderr': result.stderr[-20000:]}
            outputs.append(output_row)
            match = MEASURE.search(result.stdout)
            if result.returncode == 0 and match:
                mn, avg, mx = match.groups()
                measurements.append({'repetition': index + 1, 'min_us': float(mn), 'avg_us': float(avg),
                                     'max_us': float(mx), 'p99_us': None, 'p999_us': None,
                                     'sample_count': None})
            else:
                measurements.append({'repetition': index + 1, 'min_us': None, 'avg_us': None,
                                     'max_us': None, 'p99_us': None, 'p999_us': None,
                                     'sample_count': None})
        finally:
            if stress:
                stress.terminate()
                try:
                    stress.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stress.kill()
                    stress.communicate()
    after = environment()
    complete = all(x['max_us'] is not None for x in measurements)
    output(schema_version=1, experiment_id=exp_id, target='', started_at=started,
           completed_at=datetime.now(timezone.utc).isoformat(), kernelrelease=os.uname().release,
           profile=spec, successful=complete, measurements=measurements,
           environment={'before': before, 'after': after}, raw_outputs=outputs,
           failure=None if complete else 'At least one cyclictest execution failed or was unparseable')


def main():
    if os.geteuid() != 0:
        fail('target helper must run as root through controlled sudo')
    if len(sys.argv) < 2:
        fail('usage: kernel-lab-target COMMAND [args]')
    command, args = sys.argv[1], sys.argv[2:]
    expected = {'probe': 0, 'status': 0, 'prepare': 1, 'install-pios': 1,
                'boot-try': 1, 'benchmark': 2}
    if command not in expected or len(args) != expected[command]:
        fail('command not allowlisted or wrong number of arguments')
    return {
        'probe': lambda: probe(), 'status': lambda: status(),
        'prepare': lambda: prepare(*args), 'install-pios': lambda: install_pios(*args),
        'boot-try': lambda: boot_try(*args), 'benchmark': lambda: benchmark(*args),
    }[command]()


if __name__ == '__main__':
    main()
