# Kernel Lab (manual-first prototype)

Build and benchmark pinned Linux ARM64 PREEMPT_RT kernels on Raspberry Pi 3/4/5 from an AMD64 Docker builder. Python is the single source of truth; the optional Pi agent currently provides **read-only** queries.

> **Status:** Tested locally for configuration validation, target-helper parsing and database handling. Docker kernel compilation, SSH, physical deployment, initramfs creation, tryboot and external PDU recovery must be validated on real hardware. The included Ubuntu targets support probing and benchmarks, **not** automatic kernel installation. This is a development scaffold, not a production unattended recovery system.

## Architecture

```text
AMD64 control host
  kernel-lab (Typer + Pydantic) -> immutable artifacts / SQLite
        |                      -> Docker aarch64-linux-gnu toolchain
        | SSH / rsync (pinned host keys)
        v
Raspberry Pi 3/4/5 (Raspberry Pi OS or Ubuntu)
  root-owned /usr/local/libexec/kernel-lab-target
        -> probe / stage / guarded Pi OS one-shot tryboot / cyclictest
  [out of band] switched PDU or PoE+ controller; optional UART / rescue media

Future: Pi coding agent -> read-only tools -> controlled experiment proposals
```

## Repository structure

```text
kernel_lab/                 Python models, controller CLI, Docker/SSH adapters, SQLite
configs/common/            PREEMPT_RT fragment
configs/scheduler/         Example HZ fragments
inventory/                 Example target host/distro/boot/recovery inventory
experiments/               Pinned source+fragment+benchmark YAML, offline Kconfig matrix plans
schemas/                   Generated JSON Schemas (Pydantic)
docker/Dockerfile           Ubuntu AMD64 image with cross compiler
docker/build.sh             Kernel O= build and module/DTB package generation
docker/check_config.py      Verify effective Kconfig settings exactly match fragments
target/kernel_lab_target.py Fixed, allowlisted privileged target runner
.pi/extensions/kernel-lab.ts  Optional read-only Pi agent extension
tests/                     Unit tests, no hardware or Docker required
```

## Requirements

- AMD64 Linux: Python 3.11+, Docker engine, OpenSSH client, rsync, Git, disk space for source and kernel objects.
- Target: 64-bit Linux, SSH, `python3`, `sudo`, `rsync`, `rt-tests` (`cyclictest`), `stress-ng`, `kmod` and `initramfs-tools` for Pi OS installation.
- SSH keys and verified host keys; a dedicated `lab` account; a separate known-good boot path; **externally controllable power** for hung kernels. Optional independent UART and rescue SD/USB boot hardware.
- `source_commit` is pinned to a **clean** local kernel checkout. The controller does not clone, switch or patch source trees.
- `configs/common/preempt-rt.cfg` assumes the pinned kernel tree supports `CONFIG_PREEMPT_RT`; any unresolved Kconfig setting fails the build.

### Set up the AMD64 host

```bash
cd kernel-lab-starter
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp inventory/targets.example.yaml inventory/targets.yaml
vi inventory/targets.yaml
vi experiments/pi5-rt.example.yaml
kernel-lab targets
kernel-lab matrix-plan experiments/matrix.example.yaml # generate YAML only, no builds
kernel-lab build-image
kernel-lab validate experiments/pi5-rt.example.yaml
kernel-lab build experiments/pi5-rt.example.yaml --jobs 12
```

Set `source_commit` to the exact 40-character `git rev-parse HEAD` from a clean source checkout. Source and configuration changes require a new experiment. `matrix-plan` expands validated Kconfig fragment choices into individual experiment manifests and rejects contradictory settings. It never executes builds or deployments. Generated artifacts are stored as `artifacts/<experiment-slug>-<config-hash>/dist/{kernel.img,modules.tar.gz,dtbs.tar.gz,config,kernelrelease,manifest.json}`. The manifest contains hashes and full source commit. Source path is mounted **read-only** in Docker. The artifact records Docker's image ID and the tag; pin the `FROM` base image by digest for stricter rebuild reproducibility.

### Install target helper (run once on each Pi)

Install as **root-owned**; do not execute the helper directly from a user-writable staging directory with sudo.

