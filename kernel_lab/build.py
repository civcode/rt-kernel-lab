"""Local Docker cross-build; never checks out or modifies kernel sources."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from .models import ArtifactManifest, Experiment, Target

ROOT = Path(__file__).resolve().parent.parent
IMAGE = 'kernel-lab-builder:0.1'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def execute(cmd: list[str], *, cwd: Path | None = None):
    subprocess.run(cmd, cwd=cwd, check=True)


def validate_experiment(exp: Experiment, target: Target):
    required = 'bcm2712_defconfig' if target.board == 'pi5' else 'bcm2711_defconfig'
    if exp.defconfig != required:
        raise ValueError(f'{target.board} requires {required} for the supported Raspberry Pi arm64 profile')
    src = exp.source_dir.expanduser().resolve(strict=True)
    commit = subprocess.check_output(['git', '-C', str(src), 'rev-parse', 'HEAD'], text=True).strip().lower()
    if commit != exp.source_commit:
        raise ValueError(f'source HEAD {commit} != pinned commit {exp.source_commit}')
    dirty = subprocess.check_output(['git', '-C', str(src), 'status', '--porcelain'], text=True).strip()
    if dirty:
        raise ValueError('source checkout is dirty; build from a clean pinned tree')
    fragment_paths = []
    for fragment in exp.fragments:
        path = (ROOT / fragment).resolve(strict=True)
        if not path.is_relative_to(ROOT):
            raise ValueError(f'fragment escapes repository: {fragment}')
        fragment_paths.append(path)
    return src, fragment_paths


def build(exp: Experiment, target: Target, artifacts_root: Path, jobs: int = 0) -> tuple[Path, ArtifactManifest]:
    src, fragments = validate_experiment(exp, target)
    image_id = subprocess.check_output(
        ['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE], text=True).strip()
    # Stable ID: config + pinned revision + exact toolchain image determine the artifact.
    key = {
        'commit': exp.source_commit, 'defconfig': exp.defconfig,
        'fragments': [(p.relative_to(ROOT).as_posix(), sha256(p)) for p in fragments],
        'docker_image_id': image_id,
    }
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]
    exp_id = f'{exp.name}-{digest}'
    localversion = f'-kl-{digest}'
    outdir = (artifacts_root / exp_id).resolve()
    if outdir.exists():
        raise FileExistsError(f'artifact already exists: {outdir}; never overwrite immutable artifacts')
    outdir.mkdir(parents=True)
    try:
        docker = [
            'docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
            '--volume', f'{src}:/src:ro',
            '--volume', f'{ROOT}:/repo:ro',
            '--volume', f'{outdir}:/out:rw',
            '--env', f'BUILD_JOBS={jobs or os.cpu_count() or 4}',
            IMAGE, 'bash', '/repo/docker/build.sh', exp.defconfig, localversion,
            *[f'/repo/{p.relative_to(ROOT).as_posix()}' for p in fragments],
        ]
        execute(docker)
        dist = outdir / 'dist'
        release = (dist / 'kernelrelease').read_text().strip()
        manifest = ArtifactManifest(
            experiment_id=exp_id, experiment_name=exp.name, target=exp.target,
            source_commit=exp.source_commit, defconfig=exp.defconfig,
            kernelrelease=release, localversion=localversion,
            image_sha256=sha256(dist / 'kernel.img'),
            modules_sha256=sha256(dist / 'modules.tar.gz'),
            config_sha256=sha256(dist / 'config'),
            dtbs_sha256=sha256(dist / 'dtbs.tar.gz'),
            docker_image=IMAGE, docker_image_id=image_id, built_at=datetime.now(timezone.utc),
            fragment_names=exp.fragments,
        )
        (dist / 'manifest.json').write_text(manifest.model_dump_json(indent=2) + '\n')
        return dist, manifest
    except BaseException:
        (outdir / 'FAILED').write_text('Build failed; inspect Docker output. This directory is intentionally retained.\n')
        raise
