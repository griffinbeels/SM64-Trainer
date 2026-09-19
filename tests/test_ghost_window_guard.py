"""Nothing in the overlay may modify Project64's window. Read off the source.

He turned the ghost on once and Project64's own window came back wrong --
desktop wallpaper showing through the strip beside its FPS counter. The only
call that had ever named the emulator's window as the thing being CHANGED was
the one that made PJ64 the owner of our window, across a process boundary and
across a DPI-awareness boundary. It is gone.

A comment saying so would not survive the next person who wants "always above
its owner" for free, so this reads the package instead: which Win32 writers it
may name at all, and that not one of them is ever handed the game's handle as
the window being modified -- nor as the z-order anchor, since inserting AFTER
Project64 is what puts the ghost UNDERNEATH it.
"""
import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ghost" / "window"

#: Names this package may never USE -- as a call, as a constant, or as a string
#: handed to getattr. Each one either re-parents a window or reaches into
#: another application's. Prose explaining why they are gone is fine, which is
#: why this is checked against identifiers rather than against raw text.
FORBIDDEN = frozenset({
    "set_owner", "GWLP_HWNDPARENT", "SetParent", "SetWindowLongPtrW",
    "SetForegroundWindow", "BringWindowToTop", "AttachThreadInput",
    "SwitchToThisWindow", "MoveWindow", "SetWindowPlacement",
})

#: Anything that looks like a Win32 call that CHANGES a window.
WRITER_PATTERN = re.compile(
    r"(Set|Show|Move|Bring|Destroy|Post|Send|Attach|Switch|Close)[A-Z][A-Za-z]+")

#: The complete list of them this package is allowed to name. Adding a row
#: here is the deliberate act; forgetting to is the accident this catches.
APPROVED_WRITERS = frozenset({
    "SetWindowLongW", "SetWindowPos", "SetLayeredWindowAttributes",
    "ShowWindow", "SetProcessDpiAwarenessContext",
    "SetThreadDpiAwarenessContext",
    # Not a window call: it asks Windows to TELL us when the stacking order
    # moved, out of context and with no DLL loaded anywhere. It is listed
    # because the pattern below cannot tell a Set* apart by shape.
    "SetWinEventHook",
    # Not a window call at all: it closes the process snapshot and the process
    # handles `controller.py` uses to end the overlay's own process tree. It
    # is listed because the pattern below cannot tell a Close* apart by shape,
    # and a row here is meant to be a deliberate act.
    "CloseHandle",
})

#: Our own wrappers that change a window, plus the raw calls behind them. The
#: first argument of each is the window being modified.
MODIFIERS = frozenset({
    "SetWindowLongW", "SetWindowPos", "SetLayeredWindowAttributes", "ShowWindow",
    "apply_overlay_styles", "set_alpha", "place", "show_no_activate", "hide",
})

#: How the emulator's handle is spelled anywhere in this repo. A modifier
#: handed any of these is the defect.
GAME_SPELLINGS = ("game", "pj64", "project64", "emulator", "owner", "watcher")


def modules():
    return sorted(path for path in PACKAGE.glob("*.py"))


def parsed(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def names(tree: ast.AST) -> set[str]:
    """Every identifier and short string literal in a module.

    Strings count because `getattr(user32, "SetParent")` is a call by another
    spelling; a docstring is far too long to be mistaken for one, which is
    what keeps an explanation of the defect from tripping the guard.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) < 48:
                out.add(node.value)
    return out


def modifier_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in MODIFIERS:
            yield name, node


def game_spelled(node: ast.AST) -> bool:
    text = ast.unparse(node).lower()
    return any(spelling in text for spelling in GAME_SPELLINGS)


def test_the_guard_is_actually_reading_the_overlay_package():
    """A scan over nothing passes forever. This is the guard's own guard."""
    found = {path.name for path in modules()}
    assert {"native.py", "placer.py", "child.py", "style.py"} <= found, found
    assert any(modifier_calls(parsed(PACKAGE / "native.py"))), (
        "the scan found no window writers at all, so it is proving nothing")


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.name)
def test_no_module_uses_a_call_that_reaches_into_another_window(path):
    used = names(parsed(path)) & FORBIDDEN
    assert not used, (
        f"{path.name} uses {sorted(used)}; the overlay may not touch "
        "Project64's window relationships")


def test_the_package_names_only_the_window_writers_it_is_allowed_to():
    named: set[str] = set()
    for path in modules():
        named |= {name for name in names(parsed(path))
                  if WRITER_PATTERN.fullmatch(name)}
    assert named <= APPROVED_WRITERS, (
        f"new Win32 writers in ghost/window/: {sorted(named - APPROVED_WRITERS)}")


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.name)
def test_no_window_writer_is_handed_the_emulators_handle(path):
    for name, call in modifier_calls(parsed(path)):
        if not call.args:
            continue
        assert not game_spelled(call.args[0]), (
            f"{path.name}: {name}() is being asked to modify "
            f"{ast.unparse(call.args[0])} -- that is the emulator's window")


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.name)
def test_the_ghost_is_never_inserted_underneath_the_emulator(path):
    """SetWindowPos puts the moved window BELOW the handle it is given, so the
    z-order anchor must be the window ABOVE Project64, never Project64."""
    for name, call in modifier_calls(parsed(path)):
        anchors = [word.value for word in call.keywords
                   if word.arg == "insert_after"]
        if name in ("place", "SetWindowPos") and len(call.args) > 1:
            anchors.append(call.args[1])
        for anchor in anchors:
            assert not game_spelled(anchor), (
                f"{path.name}: {name}() anchors the ghost to "
                f"{ast.unparse(anchor)}, which puts it under the emulator")


