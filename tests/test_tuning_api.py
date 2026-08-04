"""server/tuning_api.py — SAVE writes the inspector's values into the repo.

An endpoint that rewrites a source file earns a corpus rather than a smoke
test. What is asserted here is mostly what it REFUSES: the failure that would
matter is not a wrong number, it is a request that reaches past the one field
it is allowed to touch.
"""
import json
import re
import shutil
import subprocess

import pytest
from fastapi import HTTPException

from sm64_events.server.tuning_api import (
    CSS_FALLBACK_TARGETS, TUNING_JS, TUNING_REGISTRIES, css_var_name,
    create_tuning_router, rewrite_defaults, sync_css_fallbacks)


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


def test_a_failed_save_names_the_file_it_was_actually_writing():
    """The error goes straight onto the inspector's screen, so it has to send
    the reader to the right file.

    It hardcoded "climbtuning.js" -- correct while that was the only surface,
    and a lie the moment a second registry was added: a failed save from the
    overall rank-up inspector pointed at a file it had never touched (live
    report, 2026-07-28)."""
    from sm64_events.server.tuning_api import rewrite_defaults

    with pytest.raises(ValueError) as caught:
        rewrite_defaults("  holdMs: {\n    value: 1,\n  },\n",
                         {"goneAway": 5}, where="marelotuning.js")
    assert "marelotuning.js" in str(caught.value)
    assert "climbtuning.js" not in str(caught.value)


# ---- sync_css_fallbacks — the log surface's second door --------------------
#
# `rewrite_defaults` above only ever touches ONE file (the registry's own
# `value:` fields). The log surface ships a SECOND appearance of every
# numeric default -- `var(--log-x, <fallback>)` in index.html, read by CSS
# before any JS has run -- and until 2026-08-03 SAVE moved only the first one.
# These tests work against small, hand-built registry/CSS text rather than
# the real files, the same reason `rewrite_defaults`'s own tests above do: the
# behaviour under test is the REGEX, not any one file's current contents.

REGISTRY_SAMPLE = (
    "\n  fooSize: {\n"
    "    group: \"Test\", label: \"Foo\", value: 20,\n"
    "    min: 1, max: 40, step: 1, unit: \"px\",\n"
    "    why: \"a size\",\n"
    "  },\n"
    "  ratio: {\n"
    "    group: \"Test\", label: \"Ratio\", value: 0.5,\n"
    "    min: 0, max: 1, step: 0.1, unit: \"x\",\n"
    "    why: \"a fraction\",\n"
    "  },\n")


def test_a_numeric_fallback_moves_with_its_value():
    css = ".foo { width: var(--log-foo-size, 13px); }\n"
    updated, written_css = sync_css_fallbacks(
        css, REGISTRY_SAMPLE, "log", {"fooSize": 20}, where="index.html")
    assert written_css == {"fooSize": "20px"}
    assert "var(--log-foo-size, 20px)" in updated
    assert "13px" not in updated


def test_an_x_unit_value_is_written_bare_no_px_suffix():
    css = ".bar { opacity: var(--log-ratio, 0.2); }\n"
    updated, written_css = sync_css_fallbacks(
        css, REGISTRY_SAMPLE, "log", {"ratio": 0.5}, where="index.html")
    assert written_css == {"ratio": "0.5"}
    assert "var(--log-ratio, 0.5)" in updated


def test_a_choice_value_has_no_var_to_sync_and_is_skipped():
    css = ".foo { width: var(--log-foo-size, 13px); }\n"
    updated, written_css = sync_css_fallbacks(
        css, REGISTRY_SAMPLE, "log", {"rankStyle": "chips"}, where="index.html")
    assert written_css == {}
    assert updated == css


def test_every_occurrence_of_the_same_var_moves_together():
    css = (".a { width: var(--log-foo-size, 13px); }\n"
           ".b { min-width: var(--log-foo-size, 13px); }\n")
    updated, written_css = sync_css_fallbacks(
        css, REGISTRY_SAMPLE, "log", {"fooSize": 20}, where="index.html")
    assert written_css == {"fooSize": "20px"}
    assert updated.count("var(--log-foo-size, 20px)") == 2


def test_a_value_already_matching_the_fallback_reports_nothing_written():
    css = ".foo { width: var(--log-foo-size, 20px); }\n"
    updated, written_css = sync_css_fallbacks(
        css, REGISTRY_SAMPLE, "log", {"fooSize": 20}, where="index.html")
    assert written_css == {}
    assert updated == css


def test_a_key_with_no_fallback_anywhere_is_refused():
    """The strict half of the contract: index.html is not a small known file
    the way *tuning.js is, so a key that resolves to ZERO fallbacks fails
    loudly rather than silently leaving the pre-JS render stale."""
    css = ".unrelated { color: red; }\n"
    with pytest.raises(ValueError, match="no var\\(\\.\\.\\.\\) fallback"):
        sync_css_fallbacks(css, REGISTRY_SAMPLE, "log", {"fooSize": 20},
                           where="index.html")


