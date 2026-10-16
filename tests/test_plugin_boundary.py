"""Plugins reach the harness only through Context's public methods."""
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def test_plugins_do_not_touch_private_harness_state():
    offenders = [f"{path.relative_to(PLUGINS)}:{number}"
                 for path in PLUGINS.rglob("*.py")
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if "._harness" in line]
    assert offenders == []
