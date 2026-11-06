"""Which of two capture-layer builds is newer, now that builds are repeatable (core/plugin_ownership.py).

Since 2026-11-06 a build's PE TimeDateStamp is its source date, the time of
the newest commit touching its inputs (tools/native_identity.py), not the
moment of linking. A build from before carries its link time, which is
usually LATER than its commit, so a plain date comparison would refuse the
first repeatable build forever. A build that names itself in a version
resource ranks above one that does not; otherwise the dates decide.
"""
import struct
import sys
from pathlib import Path

import pytest

from sm64_events.core.plugin_ownership import link_time, names_itself, not_older

ROOT = Path(__file__).resolve().parents[1]


def pe(stamp: int, resource: bytes | None = None) -> bytes:
    """A PE image with a TimeDateStamp and, optionally, one .rsrc section holding `resource`."""
    head = bytearray(0x400)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHI", head, 0x84, 0x14C, 1 if resource is not None else 0, stamp)
    struct.pack_into("<H", head, 0x80 + 20, 0xE0)  # optional header size
    if resource is None:
        return bytes(head)
    entry = 0x80 + 24 + 0xE0
    head[entry:entry + 8] = b".rsrc\0\0\0"
    struct.pack_into("<II", head, entry + 16, len(resource), len(head))
    return bytes(head) + resource


OURS = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0" + "SM64 Trainer".encode("utf-16-le")
SOMEONE_ELSES = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0" + "Another Company".encode("utf-16-le")


@pytest.fixture
def write(tmp_path):
    def make(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return path
    return make


def test_a_build_names_itself_only_with_our_version_resource(write):
    assert names_itself(write("ours.dll", pe(1, OURS)))
    assert not names_itself(write("theirs.dll", pe(1, SOMEONE_ELSES)))
    assert not names_itself(write("bare.dll", pe(1)))
    assert not names_itself(write("text.dll", b"not a PE image"))


def test_the_first_repeatable_build_replaces_a_link_dated_one_linked_after_its_commit(write):
    linked_later = write("installed.dll", pe(1_794_100_000))           # built an hour after the commit
    source_dated = write("bundle.dll", pe(1_794_000_000, OURS))        # that commit's source date
    assert not_older(source_dated, linked_later)
    assert not not_older(linked_later, source_dated), "an older-style build never replaces a named one"


def test_between_two_repeatable_builds_the_source_date_decides(write):
    earlier, later = write("a.dll", pe(1_794_000_000, OURS)), write("b.dll", pe(1_794_500_000, OURS))
    assert not_older(later, earlier) and not not_older(earlier, later)
    assert not_older(earlier, write("same.dll", pe(1_794_000_000, OURS))), "the same commit is not older"


@pytest.mark.skipif(sys.platform != "win32", reason="needs the MSVC toolchain")
def test_a_real_build_names_itself_and_carries_its_source_date(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("order_build_plugin", ROOT / "tools" / "build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if build.find_vcvars32() is None:
        pytest.skip("needs the MSVC toolchain")
    wrapper = build.build_wrapper(tmp_path)
    assert names_itself(wrapper)
    assert link_time(wrapper) == build._build_term()[1]
