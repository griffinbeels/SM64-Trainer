"""server/tuning_api.py — SAVE writes the inspector's values into the repo.

An endpoint that rewrites a source file earns a corpus rather than a smoke
test. What is asserted here is mostly what it REFUSES: the failure that would
matter is not a wrong number, it is a request that reaches past the one field
it is allowed to touch.
"""
import re

import pytest
from fastapi import HTTPException

from sm64_events.server.tuning_api import TUNING_JS, rewrite_defaults


@pytest.fixture()
def source() -> str:
    return TUNING_JS.read_text(encoding="utf-8")


def value_of(source: str, key: str) -> str:
    match = re.search(r"\n  " + key + r":\s*\{[^{}]*?\bvalue:\s*([^,\n]+)", source)
    assert match, f"{key} has no value: field"
    return match.group(1).strip()


def test_a_number_lands_and_nothing_else_moves(source):
    updated, written = rewrite_defaults(source, {"ladderStepMs": 275})
    assert written == {"ladderStepMs": 275}
    assert value_of(updated, "ladderStepMs") == "275"
    # Every OTHER row is byte-identical: a regex that matched too much would
    # show up here and nowhere else.
    for key in ("barSweepFullMs", "tierDwellMs", "settleMs", "skipStyle"):
        assert value_of(updated, key) == value_of(source, key)
    assert len(updated.splitlines()) == len(source.splitlines())


def test_a_choice_lands_as_a_quoted_string(source):
    # Flip to whichever it is NOT: the registry's values belong to the user
    # (the inspector saves into it), so a test naming one of them is a test
    # that goes red the first time he picks the other.
    other = "pop" if value_of(source, "skipStyle") == '"chain"' else "chain"
    updated, written = rewrite_defaults(source, {"skipStyle": other})
    assert written == {"skipStyle": other}
    assert value_of(updated, "skipStyle") == f'"{other}"'


def test_writing_the_value_it_already_has_reports_nothing_written(source):
    current = float(value_of(source, "settleMs"))
    _updated, written = rewrite_defaults(source, {"settleMs": current})
    assert written == {}, "a no-op save must not claim it changed something"


def test_a_value_outside_the_rows_own_range_is_refused(source):
    with pytest.raises(ValueError, match="between"):
        rewrite_defaults(source, {"anticipateShare": 5})
    with pytest.raises(ValueError, match="between"):
        rewrite_defaults(source, {"ladderStepMs": -1})


def test_a_choice_outside_its_own_options_is_refused(source):
    with pytest.raises(ValueError, match="must be one of"):
        rewrite_defaults(source, {"skipStyle": "teleport"})


def test_a_number_sent_to_a_choice_and_a_string_sent_to_a_number(source):
    with pytest.raises(ValueError):
        rewrite_defaults(source, {"skipStyle": 3})
    with pytest.raises(ValueError, match="takes a number"):
        rewrite_defaults(source, {"ladderStepMs": "fast"})


def test_an_unknown_key_cannot_ADD_a_row(source):
    with pytest.raises(ValueError, match="matched 0 rows"):
        rewrite_defaults(source, {"somethingInvented": 1})
    assert "somethingInvented" not in source


@pytest.mark.parametrize("key", [
    "value", "group", "label", "why", "min", "max",       # sibling fields
    "ladderStepMs: 1}, evil", "__proto__", "a.b", "a/b", "",
])
def test_a_key_that_is_not_a_plain_tunable_name_is_refused(source, key):
    """The one that matters: `value` and `min` ARE real identifiers that appear
    all over the file, so a key check that only asked 'does this appear' would
    happily rewrite the wrong field in every row at once."""
    with pytest.raises(ValueError):
        rewrite_defaults(source, {key: 1})


def test_the_endpoint_refuses_to_write_from_the_packaged_app(monkeypatch):
    """A frozen exe is somebody's installed app; it has no repo to write to,
    and a tuning tool silently doing nothing there is worse than one that says
    so."""
    from sm64_events.server import tuning_api
    monkeypatch.setattr(tuning_api, "is_frozen", lambda: True)
    router = tuning_api.create_tuning_router()
    endpoint = router.routes[0].endpoint
    with pytest.raises(HTTPException) as raised:
        endpoint("climb", tuning_api.TuningBody(values={"ladderStepMs": 300}))
    assert raised.value.status_code == 409
    assert "packaged app" in raised.value.detail


def test_the_registry_file_still_parses_after_a_write(source, tmp_path):
    """The rewrite is a regex over JavaScript, so the only honest check is to
    run the result. node --check would accept a file whose VALUES became
    nonsense, so this imports it and reads the value back out."""
    import json
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    updated, _written = rewrite_defaults(
        source, {"ladderStepMs": 275, "anticipateShare": 0.42, "skipStyle": "chain"})
    written_file = tmp_path / "climbtuning.js"
    written_file.write_text(updated, encoding="utf-8", newline="")
    result = subprocess.run(
        ["node", "--input-type=module", "-"],
        input=f"import {{ DEFAULTS }} from {written_file.as_uri()!r};\n"
              "console.log(JSON.stringify([DEFAULTS.ladderStepMs,"
              " DEFAULTS.anticipateShare, DEFAULTS.skipStyle]));",
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [275, 0.42, "chain"]
