"""The ghost asks indexed enables the way the OpenGL spec defines, not the way NVIDIA allows.

BLEND and SCISSOR_TEST are indexed enables: the spec's state tables give
IsEnabledi as their query. glGetBooleani_v of them is INVALID_ENUM on a strict
driver. NVIDIA accepts it, so every witness on the development machine passed,
while an AMD tester's renderer recorded GL_INVALID_ENUM (1280) on the ghost's
first scope and the ghost stepped aside for the whole session (2026-10-31).

Every witness here runs on NVIDIA, so it cannot see this class of error. This
scan is the guard: GetBooleani_v is kept for COLOR_WRITEMASK, the one indexed
boolean the ghost reads that the spec defines for it.
"""
import re
from pathlib import Path

from source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [*sorted((ROOT / "plugin/gfxwrap").glob("*.[ch]*")), *sorted((ROOT / "tests/native").glob("*.[ch]*"))]
CALL = re.compile(r"\bGetBooleani_v\s*\(\s*([\w:]+)")
ALLOWED = {"COLOR_WRITEMASK"}


def strict_driver_refusals(source: str) -> list[str]:
    return [name for name in CALL.findall(source) if name.rsplit("::", 1)[-1].removeprefix("GL_") not in ALLOWED]


def test_indexed_enables_are_asked_with_is_enabledi():
    found = {path.relative_to(ROOT).as_posix(): refused for path in SOURCES
             if (refused := strict_driver_refusals(code_only(path)))}
    assert not found, f"GetBooleani_v of a target a strict driver refuses (use IsEnabledi): {found}"


def test_the_guards_can_still_fail():
    assert strict_driver_refusals("g.GetBooleani_v(BLEND,0,&on);") == ["BLEND"]
    assert strict_driver_refusals("g.GetBooleani_v(cgl::SCISSOR_TEST,0,&on);") == ["cgl::SCISSOR_TEST"]
    assert strict_driver_refusals("g.GetBooleani_v(cap,0,&value);") == ["cap"]
    assert strict_driver_refusals("g.GetBooleani_v(GL_COLOR_WRITEMASK,1,mask);") == []
    assert strict_driver_refusals(code_only(Path("x.cpp"), "// GetBooleani_v(BLEND) is refused\n")) == []
