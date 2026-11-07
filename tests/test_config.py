from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).resolve().parents[1] / 'docker/check_config.py'
spec = importlib.util.spec_from_file_location('check_config', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_kconfig_checks(tmp_path):
    config = tmp_path / '.config'
    frag = tmp_path / 'rt.cfg'
    config.write_text('CONFIG_PREEMPT_RT=y\n# CONFIG_SOMETHING is not set\n')
    frag.write_text('CONFIG_PREEMPT_RT=y\n# CONFIG_SOMETHING is not set\n')
    assert mod.check(config, [frag])
    config.write_text('CONFIG_PREEMPT_RT=n\nCONFIG_SOMETHING=y\n')
    assert not mod.check(config, [frag])