# --- never over anybody else's app ------------------------------------------

#: `SetWindowPos`'s z-order band handles. Handing one of these to the overlay
#: would float it over every other application, which is the thing that makes
#: "stay up while he uses another app" safe to ship WITHOUT it.
TOPMOST_ANCHORS = frozenset({-1, -2})


def topmost_named(tree: ast.AST) -> set[str]:
    """Every mention of a topmost bit that is NOT a clearing one.

    `overlay_ex_style` has to name `WS_EX_TOPMOST` in order to strip it, so the
    guard asks how it is used rather than whether it appears: inside a `~`, it
    is being removed.
    """
    cleared = {id(node.operand) for node in ast.walk(tree)
               if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert)}
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            # Defining the constant is not asking for it; `style.py` has to
            # hold the number in order for anything to clear it.
            name = None if isinstance(node.ctx, ast.Store) else node.id
        else:
            name = node.attr if isinstance(node, ast.Attribute) else None
        if name in ("WS_EX_TOPMOST", "HWND_TOPMOST") and id(node) not in cleared:
            out.add(name)
    return out


def test_the_overlay_never_asks_to_float_over_other_applications():
    """Item 4's safety argument, read off the source. The ghost stays up while
    he is in another app ONLY because an app that covers Project64 covers it
    too; one topmost bit anywhere would turn that into a window over his
    browser."""
    from sm64_events.ghost.window import style

    assert style.OVERLAY_EX_STYLE & style.WS_EX_TOPMOST == 0
    assert style.overlay_ex_style(style.WS_EX_TOPMOST) & style.WS_EX_TOPMOST == 0
    guilty = {path.name: sorted(found) for path in modules()
              if (found := topmost_named(parsed(path)))}
    assert not guilty, f"the overlay asks to be topmost: {guilty}"


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.name)
def test_the_ghost_is_never_inserted_into_the_topmost_band(path):
    """`SetWindowPos(hwnd, HWND_TOPMOST, ...)` is spelled -1, and a bare -1
    would sail past a guard that only looks at names."""
    for name, call in modifier_calls(parsed(path)):
        anchors = [word.value for word in call.keywords
                   if word.arg == "insert_after"]
        if name in ("place", "SetWindowPos") and len(call.args) > 1:
            anchors.append(call.args[1])
        for anchor in anchors:
            literal = anchor.operand.value if (
                isinstance(anchor, ast.UnaryOp)
                and isinstance(anchor.op, ast.USub)
                and isinstance(anchor.operand, ast.Constant)) else None
            assert literal is None or -literal not in TOPMOST_ANCHORS, (
                f"{path.name}: {name}() puts the ghost in the topmost band")


def test_the_topmost_guards_would_notice_each_shape_coming_back():
    asked = parsed_source("ex = base | style.WS_EX_TOPMOST")
    assert topmost_named(asked) == {"WS_EX_TOPMOST"}
    assert topmost_named(parsed_source("ex = base & ~style.WS_EX_TOPMOST")) == set()

    floated = parsed_source("native.place(hwnd, rect, insert_after=-1)")
    anchors = [word.value for _n, call in modifier_calls(floated)
               for word in call.keywords if word.arg == "insert_after"]
    assert anchors and -anchors[0].operand.value in TOPMOST_ANCHORS


def test_the_guard_would_notice_each_defect_coming_back():
    """The mutations, as a unit: the exact shapes this file exists to reject.

    Written out here so the guard's teeth do not depend on somebody remembering
    to re-break the package by hand.
    """
    owning = parsed_source("native.set_alpha(game_hwnd, 128)")
    assert any(game_spelled(call.args[0]) for _n, call in modifier_calls(owning))

    under = parsed_source("native.place(hwnd, rect, insert_after=pj64_hwnd)")
    anchors = [word.value for _n, call in modifier_calls(under)
               for word in call.keywords if word.arg == "insert_after"]
    assert anchors and game_spelled(anchors[0])

    reparent = parsed_source(
        'setter = getattr(user32, "SetWindowLongPtrW")\n'
        "setter(hwnd, style.GWLP_HWNDPARENT, owner)\n")
    assert names(reparent) & FORBIDDEN == {"SetWindowLongPtrW", "GWLP_HWNDPARENT"}
    assert not {"SetWindowLongPtrW"} <= APPROVED_WRITERS


def parsed_source(source: str) -> ast.AST:
    return ast.parse(source)
