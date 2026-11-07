from pathlib import Path
from kernel_lab.matrix import expand
from kernel_lab.config import load_experiment


def test_matrix_expands_without_executing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    files = expand(root / 'experiments/matrix.example.yaml', tmp_path)
    assert len(files) == 2
    assert {load_experiment(x).name for x in files} == {'pi3-rt-hz250', 'pi3-rt-hz1000'}