def test_disagreeing_fallbacks_are_refused_rather_than_picked_between():
    css = (".a { width: var(--log-foo-size, 13px); }\n"
           ".b { min-width: var(--log-foo-size, 14px); }\n")
    with pytest.raises(ValueError, match="disagreeing"):
        sync_css_fallbacks(css, REGISTRY_SAMPLE, "log", {"fooSize": 20},
                           where="index.html")


def test_a_partial_rewrite_never_happens_on_a_refusal():
    """Two keys in one save, the second one broken -- the first must NOT have
    already been spliced into the returned text, or a caller that discards
    the exception on a partial result would ship a half-synced file."""
    css = ".foo { width: var(--log-foo-size, 13px); }\n"  # no --log-ratio at all
    with pytest.raises(ValueError):
        sync_css_fallbacks(css, REGISTRY_SAMPLE, "log",
                           {"fooSize": 20, "ratio": 0.5}, where="index.html")


# ---- css_var_name parity with logtuning.js's own cssVarName ----------------
#
# Python cannot import a JS module, so this is a second door in the sense
# CLAUDE.md's dev-process rules describe ("when one door is impossible, the
# duplicate gets a test that COMPARES the two") -- run the REAL registry under
# node and confirm this function's var name for every declared tunable agrees
# with `cssVarName` actually computing it, not a restatement of the regex.

pytestmark_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


