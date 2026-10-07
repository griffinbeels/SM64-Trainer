"""Guided Ghost Library flows on the real app over isolated API fixtures.

These tests deliberately use identities, missing winners and mixed clocks so
the former catalog-minimum shortcut cannot satisfy their assertions. Backend
package integrity and publication are covered by the real provider tests.
"""
import contextlib
from copy import deepcopy
import json
from pathlib import Path
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response
import pytest

from test_ui_ghost_switch import WIDTHS, get_driver, ghost_session
import ui_fixture

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / ".iteration" / "library-flow-verification"
LIBRARY = "/api/ghost/library"
TARGET = {"kind": "star", "entity_key": "2-4", "strat": ui_fixture.FIXTURE_STRAT}
SEGMENT = {"kind": "segment", "entity_key": "6", "strat": "Pole Glitch"}
STAR_NAME = "Whomp's Fortress · Fall onto the Caged Island"
OTHER_NAME = "Whomp's Fortress · Red Coins on the Floating Isle"
LONG_AUTHOR = "René the Very Patient Speedrunner"
STALE_ERROR = "The practice target changed. Choose a ghost for the current target."


def local(number, frames, *, strat=None, key="2-4", kind="star", clock="igt",
          version="us", available=True, name=None):
    return {"source": "local", "attempt_id": number, "frames": frames,
            "author": "You", "kind": kind, "entity_key": key,
            "target_name": name or (STAR_NAME if key == "2-4" else OTHER_NAME),
            "strat": strat or TARGET["strat"], "clock": clock,
            "game_version": version, "practice": False, "outcome": "success",
            "available": available, "reason": None if available else "Recording unavailable.",
            "recorded_utc": f"2026-10-01T12:00:{number % 60:02d}+00:00"}


def examples():
    """The opening strategy is slower; clocks and versions cannot compete."""
    bests = [local(1, 600), local(2, 570, strat="Owl"),
             local(3, 650, strat="Cannon practice route", available=False),
             local(4, 560, version="jp"), local(5, 480, clock="rta"),
             local(6, 490, key="2-3", strat="Standard"),
             local(7, 990, kind="segment", key="6", clock="rta",
                   strat="Pole Glitch", name="BitFS Pipe Entry"),
             local(8, 1000, kind="segment", key="6", clock="rta",
                   strat="Standard", name="BitFS Pipe Entry")]
    imported = {**local(200, 590), "source": "import", "attempt_id": None,
                "import_id": "a" * 64, "author": LONG_AUTHOR}
    ghosts = [deepcopy(row) for row in bests if row["available"]]
    ghosts += [local(101, 610), imported]
    return {"ghosts": ghosts, "bests": bests, "author": "", "shortcuts": [],
            "max_export": 4096, "max_race": 16}


def preview_examples():
    rows = []
    for number, comparison in enumerate(("faster", "not_faster", "no_best", "unknown"), 1):
        row = {**local(number, 400 + number), "source": "import", "attempt_id": None,
               "import_id": str(number) * 64, "author": LONG_AUTHOR,
               "duplicate": False, "importable": True, "comparison": comparison,
               "your_best_frames": 600 if comparison in ("faster", "not_faster") else None}
        if comparison == "not_faster":
            row["frames"] = 650
        if comparison == "no_best":
            row.update(entity_key="1-0", target_name="Bob-omb Battlefield · Big Bob-omb on the Summit")
        if comparison == "unknown":
            row["game_version"] = None
        rows.append(row)
    rows.append({**rows[0], "import_id": "5" * 64, "duplicate": True,
                 "importable": False, "reason": "Already in your library."})
    rows.append({**rows[0], "import_id": "6" * 64, "kind": "segment", "entity_key": "999",
                 "target_name": "Shared movement without a matching definition",
                 "available": False, "comparison": "unknown",
                 "reason": "Import now; add a matching segment definition before racing."})
    return rows


