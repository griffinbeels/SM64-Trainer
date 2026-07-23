# tests/test_desktop_repair.py
from sm64_events.main import _bootstrap_cleanup_arg


def test_bootstrap_cleanup_arg_parses():
    assert _bootstrap_cleanup_arg(
        ["exe", "--cleanup-bootstrap", r"C:\x\SM64Trainer.exe"]) == \
        r"C:\x\SM64Trainer.exe"
    assert _bootstrap_cleanup_arg(["exe"]) is None
    assert _bootstrap_cleanup_arg(["exe", "--cleanup-bootstrap"]) is None
