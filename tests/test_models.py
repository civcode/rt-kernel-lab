import pytest
from pydantic import ValidationError
from kernel_lab.models import Target, Experiment, BenchmarkSpec


def test_mixed_distro_policy():
    with pytest.raises(ValidationError):
        Target(host='10.0.0.1', user='lab', board='pi5', distro='ubuntu', boot_mode='rpi-tryboot')
    t = Target(host='10.0.0.1', user='lab', board='pi4', distro='ubuntu', boot_mode='manual')
    assert t.boot_mode == 'manual'


def test_no_arbitrary_fragment_path():
    with pytest.raises(ValidationError):
        Experiment(name='test-exp', target='pi3-os', source_dir='/tmp/linux',
                   source_commit='a' * 40, defconfig='bcm2711_defconfig',
                   fragments=['../../etc/shadow'])


def test_benchmark_bounds():
    with pytest.raises(ValidationError):
        BenchmarkSpec(duration_seconds=0)
    with pytest.raises(ValidationError):
        BenchmarkSpec(load='sudo rm -rf /')