def attach_session_routes(app, state, record, job):
    @app.middleware("http")
    async def observe(request, call_next):
        if request.url.path == "/api/target" and request.method != "GET":
            state["target_writes"].append(request.url.path)
        return await call_next(request)

    @app.get("/api/ghost")
    def ghost_get():
        return state["session"]

    @app.put("/api/ghost")
    def ghost_put(body: dict):
        record("PUT", "/api/ghost", body)
        if body.get("target") != state["session"]["target"]:
            raise HTTPException(422, STALE_ERROR)
        state["writes"].append(deepcopy(body))
        state["session"]["selection"]["ghosts"] = body["ghosts"]
        return state["session"]

    @app.post(LIBRARY + "/prepare-race")
    def prepare_race(body: dict):
        record("POST", LIBRARY + "/prepare-race", body)
        if state["stale_race"]:
            state["session"]["target"] = {**TARGET, "entity_key": "2-3"}
        return {"prepared": True}

    @app.api_route("/api/ghost/{unknown:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def unknown(request: Request, unknown):
        text = f"Unmatched fixture call: {request.method} {request.url.path}?{request.url.query}"
        state["unmatched"].append(text)
        print(text, flush=True)
        raise HTTPException(404, text)


def attach_preference_routes(app, state, record, job):
    @app.get(LIBRARY)
    def library_get(request: Request):
        assert not request.url.query, f"Unexpected catalog query: {request.url}"
        record("GET", LIBRARY)
        return state["catalog"]

    @app.put(LIBRARY + "/author")
    def author_put(body: dict):
        record("PUT", LIBRARY + "/author", body)
        if not body["author"].strip() or len(body["author"].encode("utf-8")) > 128:
            raise HTTPException(422, "Use a player name up to 128 UTF-8 bytes.")
        state["catalog"]["author"] = body["author"].strip()
        return {"author": state["catalog"]["author"]}

    @app.post(LIBRARY + "/shortcuts")
    def shortcut_post(body: dict):
        record("POST", LIBRARY + "/shortcuts", body)
        shortcut = {"id": "shortcut-1", "name": body["name"], "version": 1,
                    "criteria": deepcopy(body["criteria"])}
        state["catalog"]["shortcuts"].append(shortcut)
        return {"shortcuts": state["catalog"]["shortcuts"]}

    @app.delete(LIBRARY + "/shortcuts/{identity}")
    def shortcut_delete(identity):
        record("DELETE", LIBRARY + "/shortcuts/" + identity)
        state["catalog"]["shortcuts"] = [row for row in state["catalog"]["shortcuts"]
                                         if row["id"] != identity]
        return {"shortcuts": state["catalog"]["shortcuts"]}


def attach_export_routes(app, state, record, job):
    @app.post(LIBRARY + "/export")
    def export_post(body: dict):
        record("POST", LIBRARY + "/export", body)
        selected = body["selected"]
        count = len(selected["attempts"]) + len(selected["imports"])
        assert count > 0, "UI submitted an empty export"
        return job({"download_url": LIBRARY + "/downloads/fixture", "exported": count, "skipped": []})

    @app.get(LIBRARY + "/downloads/fixture")
    def download():
        return Response(b"isolated export receipt", media_type="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="fixture.sm64ghosts"'})


def attach_import_routes(app, state, record, job):
    @app.post(LIBRARY + "/import/preview")
    async def import_preview(request: Request):
        raw = await request.body()
        record("POST", LIBRARY + "/import/preview", {"bytes": len(raw)})
        if raw == b"broken":
            return job(None, "Choose a valid SM64 ghost collection.", failed=True)
        identity = "preview-" + str(len(state["previews"]) + 1)
        result = {"preview_id": identity, "ghosts": deepcopy(state["preview_rows"]),
                  "expires_utc": "2099-01-01T00:00:00+00:00"}
        state["previews"][identity] = result
        return job(result)

    @app.post(LIBRARY + "/import/commit")
    def import_commit(body: dict):
        record("POST", LIBRARY + "/import/commit", body)
        assert body["selected"], "UI submitted an empty import"
        preview = state["previews"].pop(body["preview_id"])
        selected = [row for row in preview["ghosts"] if row["import_id"] in body["selected"]]
        assert len(selected) == len(body["selected"]) and all(row["importable"] for row in selected)
        state["catalog"]["ghosts"].extend(deepcopy(selected))
        return job({"imported": len(selected), "duplicates": 0,
                    "unmatched": sum(not row["available"] for row in selected),
                    "import_ids": list(body["selected"]), "recorded_utc": "2026-10-07T12:00:00+00:00"})

    @app.delete(LIBRARY + "/import/preview/{identity}")
    def preview_delete(identity):
        record("DELETE", LIBRARY + "/import/preview/" + identity)
        state["previews"].pop(identity, None)
        return {"discarded": True}

    @app.get(LIBRARY + "/jobs/{identity}")
    def jobs_get(identity):
        return state["jobs"][identity]


@contextlib.contextmanager
def serve_library(folder, *, catalog=None, target=None):
    """Shipping UI; fixture APIs record every sharing write and reject unknown calls.

    Content queries are intentionally exact. A new undocumented endpoint never
    silently falls back to a bare path, which would hide a consumer mismatch.
    """
    folder.mkdir(parents=True, exist_ok=True)
    catalog = deepcopy(catalog or examples())
    session = ghost_session(enabled=False, target=target or TARGET)
    session["selection"] = {"ghosts": {"pb": True, "attempts": [], "imports": ["a" * 64]},
                            "pb": {"attempt_id": 1, "frames": 600, "native_available": True},
                            "playing": [{"key": "pb", "attempt_id": 1},
                                        {"key": "a" * 64, "attempt_id": None, "import_id": "a" * 64}],
                            "candidates": [], "missing": []}
    state = {"catalog": catalog, "session": session, "calls": [], "target_writes": [],
             "writes": [], "previews": {}, "jobs": {}, "unmatched": [],
             "preview_rows": preview_examples(), "stale_race": False}
    original = ui_fixture.create_app

    def create_app(*args, **kwargs):
        app = original(*args, **kwargs)
        app.router.routes = [route for route in app.router.routes
                             if not str(getattr(route, "path", "")).startswith("/api/ghost")]

        def record(method, path, body=None):
            state["calls"].append((method, path, deepcopy(body)))

        def job(result, message="Ready", *, failed=False):
            identity = str(len(state["jobs"]) + 1)
            state["jobs"][identity] = {"state": "error" if failed else "done", "progress": 1,
                                        "message": message, "result": result}
            return {"job_id": identity}

        attach_preference_routes(app, state, record, job)
        attach_export_routes(app, state, record, job)
        attach_import_routes(app, state, record, job)
        # The broad unmatched-call refusal follows every exact library route.
        attach_session_routes(app, state, record, job)
        return app

    ui_fixture.create_app = create_app
    try:
        segment_args = {"target_segment": 6, "arm_segment": 6} if target and target["kind"] == "segment" else {}
        with ui_fixture.serve_ui(db_path=folder / "fixture.db", from_dev_db=False,
                                 seed_editor_fixtures=True, **segment_args) as base:
            state["target_writes"].clear()  # fixture seeding is not a browser gesture
            yield base, state
    finally:
        ui_fixture.create_app = original


def until(page, expression, expected=True, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = page.evaluate(expression)
        if value == expected:
            return value
        page.wait_ms(60)
    raise AssertionError(f"Never reached {expected!r}; last value {value!r} for {expression}")


def click_text(page, text, scope=".modal"):
    """Resolve one visible button by its public words, then use a real click."""
    expression = f"""(() => {{ const found=Array.from(document.querySelectorAll({json.dumps(scope + ' button')}))
      .filter(node=>node.getClientRects().length && (node.getAttribute('aria-label')==={json.dumps(text)}
        || node.textContent.trim()==={json.dumps(text)} || node.textContent.trim()==={json.dumps(text + '→')}
        || node.textContent.trim().startsWith({json.dumps(text + ' (')})));
      if (found.length!==1) throw new Error('Expected one button '+{json.dumps(text)}+', got '+found.length);
      found[0].setAttribute('data-flow-click','yes'); }})()"""
    page.evaluate(expression)
    page.click('[data-flow-click="yes"]')
    page.evaluate("document.querySelector('[data-flow-click]')?.removeAttribute('data-flow-click')")


def input_value(page, selector, value, event="input"):
    page.evaluate(f"""(() => {{const node=document.querySelector({json.dumps(selector)});
      if (!node) throw new Error('Missing field '+{json.dumps(selector)});
      node.value={json.dumps(value)};node.dispatchEvent(new Event({json.dumps(event)},{{bubbles:true}}));}})()""")


def page_is(page, name):
    page.wait_for(f'.ghost-library[data-page="{name}"]')


def rendered_rows(page):
    return page.evaluate("""Array.from(document.querySelectorAll('.ghost-library .ghost-library-row'), row=>({
      id:row.dataset.ghostId, text:row.textContent.trim(),
      checked:row.querySelector('input[type=checkbox]')?.checked??null,
      disabled:row.querySelector('input[type=checkbox]')?.disabled??null}))""")


def selected_ids(page):
    return [row["id"] for row in rendered_rows(page) if row["checked"]]


def tick(page, identity):
    page.click(f'.ghost-library [data-ghost-id="{identity}"] input[type=checkbox]')


def open_library(page, base, width):
    page.set_viewport(width, 1200)
    page.goto(base)
    page.wait_for(".ghost-library-button")
    page.click(".ghost-library-button")  # known-good control interaction
    page_is(page, "home")
    until(page, "document.querySelectorAll('.ghost-library button').length>2")


def fits_and_shoot(page, width, name):
    """The surface is nonempty and its rows/time columns align in real CSS."""
    measured = page.evaluate("""(() => {
      const library=document.querySelector('.ghost-library');const modal=library.closest('.modal');
      const rows=Array.from(library.querySelectorAll('.ghost-library-row'));
      return {modal:modal.scrollWidth-modal.clientWidth,library:library.scrollWidth-library.clientWidth,
        body:library.closest('.modal-body').scrollWidth-library.closest('.modal-body').clientWidth,
        text:library.textContent.trim().length,bound:library.getBoundingClientRect().right,
        rows:rows.map(row=>({overflow:row.scrollWidth-row.clientWidth,
          right:row.getBoundingClientRect().right,
          time:row.querySelector('.ghost-library-time')?.getBoundingClientRect().right??null}))};})()""")
    assert measured["text"] > 20, measured
    assert all(measured[key] <= 1 for key in ("modal", "library", "body")), measured
    assert all(row["overflow"] <= 1 for row in measured["rows"]), measured
    assert all(row["right"] <= measured["bound"] + 1 for row in measured["rows"]), measured
    times = [row["time"] for row in measured["rows"] if row["time"] is not None]
    if times:
        assert max(times) - min(times) <= 1, measured
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    (SCREENSHOTS / f"{name}-{width}.png").write_bytes(page.screenshot())


def assert_clean(page, state, base, *, expected_http=None):
    problems = page.problems()
    # A deliberate export attachment handoff may produce ERR_ABORTED. Its
    # exact same-origin URL is the only download failure allowed here.
    problems = [problem for problem in problems if not (
        problem.startswith("requestfailed: ")
        and base + LIBRARY + "/downloads/fixture" in problem
        and problem.endswith("(net::ERR_ABORTED)"))]
    if expected_http:
        assert f"HTTP 422 {base}{expected_http}" in problems, problems
        problems = [problem for problem in problems if not (
            problem == f"HTTP 422 {base}{expected_http}" or
            problem.startswith("console.error: Failed to load resource: the server responded with a status of 422"))]
    assert problems == [], problems
    assert state["unmatched"] == []
    assert state["target_writes"] == [], "Library browsing wrote the live practice target"


def library_log(page):
    return page.evaluate("""(async () => {const reader=await import('/ui/uilog.js');
      return reader.readGhostLibrary(document.querySelector('.ghost-library'));})()""")


def prove_layout_guard(page, width):
    """A deliberately overflowing rendered row must make the same guard fail."""
    page.evaluate("document.querySelector('.ghost-library-row').style.minWidth='2000px'")
    try:
        with pytest.raises(AssertionError):
            fits_and_shoot(page, width, "deliberate-overflow-must-not-pass")
    finally:
        page.evaluate("document.querySelector('.ghost-library-row').style.removeProperty('min-width')")


def export_start(page, state, name="Local Runner"):
    click_text(page, "Export")
    if not state["catalog"]["author"]:
        page_is(page, "name")
        input_value(page, '.ghost-library-name input', "é" * 65)
        assert page.evaluate("document.querySelector('.modal-actions .primary').disabled")
        assert page.evaluate("document.querySelector('.ghost-library .error').getBoundingClientRect().height") > 0
        assert not any(call[1].endswith("/author") for call in state["calls"])
        input_value(page, '.ghost-library-name input', name)
        click_text(page, "Continue")
    page_is(page, "export")


def repeat_export_and_browse(page, state, width):
    page.click(".modal-close")
    page.wait_for(".ghost-library-button")
    page.click(".ghost-library-button")
    page_is(page, "home")
    export_start(page, state)
    assert len([call for call in state["calls"] if call[1].endswith("/author")]) == 1
    click_text(page, "Change")
    page_is(page, "name")
    input_value(page, '.ghost-library-name input', "Renamed Runner")
    click_text(page, "Continue")
    page_is(page, "export")
    assert state["catalog"]["author"] == "Renamed Runner"
    click_text(page, "All personal bests")
    page_is(page, "review")
    assert {row["id"] for row in rendered_rows(page)} == {
        "attempt:1", "attempt:2", "attempt:4", "attempt:5", "attempt:6", "attempt:7", "attempt:8"}
    assert "attempt:101" not in {row["id"] for row in rendered_rows(page)}
    page.click(".modal-close")
    page.click(".ghost-library-button")
    page_is(page, "home")
    click_text(page, "Your personal bests")
    page_is(page, "browser")
    assert {row["id"] for row in rendered_rows(page)} == {
        "attempt:1", "attempt:2", "attempt:3", "attempt:4", "attempt:5", "attempt:6", "attempt:7", "attempt:8"}
    fits_and_shoot(page, width, "personal-bests")


@pytest.mark.parametrize("width", WIDTHS)
def test_context_strategy_bests_author_once_and_back_preserves_choices(tmp_path, width):
    """The opening strategy never masquerades as overall best; slower best alone exports."""
    with serve_library(tmp_path / f"strategy-{width}") as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, width)
            home = rendered_rows(page)
            assert {row["id"] for row in home} == {"attempt:1", "a" * 64}, home
            assert all(STAR_NAME in row["text"] for row in home)
            assert "Owl" not in " ".join(row["text"] for row in home)
            log = library_log(page)
            assert log and log["surface"] == "ghost_library" and log["note"].startswith("home · "), log
            assert log["title"] == "Ghosts you are racing" and len(log["rows"]) == 2, log
            fits_and_shoot(page, width, "home")
            prove_layout_guard(page, width)
            export_start(page, state)
            assert [(method, path, body) for method, path, body in state["calls"]
                    if path.endswith("/author")] == [("PUT", LIBRARY + "/author", {"author": "Local Runner"})]
            click_text(page, "This Star's Personal Bests")
            page_is(page, "target-bests")
            rows = rendered_rows(page)
            assert [row["id"] for row in rows].count("attempt:2") == 1
            assert set(selected_ids(page)) == {"attempt:2", "attempt:4", "attempt:5"}, rows
            missing = next(row for row in rows if row["id"] == "attempt:3")
            assert missing["disabled"] and "Recording unavailable" in missing["text"]
            assert any("US" in row["text"] or "JP" in row["text"] for row in rows)
            assert "RTA" in " ".join(row["text"] for row in rows)
            assert library_log(page).get("rows"), "The rendered strategy rows were not logged"
            fits_and_shoot(page, width, "strategy-bests")
            # Only the slower opening strategy remains selected.
            for identity in ("attempt:2", "attempt:4", "attempt:5"):
                tick(page, identity)
            tick(page, "attempt:1")
            click_text(page, "Review 1 ghost")
            page_is(page, "review")
            assert selected_ids(page) == []  # review rows do not pretend to be edit controls
            assert "TJ Owlless" in " ".join(row["text"] for row in rendered_rows(page))
            click_text(page, "Back")
            page_is(page, "target-bests")
            assert selected_ids(page) == ["attempt:1"]
            click_text(page, "Review 1 ghost")
            page_is(page, "review")
            fits_and_shoot(page, width, "export-review")
            click_text(page, "Export collection")
            page_is(page, "receipt")
            exported = [body for method, path, body in state["calls"] if path.endswith("/export")]
            assert exported == [{"selected": {"attempts": [1], "imports": []}, "author": "Local Runner"}]
            fits_and_shoot(page, width, "export-receipt")
            repeat_export_and_browse(page, state, width)
            assert_clean(page, state, base)


def test_missing_overall_is_visible_not_replaced_and_segment_shortcut_matches(tmp_path):
    catalog = examples()
    # The overall US/IGT winner has lost its recording. Its available slower
    # same-strategy survivor remains in the catalog; it must never be promoted.
    catalog["bests"] = [local(50, 500, strat="Owl", available=False), local(1, 600)]
    catalog["ghosts"].append(local(51, 510, strat="Owl"))
    catalog["author"] = "Known Runner"
    with serve_library(tmp_path / "missing", catalog=catalog) as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 1500)
            export_start(page, state)
            click_text(page, "This Star's Personal Bests")
            page_is(page, "target-bests")
            rows = rendered_rows(page)
            assert {row["id"] for row in rows} == {"attempt:50", "attempt:1"}, rows
            winner = next(row for row in rows if row["id"] == "attempt:50")
            assert winner["disabled"] and "Overall best" in winner["text"], winner
            assert selected_ids(page) == []
            tick(page, "attempt:1")
            click_text(page, "Review 1 ghost")
            page_is(page, "review")
            assert_clean(page, state, base)
            # An equally fast available strategy may default when its tied
            # peer is missing. No slower recording is substituted.
            state["catalog"]["bests"].append(local(52, 500, strat="Owl tied route"))
            state["catalog"]["ghosts"].append(local(52, 500, strat="Owl tied route"))
            page.click(".modal-close")
            page.click(".ghost-library-button")
            page_is(page, "home")
            export_start(page, state)
            click_text(page, "This Star's Personal Bests")
            page_is(page, "target-bests")
            assert selected_ids(page) == ["attempt:52"]
            tied = [row for row in rendered_rows(page) if row["id"] in ("attempt:50", "attempt:52")]
            assert len(tied) == 2 and all("Joint overall best" in row["text"] for row in tied), tied
            assert_clean(page, state, base)
    catalog = examples()
    catalog["author"] = "Known Runner"
    with serve_library(tmp_path / "segment", catalog=catalog, target=SEGMENT) as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 850)
            export_start(page, state)
            click_text(page, "This Segment's Personal Bests")
            page_is(page, "target-bests")
            assert set(row["id"] for row in rendered_rows(page)) == {"attempt:7", "attempt:8"}
            assert selected_ids(page) == ["attempt:7"]
            tick(page, "attempt:7")
            tick(page, "attempt:8")
            click_text(page, "Review 1 ghost")
            page_is(page, "review")
            fits_and_shoot(page, 850, "segment-review")
            assert_clean(page, state, base)


