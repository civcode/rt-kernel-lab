import pytest
from kernel_lab.locks import target_lock


def test_target_lock_excludes_parallel_operations(tmp_path):
    with target_lock('pi3-os', tmp_path):
        with pytest.raises(RuntimeError):
            with target_lock('pi3-os', tmp_path):
                pass
    with target_lock('pi3-os', tmp_path):
        pass