@pytestmark_node
def test_css_var_name_agrees_with_the_real_registrys_cssVarName():
    from sm64_events.server.tuning_api import TUNING_REGISTRIES as REGISTRIES
    log_js = REGISTRIES["log"]
    script = (
        f"import {{ TUNABLES, cssVarName }} from {log_js.as_uri()!r};\n"
        "console.log(JSON.stringify(Object.fromEntries(Object.keys(TUNABLES)"
        ".map((key) => [key, cssVarName(key)]))));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    js_names = json.loads(result.stdout)
    assert js_names, "logtuning.js declared no TUNABLES -- nothing to compare"
    for key, expected in js_names.items():
        assert css_var_name("log", key) == expected, (
            f"{key}: tuning_api.py's css_var_name says "
            f"{css_var_name('log', key)!r}, logtuning.js's own cssVarName "
            f"says {expected!r}")


@pytestmark_node
def test_css_literal_formatting_agrees_with_logTuningVars_for_every_tunable():
    """Not just the var NAME -- the LITERAL it is given. Sweeps every
    tunable's own min/max/a mid-range value through the real `logTuningVars`
    and confirms `sync_css_fallbacks`'s formatting (`_format_number` +
    `_css_literal`, exercised here through the public function) produces the
    identical string for each, closing the "two independent number-to-text
    roads" risk a hand-verified formatting rule would otherwise carry."""
    from sm64_events.server.tuning_api import TUNING_REGISTRIES as REGISTRIES
    log_js = REGISTRIES["log"]
    registry_source = log_js.read_text(encoding="utf-8")
    script = (
        f"import {{ TUNABLES, DEFAULTS, cssVarName, logTuningVars }} "
        f"from {log_js.as_uri()!r};\n"
        "const probes = {};\n"
        "for (const [key, row] of Object.entries(TUNABLES)) {\n"
        "  probes[key] = [row.min, row.max, (row.min + row.max) / 2];\n"
        "}\n"
        "const out = {};\n"
        "for (const [key, values] of Object.entries(probes)) {\n"
        "  out[key] = values.map((v) => "
        "    logTuningVars({ ...DEFAULTS, [key]: v })[cssVarName(key)]);\n"
        "}\n"
        "console.log(JSON.stringify({ probes, out }));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for key, probe_values in data["probes"].items():
        for value, expected_literal in zip(probe_values, data["out"][key]):
            # A one-off probe stylesheet naming THIS key's real var, reusing
            # the real registry source so the real `unit:` is what gets read.
            var = css_var_name("log", key)
            probe_css = f".probe {{ width: var({var}, __PLACEHOLDER__); }}"
            updated, written_css = sync_css_fallbacks(
                probe_css, registry_source, "log", {key: value},
                where="index.html")
            assert written_css[key] == expected_literal, (
                f"{key}={value}: tuning_api.py produced "
                f"{written_css[key]!r}, logTuningVars produced "
                f"{expected_literal!r}")


# ---- The endpoint: both files move together, or neither does ---------------

@pytest.fixture()
def log_endpoint(tmp_path, monkeypatch):
    """A real POST through `save_tuning`, but pointed at tmp_path copies of
    the REAL logtuning.js/index.html -- proves the round trip against the
    actual shipped files' structure without ever writing to the tracked
    copies (a sibling session may be running this same suite in this same
    worktree, per CLAUDE.md's dev-process rules)."""
    from sm64_events.server import tuning_api

    real_js = TUNING_REGISTRIES["log"]
    real_css, prefix = CSS_FALLBACK_TARGETS["log"]
    tmp_js = tmp_path / "logtuning.js"
    tmp_css = tmp_path / "index.html"
    tmp_js.write_text(real_js.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    tmp_css.write_text(real_css.read_text(encoding="utf-8"), encoding="utf-8", newline="")

    monkeypatch.setitem(tuning_api.TUNING_REGISTRIES, "log", tmp_js)
    monkeypatch.setitem(tuning_api.CSS_FALLBACK_TARGETS, "log", (tmp_css, prefix))
    monkeypatch.setattr(tuning_api, "is_frozen", lambda: False)

    router = tuning_api.create_tuning_router()
    endpoint = router.routes[0].endpoint
    return endpoint, tmp_js, tmp_css


def test_the_log_endpoint_moves_both_files_in_one_save(log_endpoint):
    from sm64_events.server import tuning_api
    endpoint, tmp_js, tmp_css = log_endpoint

    before_js = tmp_js.read_text(encoding="utf-8")
    before_css = tmp_css.read_text(encoding="utf-8")
    assert "var(--log-card-gap, 9px)" not in before_css  # sanity: not already 9px

    payload = endpoint("log", tuning_api.TuningBody(values={"cardGap": 9}))

    assert payload["written"] == 1
    assert payload["written_css"] == 1
    after_js = tmp_js.read_text(encoding="utf-8")
    after_css = tmp_css.read_text(encoding="utf-8")
    assert after_js != before_js
    assert after_css != before_css
    assert value_of(after_js, "cardGap") == "9"
    assert "var(--log-card-gap, 9px)" in after_css


def test_a_css_disagreement_fails_the_whole_save_leaving_both_files_untouched(
        log_endpoint):
    """The atomicity guarantee: a real logtuning.js save that cannot also
    sync index.html must not partially apply -- writing the registry alone
    would recreate this exact bug with the two files swapped."""
    from sm64_events.server import tuning_api
    endpoint, tmp_js, tmp_css = log_endpoint

    # `--log-stacked-rank-width` appears FOUR times in the real index.html
    # (2026-08-04, layout-matrix round: the "stacked" and "stackedRow"
    # direction rules each need their own copy for the wide cells AND the
    # narrow, @container-scoped ones -- up from the two rankStyle rules the
    # single-choice mechanism this replaced had) -- corrupt only the FIRST
    # occurrence so the rest disagree with it, the same failure mode
    # `test_disagreeing_fallbacks_are_refused_rather_than_picked_between`
    # proves against a hand-built sample, reproduced here against the real
    # file's structure. (This test named `--log-rank-width` until an earlier
    # round removed its own second occurrence as part of fixing BUG 1 -- a
    # rank column that absorbed free space up to a fixed cap instead of
    # genuinely -- leaving only one real reader of that var;
    # `--log-stacked-rank-width` is the var this same atomicity guarantee now
    # has multiple real readers of.)
    original = tmp_css.read_text(encoding="utf-8")
    corrupted = original.replace(
        "var(--log-stacked-rank-width, 420px)",
        "var(--log-stacked-rank-width, 999px)", 1)
    assert corrupted != original, (
        "the replace above matched nothing -- index.html's own fallback "
        "text no longer looks like what this test assumes")
    assert corrupted.count("var(--log-stacked-rank-width, 420px)") == 3, (
        "expected exactly three surviving agreeing occurrences -- the real "
        "file's stackedRankWidth fallback count no longer matches what this "
        "test assumes")
    tmp_css.write_text(corrupted, encoding="utf-8", newline="")

    before_js = tmp_js.read_text(encoding="utf-8")
    before_css = tmp_css.read_text(encoding="utf-8")

    with pytest.raises(HTTPException) as raised:
        endpoint("log", tuning_api.TuningBody(values={"stackedRankWidth": 300}))
    assert raised.value.status_code == 409
    assert "disagreeing" in raised.value.detail

    assert tmp_js.read_text(encoding="utf-8") == before_js, (
        "the registry was written even though the stylesheet sync failed")
    assert tmp_css.read_text(encoding="utf-8") == before_css, (
        "the stylesheet was written despite raising")


def test_a_climb_save_never_touches_index_html(tmp_path, monkeypatch):
    """climb/marelo have no entry in CSS_FALLBACK_TARGETS -- their saves must
    report written_css=0 and leave the stylesheet's mtime/content alone."""
    from sm64_events.server import tuning_api

    tmp_climb = tmp_path / "climbtuning.js"
    tmp_climb.write_text(TUNING_JS.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    monkeypatch.setitem(tuning_api.TUNING_REGISTRIES, "climb", tmp_climb)
    monkeypatch.setattr(tuning_api, "is_frozen", lambda: False)

    real_css, _prefix = CSS_FALLBACK_TARGETS["log"]
    before_css_mtime = real_css.stat().st_mtime

    router = tuning_api.create_tuning_router()
    endpoint = router.routes[0].endpoint
    payload = endpoint("climb", tuning_api.TuningBody(values={"ladderStepMs": 275}))

    assert payload["written_css"] == 0
    assert payload["css_path"] is None
    assert real_css.stat().st_mtime == before_css_mtime
