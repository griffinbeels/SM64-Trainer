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
import ast
import io
import re
import tokenize
from pathlib import Path

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def strip_comments(source: str) -> str:
    """JS/CSS source with both comment styles removed."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


# --- Python -----------------------------------------------------------------
# Same problem, other language: a `# comment` or a docstring explaining WHY a
# thing is absent must not satisfy (or trip) a guard about the code. Python
# docstrings are the reason a naive strip is not enough — core/relaunch.py and
# desktop/single_instance.py both explain the server port by number in their
# module docstrings, and a text scan reads those as hardcoded ports.
def python_code(source: str) -> str:
    """Python source with comments and docstrings removed, as loose tokens.

    Token-joined, not reconstructed: this is for substring assertions, never
    for execution. Non-docstring string LITERALS are kept on purpose — a
    hardcoded value inside one is exactly the kind of second source of truth
    these guards exist to find.
    """
    docstring_lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    kept = [token.string
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type != tokenize.COMMENT and token.start[0] not in docstring_lines]
    return "\n".join(kept)


def code_only(path: Path, source: str | None = None) -> str:
    """Comment-free source for a file, dispatched on its suffix."""
    text = source if source is not None else path.read_text(encoding="utf-8")
    return python_code(text) if path.suffix == ".py" else strip_comments(text)