```bash
# On AMD64 host, after provisioning target and SSH user:
scp target/kernel_lab_target.py lab@pi3:/tmp/kernel_lab_target.py
ssh lab@pi3 'sudo install -o root -g root -m 0755 /tmp/kernel_lab_target.py /usr/local/libexec/kernel-lab-target'
ssh lab@pi3 'sudo apt-get install rt-tests stress-ng rsync python3 kmod initramfs-tools'
ssh lab@pi3 'sudo visudo -f /etc/sudoers.d/kernel-lab'
```

Put this **restricted command** in the sudoers file, adjusting account name:

```text
lab ALL=(root) NOPASSWD: /usr/local/libexec/kernel-lab-target
```

This helper accepts only `probe`, `prepare`, `install-pios`, `boot-try`, `status`, and `benchmark` and validates arguments. For tighter hardening, use a root-owned wrapper/service with action-specific authorization and dedicated service accounts. Confirm permissions (`root:root`, not writable by the SSH user), network isolation, and SSH key restrictions before deploying.

### Probe and benchmark existing stock kernels

```bash
kernel-lab probe pi3-os
kernel-lab probe pi4-ubuntu
kernel-lab benchmark-baseline pi3-os baseline-pi3-stock experiments/pi3-rt.example.yaml
```

`benchmark-baseline` uses the benchmark profile from the YAML but does not require the running kernel to match a compiled artifact. Copy the supplied Pi5 experiment and update target, defconfig and source revision for Pi3/Pi4 before invoking it.

### Raspberry Pi OS staged installation (requires provisioning)

First verify the existing boot filesystem, effective config sections, tryboot support, initramfs and all necessary drivers on your board. Confirm power-cycle fallback to stock boot **with a deliberately non-functional test kernel on a sacrificial SD card**. Only then create the target opt-in marker:

```bash
ssh lab@pi3 'sudo install -d -m 0755 /etc/kernel-lab && sudo touch /etc/kernel-lab/allow-rpi-tryboot'
```

**Important:** The provided helper stages the kernel, new versioned modules and initramfs, and writes `tryboot.txt` without replacing normal `config.txt`. It does not install new DTBs/overlays. Choose compatible kernel/firmware/device trees; for experiments requiring different DTBs or overlays, implement and test a full alternate boot slot first.

```bash
# Replace ID with the build's reported artifact ID.
kernel-lab stage pi3-os pi3-rt-example-0123456789ab
kernel-lab install pi3-os pi3-rt-example-0123456789ab --confirm
kernel-lab boot-try pi3-os pi3-rt-example-0123456789ab --confirm
# Execute after the Pi is reachable again:
kernel-lab verify pi3-os pi3-rt-example-0123456789ab
kernel-lab benchmark pi3-os pi3-rt-example-0123456789ab experiments/pi3-rt.example.yaml
```

If the candidate fails to boot, use the **physically independent** PDU/PoE path to reset. Tryboot is one shot and normally falls back to known-good boot after another reset; if a target's firmware/configuration is wrong, automatic fallback is not guaranteed. Power control by itself cannot repair boot media. Keep independent rescue media or a boot slot selector available. Do not run the same target's kernel experiments concurrently. The starter intentionally does **not** overwrite pre-existing `tryboot.txt` and it leaves staged assets and pending records for manual diagnosis/cleanup (it has no automatic garbage collector yet).

### Mixed Ubuntu support

Ubuntu on Raspberry Pi typically uses `/boot/firmware/config.txt`, with `vmlinuz`, `initrd.img`, and boot-managed assets. Starting with **Ubuntu 25.10**, the system has a managed `piboot-try` A/B boot workflow using `current/`, `new/` and `old/` assets. Ubuntu can *automatically promote* a candidate at `multi-user.target` if the validation script passes. This is not equivalent to Pi OS one-shot tryboot. **No Ubuntu boot installer is included in v0.1.**

Before enabling automated Ubuntu installs, build and validate a separate `UbuntuFlashKernelAdapter` for your exact release that packages the custom kernel and modules, integrates with flash-kernel, generates a matching initramfs, manages the validation gate, and guarantees a known-good previous boot. On Ubuntu 24.04 and earlier, use a separately provisioned A/B boot strategy or manual boot activation with physical rescue. Probe and benchmark commands already support Ubuntu.

