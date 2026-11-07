"""Single-controller per-target advisory file locks for manual workflows."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def target_lock(target: str, state: Path):
    directory = state / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f'{target}.lock').open('w') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f'{target} is already in use by another kernel-lab operation') from error
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)