def cancel_then_commit_subset(page, state, package, width):
    click_text(page, "Cancel import")
    page_is(page, "home")
    assert state["previews"] == {}
    assert len(state["catalog"]["ghosts"]) == len(examples()["ghosts"])
    assert any(call[0] == "DELETE" and "/import/preview/" in call[1] for call in state["calls"])
    click_text(page, "Import")
    page_is(page, "import")
    page.set_input_files('.ghost-library input[type="file"]', str(package))
    page_is(page, "import-review")
    # Manual subset includes an unmatched segment: storage is allowed;
    # racing it still requires its missing definition.
    for identity in ("1" * 64, "2" * 64, "4" * 64):
        tick(page, identity)
    assert set(selected_ids(page)) == {"3" * 64, "6" * 64}
    click_text(page, "Import 2 ghosts")
    page_is(page, "receipt")
    commits = [call[2] for call in state["calls"] if call[1].endswith("/commit")]
    assert len(commits) == 1 and set(commits[0]["selected"]) == {"3" * 64, "6" * 64}
    assert state["previews"] == {}
    assert len(state["catalog"]["ghosts"]) == len(examples()["ghosts"]) + 2
    fits_and_shoot(page, width, "import-receipt")


@pytest.mark.parametrize("width", WIDTHS)
def test_import_preview_selection_back_cancel_and_explicit_commit(tmp_path, width):
    """File selection is only preview; duplicates and unknowns remain explicit."""
    package = tmp_path / "selected.sm64ghosts"
    package.write_bytes(b"isolated collection fixture")
    with serve_library(tmp_path / f"import-{width}") as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, width)
            click_text(page, "Import")
            page_is(page, "import")
            page.set_input_files('.ghost-library input[type="file"]', str(package))
            page_is(page, "import-review")
            rows = rendered_rows(page)
            assert len(rows) == 6, rows
            assert set(selected_ids(page)) == {str(number) * 64 for number in (1, 2, 3, 4, 6)}
            duplicate = next(row for row in rows if row["id"] == "5" * 64)
            assert duplicate["disabled"] and not duplicate["checked"]
            assert "Already in" in duplicate["text"]
            assert "matching" in next(row["text"] for row in rows if row["id"] == "6" * 64).lower()
            assert "unknown" in next(row["text"] for row in rows if row["id"] == "4" * 64).lower()
            assert not any(call[1].endswith("/commit") for call in state["calls"])
            fits_and_shoot(page, width, "import-all")
            click_text(page, "Faster than my best")
            assert selected_ids(page) == ["1" * 64]
            fits_and_shoot(page, width, "import-faster")
            page.click('.ghost-library-import-explanation input[type="checkbox"]')
            assert set(selected_ids(page)) == {"1" * 64, "3" * 64}
            tick(page, "1" * 64)
            assert selected_ids(page) == ["3" * 64]
            click_text(page, "Back")
            page_is(page, "import")
            assert not any(call[0] == "DELETE" for call in state["calls"])
            click_text(page, "Continue review")
            page_is(page, "import-review")
            assert selected_ids(page) == ["3" * 64]
            cancel_then_commit_subset(page, state, package, width)
            assert_clean(page, state, base)


