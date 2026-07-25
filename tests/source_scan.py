"""Shared helper for tests that assert things about SOURCE TEXT.

Import it as `from source_scan import strip_comments` — tests/ is not a
package, so pytest puts this directory itself on sys.path. (`from
tests.source_scan import ...` also resolves, via a sys.path append in
tests/conftest.py, because that is the form people write first and an
ImportError here aborts collection for the whole suite.)

A substring assertion cannot tell code from prose, and this repo has shipped
broken guards in BOTH directions:

* ``assert "Escape" in source`` stays green after the handler is deleted,
  because the component's header comment explains Escape by name. That exact
  guard was live in ``test_ui_entitymodal.py`` until 2026-07-25.
* ``assert "role=\\"grid\\"" not in source`` fails the moment a comment
  mentions the thing it forbids. Five separate guards were rewritten for this
  in one session (2026-07-25: ``WORLD_EDGES``, ``--depth``, ``.route-cat``,
  ``start_levels``, ``role="grid"``) — every time, the "failure" was the
  comment explaining WHY the code was absent.

So: strip the comments, then assert on what is left. And keep the guard
honest by probing it — express the check as a function of source text and
feed it a comment-only sample (must pass) and a real-code sample (must fail).
That probe used to be done by hand after every retarget, which is exactly the
kind of step that gets skipped.
"""
import re

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def strip_comments(source: str) -> str:
    """JS/CSS source with both comment styles removed."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))