### External power recovery

Put a **static** approved command array in each inventory target:

```yaml
power_cycle_argv:
  - /usr/local/sbin/lab-pdu
  - cycle
  - pi3-outlet
```

Then invoke only after verifying that the PDU command affects the expected board:

```bash
kernel-lab power-cycle pi3-os --confirm
```

This runs on AMD64, not over SSH, and is never dispatched automatically by the Pi extension. A power cycle only resets the Pi; proper fallback depends on boot-slot/tryboot configuration. For a hardware SD/USB-mux or independently switchable rescue slot, configure an additional `rescue_select_argv` with an approved local command, then run `kernel-lab recover pi3-os --confirm`. This selects rescue media *before* power cycling. Ensure neither command relies on the failed Pi being reachable, and test both devices manually. The controller uses local advisory locks so it cannot run its own parallel operations against the same target; an independent orchestrator still needs distributed locking.

### Compare and later agent use

```bash
kernel-lab compare baseline-pi3-stock pi3-rt-example-0123456789ab
pytest -q
# Optional: Pi discovers project-local .pi/extensions/kernel-lab.ts in a trusted project.
```

Pi extension exposes read-only `kernel_lab_targets` and `kernel_lab_compare`. It does **not** expose SSH, kernel installation, reboot, or PDU control. Add authorizing tools only after manual installation and recovery have been exercised repeatedly.

## Data and benchmark contracts

- `schemas/experiment.schema.json`: pinned kernel source, Kconfig fragments, target and `BenchmarkSpec`.
- `schemas/inventory.schema.json`: mixed-distro inventory, board model, SSH and static PDU/rescue argv.
- `schemas/artifact.schema.json`: compiled artifact metadata and SHA-256 checksums.
- `schemas/benchmark-result.schema.json`: timestamped run, actual `uname`, profile, environment, per-repetition latency summaries and full command output.
- Initial `cyclictest` runner uses one scheduling thread (`-t1`) without explicit CPU affinity; it is not a complete per-core, IRQ-isolation or maximum-load validation yet.
- SQLite: `runs` table stores trial identity, target, kernel version, success, worst observed latency and JSON path. No histogram percentiles are **invented**: p99 and p99.9 remain null until histogram or sample-data integration is added.

Capture `cyclictest` stdout/stderr on each repetition, kernel cmdline, cpufreq governors, Pi temperature/throttling where available, and distro information. Compare distributions under identical priorities, duration, CPU governor, cooling, load level, IRQ affinity, firmware, and board revision. For high assurance, extend the runner to capture frequency traces, fine-grained timerlat/osnoise output, histogram percentiles, and test-run start/stop temperature.

## Boundaries and roadmap

**Implemented scaffold:** strict models, matrix generation, Kconfig verification, cross-build packaging, SSH/probe/stage, opt-in Pi OS one-shot deployment, target benchmarking, SQLite index, baseline comparison, PDU command hook, read-only Pi extension and unit tests.

**Not implemented:** Ubuntu kernel package/flash-kernel adapter, end-to-end automated offline rollback, A/B rootfs management, automated source fetching and RT patch resolution, automatic test farm scheduler, dashboards, advanced histogram analysis, distributed concurrency locks, agent write tools, cryptographic artifact signing, or a complete target hardening system.

These are deliberate gates, not capabilities to assume on real devices. Run first trials on removable media or sacrificial targets, and verify the fallback behavior before using this with valuable systems.

## References

- [Raspberry Pi kernel build guide](https://www.raspberrypi.com/documentation/computers/linux_kernel.html)
- [Raspberry Pi firmware tryboot / autoboot](https://www.raspberrypi.com/documentation/computers/config_txt.html)
- [Ubuntu Raspberry Pi A/B boot](https://ubuntu.com/hardware/docs/boards/explanations/piboot-ab/)
- [Ubuntu Raspberry Pi config.txt](https://ubuntu.com/hardware/docs/boards/how-to/special_hardware/rpi-config-txt/)
- [Pi coding agent extensions](https://pi.dev/docs/latest/extensions)
- [cyclictest options](https://manpages.debian.org/unstable/rt-tests/cyclictest.8.en.html)