def choose_empty_target(page):
    click_text(page, "Choose a star or segment")
    page.wait_for(".modal-grid .entity-grid")
    page.click('.modal-grid button[title="Whomp\'s Fortress"]')
    page.click('.modal-grid button[title="Chip Off Whomp\'s Block"]')
    page_is(page, "browser")
    assert rendered_rows(page) == []
    assert "Chip Off Whomp's Block" in page.evaluate(
        "document.querySelector('.ghost-library-filter-target').textContent")


@pytest.mark.parametrize("width", WIDTHS)
def test_local_target_picker_and_saved_search_resolve_new_results(tmp_path, width):
    catalog = examples()
    catalog["author"] = "Known Runner"
    with serve_library(tmp_path / f"custom-{width}", catalog=catalog) as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, width)
            click_text(page, "Find a star or segment")
            page.wait_for(".modal-grid .entity-grid")
            page.click('.modal-grid button[title="Whomp\'s Fortress"]')
            page.click('.modal-grid button[title="Red Coins on the Floating Isle"]')
            page_is(page, "browser")
            assert {row["id"] for row in rendered_rows(page)} == {"attempt:6"}
            assert state["target_writes"] == []
            assert state["session"]["target"] == TARGET
            choose_empty_target(page)
            click_text(page, "Back")
            page_is(page, "home")
            export_start(page, state)
            click_text(page, "Choose a custom set")
            page_is(page, "browser")
            input_value(page, '.ghost-library input[type="search"]', "TJ Owlless")
            click_text(page, "Select filtered")
            chosen = selected_ids(page)
            assert "attempt:1" in chosen and "attempt:101" in chosen
            assert "attempt:2" not in chosen
            fits_and_shoot(page, width, "custom-selection")
            click_text(page, f"Review {len(chosen)} ghosts")
            page_is(page, "review")
            click_text(page, "Export collection")
            page_is(page, "receipt")
            input_value(page, '.ghost-library-save-shortcut input', "My owlless ghosts")
            click_text(page, "Save shortcut")
            until(page, "document.querySelector('.ghost-library').textContent.includes('Shortcut saved')")
            shortcut = state["catalog"]["shortcuts"][0]
            assert shortcut["criteria"]["query"] == "TJ Owlless"
            assert "attempts" not in shortcut["criteria"] and "imports" not in shortcut["criteria"]
            # New result matches the saved criteria. Reopening resolves it now;
            # a shortcut that stores the old selected IDs cannot pass this.
            state["catalog"]["ghosts"].append(local(109, 605))
            page.click(".modal-close")
            page.click(".ghost-library-button")
            page_is(page, "home")
            export_start(page, state)
            click_text(page, "My owlless ghosts")
            page_is(page, "review")
            current_ids = {row["id"] for row in rendered_rows(page)}
            assert "attempt:109" in current_ids
            assert current_ids == set(chosen) | {"attempt:109"}
            fits_and_shoot(page, width, "saved-search")
            assert_clean(page, state, base)


