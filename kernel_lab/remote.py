"""Secure-ish SSH transport using system OpenSSH and pinned host keys."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess

from .models import Target

RUNNER = '/usr/local/libexec/kernel-lab-target'


class Remote:
    def __init__(self, target: Target):
        self.target = target
        self.endpoint = f'{target.user}@{target.host}'
        self.ssh_opts = [
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-p', str(target.ssh_port),
        ]

    def run(self, action: str, *args: str, timeout: int = 120) -> dict:
        command = shlex.join(['sudo', '-n', RUNNER, action, *args])
        completed = subprocess.run(
            ['ssh', *self.ssh_opts, self.endpoint, command],
            text=True, capture_output=True, timeout=timeout, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f'SSH action {action} failed ({completed.returncode}): {completed.stderr[-4000:]}')
        return json.loads(completed.stdout)

    def stage(self, dist: Path, exp_id: str):
        # Fixed incoming path, no arbitrary remote shell commands.
        files = ['kernel.img', 'modules.tar.gz', 'config', 'dtbs.tar.gz', 'manifest.json']
        missing = [f for f in files if not (dist / f).is_file()]
        if missing:
            raise FileNotFoundError(f'artifact missing files: {missing}')
        self.run('prepare', exp_id)
        cmd = [
            'rsync', '-rt', '--chmod=F600', '-e',
            'ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p ' + str(self.target.ssh_port),
            *[str(dist / filename) for filename in files],
            f'{self.endpoint}:/var/tmp/kernel-lab/incoming/{exp_id}/',
        ]
        subprocess.run(cmd, check=True)
