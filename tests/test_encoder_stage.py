"""The encoder helper loads a private copy, so a new build can replace the shipped DLL."""
import ctypes
import sys

import pytest

from sm64_events.core.paths import ENCODER_DLL_NAME, bundled_encoder_dll
from sm64_events.replay.encoderstage import staged_encoder_dll


def test_the_copy_is_identical_reused_and_replaced_per_build(tmp_path):
    shipped = tmp_path / "plugin" / ENCODER_DLL_NAME
    shipped.parent.mkdir()
    shipped.write_bytes(b"build one")
    first = staged_encoder_dll(shipped, tmp_path / "scratch")
    assert first.is_absolute() and first.name == ENCODER_DLL_NAME and first != shipped
    assert first.read_bytes() == b"build one"
    assert staged_encoder_dll(shipped, tmp_path / "scratch") == first
    shipped.write_bytes(b"build two")
    second = staged_encoder_dll(shipped, tmp_path / "scratch")
    assert second != first and second.read_bytes() == b"build two"
    assert not first.exists(), "an unused older copy is removed"


@pytest.mark.skipif(sys.platform != "win32" or bundled_encoder_dll() is None, reason="Windows + a built helper")
def test_the_shipped_file_can_be_replaced_while_the_copy_is_loaded(tmp_path):
    shipped = tmp_path / "plugin" / ENCODER_DLL_NAME
    shipped.parent.mkdir()
    shipped.write_bytes(bundled_encoder_dll().read_bytes())
    loaded = ctypes.WinDLL(str(staged_encoder_dll(shipped, tmp_path / "scratch")), winmode=0x1100)
    try:
        shipped.write_bytes(b"a newer build")  # would fail with the shipped file mapped
        assert staged_encoder_dll(shipped, tmp_path / "scratch").read_bytes() == b"a newer build"
    finally:
        ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(loaded._handle))