def test_empty_import_error_and_stale_race_keep_the_user_choices(tmp_path):
    package = tmp_path / "broken.sm64ghosts"
    package.write_bytes(b"broken")
    with serve_library(tmp_path / "errors") as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 850)
            click_text(page, "Import")
            page_is(page, "import")
            page.set_input_files('.ghost-library input[type="file"]', str(package))
            page.wait_for('.ghost-library [role="alert"]')
            assert "Choose a valid SM64 ghost collection" in page.evaluate(
                "document.querySelector('.ghost-library [role=alert]').textContent")
            assert not state["previews"] and not any(call[1].endswith("/commit") for call in state["calls"])
            fits_and_shoot(page, 850, "invalid-file")
            click_text(page, "Back")
            page_is(page, "home")
            click_text(page, "Choose ghosts to race")
            page_is(page, "browser")
            tick(page, "a" * 64)
            state["stale_race"] = True
            click_text(page, "Use selected ghosts")
            page.wait_for('.ghost-library [role="alert"]')
            assert STALE_ERROR in page.evaluate("document.querySelector('.ghost-library [role=alert]').textContent")
            assert selected_ids(page) == ["attempt:1"]
            assert state["writes"] == []
            preparation = [call[2] for call in state["calls"] if call[1].endswith("/prepare-race")]
            assert preparation == [{"ghosts": {"pb": True, "attempts": [], "imports": []}, "target": TARGET}]
            fits_and_shoot(page, 850, "stale-race")
            assert_clean(page, state, base, expected_http="/api/ghost")
    empty = {**examples(), "ghosts": [], "bests": [], "author": "Known Runner"}
    with serve_library(tmp_path / "empty", catalog=empty) as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 850)
            export_start(page, state)
            assert page.evaluate("""Array.from(document.querySelectorAll('.ghost-library button'))
              .find(node=>node.textContent.includes("This Star's Personal Bests")).disabled""")
            assert "No" in page.evaluate("document.querySelector('.ghost-library').textContent")
            fits_and_shoot(page, 850, "empty-export")
            assert_clean(page, state, base)


