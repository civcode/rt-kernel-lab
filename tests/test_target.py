from pathlib import Path
import importlib.util
import io
import tarfile
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'target/kernel_lab_target.py'
spec = importlib.util.spec_from_file_location('kernel_lab_target', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def pack(path, file, content=b'data', symlink=False):
    with tarfile.open(path, 'w:gz') as tar:
        info = tarfile.TarInfo(file)
        if symlink:
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc/shadow'
            tar.addfile(info)
        else:
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def test_safe_modules(tmp_path):
    release = '6.12.0-kl-123456789abc'
    tar = tmp_path / 'modules.tgz'
    pack(tar, f'lib/modules/{release}/kernel/foo.ko')
    mod.safe_extract_modules(tar, tmp_path / 'out', release)
    assert (tmp_path / 'out/lib/modules' / release / 'kernel/foo.ko').read_bytes() == b'data'


def test_reject_module_symlinks_and_escape(tmp_path):
    release = '6.12.0-kl-123456789abc'
    tar = tmp_path / 'modules.tgz'
    pack(tar, f'lib/modules/{release}/link', symlink=True)
    with pytest.raises(SystemExit):
        mod.safe_extract_modules(tar, tmp_path / 'out', release)
    pack(tar, '../../etc/shadow')
    with pytest.raises(SystemExit):
        mod.safe_extract_modules(tar, tmp_path / 'out', release)


def test_parse_cyclictest_summary():
    text = 'T: 0 ( 233) P:80 I:1000 C: 60000 Min: 4 Act: 8 Avg: 9 Max: 38'
    assert tuple(map(float, mod.MEASURE.search(text).groups())) == (4.0, 9.0, 38.0)
