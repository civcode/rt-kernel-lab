"""Manual-first orchestration entrypoint. No workflow runs without an operator command."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import typer

from .build import IMAGE, ROOT, build, validate_experiment
from .config import load_experiment, load_inventory
from .models import ArtifactManifest, Target
from .remote import Remote
from .storage import insert, compare as compare_results
from .locks import target_lock
from .matrix import expand

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
DEFAULT_INVENTORY = Path('inventory/targets.yaml')
DEFAULT_ARTIFACTS = Path('artifacts')
DEFAULT_STATE = Path('state')


def get_target(name: str, inventory_path: Path) -> Target:
    inventory = load_inventory(inventory_path)
    if name not in inventory.targets:
        raise typer.BadParameter(f'unknown target {name}')
    return inventory.targets[name]


def artifact(artifact_id: str, root: Path):
    if not artifact_id or '/' in artifact_id or '..' in artifact_id:
        raise typer.BadParameter('invalid artifact id')
    dist = root / artifact_id / 'dist'
    manifest = ArtifactManifest.model_validate_json((dist / 'manifest.json').read_text())
    if manifest.experiment_id != artifact_id:
        raise typer.BadParameter('manifest does not match artifact id')
    return dist, manifest


def ensure_target(remote: Remote, target: Target):
    probe = remote.run('probe')
    model = probe['model']
    if 'Raspberry Pi' not in model or target.board[-1] not in model:
        raise RuntimeError(f'board mismatch: expected {target.board}; reported {model}')
    if probe['arch'] not in ('aarch64', 'arm64'):
        raise RuntimeError(f'expected arm64 target, got {probe["arch"]}')
    if target.distro == 'ubuntu' and probe['os_id'] != 'ubuntu':
        raise RuntimeError(f'distro mismatch: expected Ubuntu, got {probe["os_id"]}')
    if target.distro == 'raspberrypi-os' and probe['os_id'] not in ('raspbian', 'debian'):
        raise RuntimeError(f'distro mismatch: expected Raspberry Pi OS, got {probe["os_id"]}')
    return probe


@app.command('targets')
def targets(inventory: Path = typer.Option(DEFAULT_INVENTORY)):
    for name, target in load_inventory(inventory).targets.items():
        typer.echo(f'{name:20} {target.board:4} {target.distro:15} {target.boot_mode:12} {target.host}')


@app.command('validate')
def validate(experiment: Path, inventory: Path = typer.Option(DEFAULT_INVENTORY)):
    exp = load_experiment(experiment)
    target = get_target(exp.target, inventory)
    src, fragments = validate_experiment(exp, target)
    typer.echo(json.dumps({'experiment': exp.name, 'target': exp.target,
                           'source': str(src), 'fragments': [str(x) for x in fragments]}, indent=2))


@app.command('matrix-plan')
def matrix_plan(matrix: Path, output: Path = typer.Option(Path('experiments/generated')),
                max_candidates: int = typer.Option(128, min=1, max=1024)):
    files = expand(matrix, output, max_candidates=max_candidates)
    for file in files:
        typer.echo(str(file))
    typer.echo(f'Generated {len(files)} experiment YAML files. No build or deployment executed.')


@app.command('build-image')
def build_image():
    subprocess.run(['docker', 'build', '-t', IMAGE, '-f', str(ROOT / 'docker/Dockerfile'), str(ROOT)], check=True)


@app.command('build')
def build_command(experiment: Path, inventory: Path = typer.Option(DEFAULT_INVENTORY),
                  artifacts: Path = typer.Option(DEFAULT_ARTIFACTS), jobs: int = typer.Option(0, min=0)):
    exp = load_experiment(experiment)
    target = get_target(exp.target, inventory)
    dist, manifest = build(exp, target, artifacts, jobs)
    typer.echo(f'Artifact: {dist}\nID: {manifest.experiment_id}\nKernel: {manifest.kernelrelease}')


@app.command('probe')
def probe(name: str, inventory: Path = typer.Option(DEFAULT_INVENTORY)):
    target = get_target(name, inventory)
    typer.echo(json.dumps(ensure_target(Remote(target), target), indent=2))


@app.command('stage')
def stage(name: str, artifact_id: str,
          inventory: Path = typer.Option(DEFAULT_INVENTORY),
          artifacts: Path = typer.Option(DEFAULT_ARTIFACTS)):
    target = get_target(name, inventory)
    dist, manifest = artifact(artifact_id, artifacts)
    if manifest.target != name:
        raise typer.BadParameter(f'artifact built for {manifest.target}, not {name}')
    remote = Remote(target)
    ensure_target(remote, target)
    with target_lock(name, DEFAULT_STATE):
        remote.stage(dist, artifact_id)
    typer.echo(f'Staged {artifact_id} on {name}; no boot files changed.')


@app.command('install')
def install(name: str, artifact_id: str,
            inventory: Path = typer.Option(DEFAULT_INVENTORY),
            artifacts: Path = typer.Option(DEFAULT_ARTIFACTS),
            confirm: bool = typer.Option(False, '--confirm', help='Confirm boot-file staging')):
    if not confirm:
        raise typer.BadParameter('must pass --confirm to install experimental kernel')
    target = get_target(name, inventory)
    if target.boot_mode != 'rpi-tryboot':
        raise typer.BadParameter('unsupported boot adapter; Ubuntu/manual targets intentionally refuse install')
    _, manifest = artifact(artifact_id, artifacts)
    if manifest.target != name:
        raise typer.BadParameter('artifact target mismatch')
    remote = Remote(target)
    ensure_target(remote, target)
    with target_lock(name, DEFAULT_STATE):
        typer.echo(json.dumps(remote.run('install-pios', artifact_id, timeout=300), indent=2))


@app.command('boot-try')
def boot_try(name: str, artifact_id: str, inventory: Path = typer.Option(DEFAULT_INVENTORY),
             confirm: bool = typer.Option(False, '--confirm')):
    if not confirm:
        raise typer.BadParameter('must pass --confirm to reboot a target')
    target = get_target(name, inventory)
    if target.boot_mode != 'rpi-tryboot':
        raise typer.BadParameter('boot-try requires rpi-tryboot target')
    with target_lock(name, DEFAULT_STATE):
        typer.echo(json.dumps(Remote(target).run('boot-try', artifact_id), indent=2))
    typer.echo('Reboot scheduled. Run verify manually after the target reconnects.')


@app.command('verify')
def verify(name: str, artifact_id: str,
           inventory: Path = typer.Option(DEFAULT_INVENTORY),
           artifacts: Path = typer.Option(DEFAULT_ARTIFACTS)):
    target = get_target(name, inventory)
    _, manifest = artifact(artifact_id, artifacts)
    if manifest.target != name:
        raise typer.BadParameter('artifact target mismatch')
    result = Remote(target).run('status')
    typer.echo(json.dumps(result, indent=2))
    if result['kernelrelease'] != manifest.kernelrelease:
        raise typer.Exit(code=2)
    typer.echo('Verified expected kernelrelease')


def run_benchmark(target_name: str, exp_id: str, spec, inventory: Path, state: Path,
                  expected_release: str | None):
    target = get_target(target_name, inventory)
    remote = Remote(target)
    ensure_target(remote, target)
    status = remote.run('status')
    if expected_release and status['kernelrelease'] != expected_release:
        raise RuntimeError(f'wrong kernel: expected {expected_release}, found {status["kernelrelease"]}')
    # Typed spec only, no user-provided shell command executed on target.
    with target_lock(target_name, state):
        result = remote.run('benchmark', exp_id, spec.model_dump_json(),
                            timeout=spec.duration_seconds * spec.repetitions + 180)
    result['target'] = target_name
    output = insert(state / 'kernel-lab.sqlite3', state / 'runs', result)
    typer.echo(f'Result: {output}')
    typer.echo(json.dumps({'success': result['successful'], 'measurements': result['measurements']}, indent=2))
    if not result['successful']:
        raise typer.Exit(code=3)


@app.command('benchmark')
def benchmark(name: str, artifact_id: str, experiment: Path,
              inventory: Path = typer.Option(DEFAULT_INVENTORY),
              artifacts: Path = typer.Option(DEFAULT_ARTIFACTS),
              state: Path = typer.Option(DEFAULT_STATE)):
    exp = load_experiment(experiment)
    if name != exp.target:
        raise typer.BadParameter('experiment target mismatch')
    _, manifest = artifact(artifact_id, artifacts)
    if manifest.target != name or manifest.experiment_name != exp.name:
        raise typer.BadParameter('experiment does not match manifest')
    run_benchmark(name, artifact_id, exp.benchmark, inventory, state, manifest.kernelrelease)


@app.command('benchmark-baseline')
def baseline(name: str, baseline_id: str, experiment: Path,
             inventory: Path = typer.Option(DEFAULT_INVENTORY),
             state: Path = typer.Option(DEFAULT_STATE)):
    exp = load_experiment(experiment)
    if name != exp.target:
        raise typer.BadParameter('experiment target mismatch')
    if not baseline_id.startswith('baseline-') or len(baseline_id) > 63:
        raise typer.BadParameter('baseline id must start baseline- and be <=63 chars')
    run_benchmark(name, baseline_id, exp.benchmark, inventory, state, None)


@app.command('compare')
def compare(first: str, second: str, state: Path = typer.Option(DEFAULT_STATE)):
    typer.echo(json.dumps(compare_results(state / 'kernel-lab.sqlite3', first, second), indent=2))


@app.command('power-cycle')
def power_cycle(name: str, inventory: Path = typer.Option(DEFAULT_INVENTORY),
                confirm: bool = typer.Option(False, '--confirm')):
    if not confirm:
        raise typer.BadParameter('must pass --confirm to power cycle a target')
    target = get_target(name, inventory)
    if not target.power_cycle_argv:
        raise typer.BadParameter('power_cycle_argv not configured for target')
    # This executes an explicitly operator-configured local program, not
    # arbitrary agent-provided commands. Ensure a physical fallback is tested.
    with target_lock(name, DEFAULT_STATE):
        subprocess.run(target.power_cycle_argv, check=True, timeout=120)
    typer.echo('Power-cycle command completed. Verify that target returned to known-good boot.')


@app.command('recover')
def recover(name: str, inventory: Path = typer.Option(DEFAULT_INVENTORY),
            confirm: bool = typer.Option(False, '--confirm')):
    if not confirm:
        raise typer.BadParameter('must pass --confirm for hardware rescue + power cycle')
    target = get_target(name, inventory)
    if not target.rescue_select_argv or not target.power_cycle_argv:
        raise typer.BadParameter('both rescue_select_argv and power_cycle_argv are required')
    with target_lock(name, DEFAULT_STATE):
        subprocess.run(target.rescue_select_argv, check=True, timeout=120)
        subprocess.run(target.power_cycle_argv, check=True, timeout=120)
    typer.echo('Rescue boot selected and power cycled. Verify rescue OS and restore only after diagnosis.')


if __name__ == '__main__':
    app()