def test_first_name_back_returns_to_pending_browse_selection(tmp_path):
    """Browse-first export preserves local filters/checkboxes through first name."""
    with serve_library(tmp_path / "name-back") as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 850)
            click_text(page, "Your personal bests")
            page_is(page, "browser")
            input_value(page, '.ghost-library input[type="search"]', "Owl")
            tick(page, "attempt:1")
            tick(page, "attempt:2")
            assert set(selected_ids(page)) == {"attempt:1", "attempt:2"}
            click_text(page, "Review 2 ghosts")
            page_is(page, "name")
            click_text(page, "Back")
            page_is(page, "browser")
            assert set(selected_ids(page)) == {"attempt:1", "attempt:2"}
            assert page.evaluate("document.querySelector('.ghost-library input[type=search]').value") == "Owl"
            assert not any(call[1].endswith("/author") for call in state["calls"])
            click_text(page, "Review 2 ghosts")
            page_is(page, "name")
            input_value(page, '.ghost-library-name input', "Browse-first Runner")
            click_text(page, "Continue")
            page_is(page, "review")
            assert {row["id"] for row in rendered_rows(page)} == {"attempt:1", "attempt:2"}
            fits_and_shoot(page, 850, "browse-name-review")
            click_text(page, "Back")
            page_is(page, "browser")
            assert set(selected_ids(page)) == {"attempt:1", "attempt:2"}
            assert_clean(page, state, base)


@pytest.mark.parametrize("keep_pb", (True, False))
def test_race_checkbox_selection_matches_the_applied_slots(tmp_path, keep_pb):
    """The saved PB has one slot; unchecking its row removes that slot."""
    catalog = {**examples(), "max_race": 2}
    with serve_library(tmp_path / f"race-pb-{keep_pb}", catalog=catalog) as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 850)
            click_text(page, "Choose ghosts to race")
            page_is(page, "browser")
            assert set(selected_ids(page)) == {"attempt:1", "a" * 64}
            assert not page.evaluate("document.querySelector('.modal-actions .primary').disabled")
            if keep_pb:
                tick(page, "a" * 64)
                expected = {"pb": True, "attempts": [], "imports": []}
            else:
                tick(page, "attempt:1")
                tick(page, "attempt:101")
                expected = {"pb": False, "attempts": [101], "imports": ["a" * 64]}
            click_text(page, "Use selected ghosts")
            until(page, "document.querySelector('.ghost-library')===null")
            assert state["writes"] == [{"ghosts": expected, "target": TARGET}]
            preparations = [call[2] for call in state["calls"] if call[1].endswith("/prepare-race")]
            assert preparations == state["writes"]
            assert_clean(page, state, base)


def test_race_slot_limit_includes_pb_and_empty_selection_is_disabled(tmp_path):
    catalog = {**examples(), "max_race": 2}
    with serve_library(tmp_path / "race-cap", catalog=catalog) as (base, state):
        with get_driver().launch() as page:
            open_library(page, base, 850)
            click_text(page, "Choose ghosts to race")
            page_is(page, "browser")
            tick(page, "attempt:101")
            assert page.evaluate("document.querySelector('.modal-actions .primary').disabled")
            assert "Choose at most 1 recordings with this PB choice" in page.evaluate(
                "document.querySelector('.modal-actions').textContent")
            assert not any(call[1].endswith("/prepare-race") for call in state["calls"])
            fits_and_shoot(page, 850, "race-cap")
            tick(page, "attempt:1")
            assert not page.evaluate("document.querySelector('.modal-actions .primary').disabled")
            tick(page, "attempt:101")
            tick(page, "a" * 64)
            assert selected_ids(page) == []
            assert page.evaluate("document.querySelector('.modal-actions .primary').disabled")
            assert state["writes"] == []
            assert_clean(page, state, base)
