"""Bulk sharing on the real page, routes, jobs, provider and ghost session."""
import io
import json
from pathlib import Path
import time
from urllib.request import urlopen
import zipfile

from sm64_events.core.timefmt import format_igt_short
from test_ui_ghost_switch import WIDTHS, get_driver
from ghost_sharing_fixture import FOREIGN_COUNT, TARGET, serve_collection

ROOT = Path(__file__).resolve().parents[1]


def until(page, expression, expected, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = page.evaluate(expression)
        if value == expected:
            return value
        page.wait_ms(80)
    raise AssertionError(f"Never reached {expected!r}: {value!r}")


def choose(page, label, value):
    selector = json.dumps(f'.ghost-library select[aria-label="{label}"]')
    page.evaluate(f"""(() => {{ const field=document.querySelector({selector});
      field.value={json.dumps(value)};field.dispatchEvent(new Event('change',{{bubbles:true}})); }})()""")


def search(page, query):
    page.evaluate(f"""(() => {{ const field=document.querySelector('.ghost-library input[type=search]');
      field.value={json.dumps(query)};field.dispatchEvent(new Event('input',{{bubbles:true}})); }})()""")


def click_race(page):
    page.evaluate("Array.from(document.querySelectorAll('.modal-actions button')).find(b=>b.textContent.startsWith('Race ')).click()")


def rows(page):
    return page.evaluate("""Array.from(document.querySelectorAll('.ghost-library-row'), row => ({
      creator:row.querySelector('.ghost-library-creator').textContent.trim(),
      strategy:row.querySelector('.ghost-library-description .meta').textContent.trim(),
      target:row.querySelector('strong').textContent.trim(),
      time:row.querySelector('.ghost-library-time').firstChild.textContent.trim(),
      checked:row.querySelector('input').checked}))""")


def assert_fits_and_shoot(page, width, name):
    measured = page.evaluate("""(() => {const modal=document.querySelector('.modal');
      const library=document.querySelector('.ghost-library');
      return {modal:modal.scrollWidth-modal.clientWidth, library:library.scrollWidth-library.clientWidth,
        rows:Array.from(library.querySelectorAll('.ghost-library-row'),row=>row.scrollWidth-row.clientWidth)};})()""")
    assert measured["modal"] <= 1 and measured["library"] <= 1
    assert all(value <= 1 for value in measured["rows"])
    folder = ROOT / ".iteration" / "ghost148-collection"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}-{width}.png").write_bytes(page.screenshot())


def exported_collection(page, base, folder):
    page.click(".modal-actions button.primary")
    page.wait_for(".modal-actions a[download]")
    href = page.evaluate("document.querySelector('.modal-actions a[download]').getAttribute('href')")
    with urlopen(base + href, timeout=10) as response:
        assert response.status == 200 and ".sm64ghosts" in response.headers["Content-Disposition"]
        raw = response.read()
    path = folder / "downloaded.sm64ghosts"
    path.write_bytes(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        manifest = json.loads(package.read("collection.json"))["ghosts"]
    return path, manifest, href


def import_collection(page, path, text):
    page.set_input_files(".ghost-library input[type=file]", str(path))
    # The job progress is also a live region. Wait for the final result, which
    # is published after refreshing the catalog, rather than that earlier line.
    until(page, "Boolean(document.querySelector('.ghost-library [role=status]:not(.job-status)')?.textContent.includes(" + json.dumps(text) + "))", True)


def check_problems(page, download):
    # Chromium hands an attachment navigation to its download manager. That
    # deliberate handoff can report ERR_ABORTED; the actual FileResponse was
    # checked above. Every console error, other failed request or HTTP error
    # remains a failure.
    problems = [item for item in page.problems()
                if not (item.startswith("requestfailed: ") and download in item
                        and item.endswith("(net::ERR_ABORTED)"))]
    assert problems == []


def local_export_and_race(page, base, state, folder, width):
    page.wait_for(".ghost-library-row")
    assert len(rows(page)) == 23  # the save-stated attempt is hidden by default
    choose(page, "Sort ghosts", "recent")  # positive control before the sharing gestures
    until(page, "document.querySelector('.ghost-library-time').firstChild.textContent.trim()", format_igt_short(143))
    choose(page, "Sort ghosts", "fastest")
    until(page, "document.querySelector('.ghost-library-time').firstChild.textContent.trim()", format_igt_short(121))
    page.evaluate("""(() => {const field=document.querySelector('.ghost-library-author input');
      field.value='Local Runner';field.dispatchEvent(new Event('input',{bubbles:true}));})()""")
    page.click(".ghost-library-selection button:first-child")
    until(page, "document.querySelector('.ghost-library-count').textContent.trim()", "23 selected")
    assert page.evaluate("Array.from(document.querySelectorAll('.modal-actions button')).find(b=>b.textContent.startsWith('Race ')).disabled")
    assert_fits_and_shoot(page, width, "bulk-export")
    exported, manifest, download = exported_collection(page, base, folder)
    assert len(manifest) == 23 and {row["author"] for row in manifest} == {"Local Runner"}
    assert state["store"].get_state("ghost_saved", []) == []
    page.click(".ghost-library-selection button:nth-child(2)")
    choose(page, "Ghost targets", "current")
    search(page, "Sideflip")
    until(page, "document.querySelectorAll('.ghost-library-row').length", 11)
    page.click(".ghost-library-selection button:first-child")
    click_race(page)
    until(page, "document.querySelector('.ghost-library') === null", True)
    # The catalog supplies race order; display sort is independently checked.
    expected = list(reversed(range(2, 23, 2)))
    assert state["session"].writes[-1] == {"ghosts": {"pb": True, "attempts": expected, "imports": []},
                                           "target": TARGET}
    assert [row["attempt_id"] for row in state["store"].get_state("ghost_saved")] == expected
    return exported, download


def imported_race(page, state, width):
    page.click(".ghost-library-selection button:nth-child(2)")
    choose(page, "Ghost targets", "current")
    # Imported practice rows stay available through an explicit filter, and
    # disappear when that filter is turned off.
    page.evaluate("document.querySelector('.ghost-library-filters input[type=checkbox]').click()")
    search(page, "René")
    until(page, "document.querySelectorAll('.ghost-library-row').length", 7)
    shown = rows(page)
    assert {row["creator"] for row in shown} == {"René the Very Patient Speedrunner"}
    assert {row["strategy"] for row in shown} == {TARGET["strat"], "Sideflip"}
    assert_fits_and_shoot(page, width, "creator-and-strategy")
    page.click(".ghost-library-selection button:first-child")
    expected = [row["id"] for row in state["entries"] if row["author"] == "René the Very Patient Speedrunner"
                and row["target"]["entity_key"] == TARGET["entity_key"] and not row["practice"]]
    click_race(page)
    until(page, "document.querySelector('.ghost-library') === null", True)
    assert state["session"].writes[-1] == {"ghosts": {"pb": True, "attempts": [], "imports": expected},
                                           "target": TARGET}
    assert len(state["session"].payload()["selection"]["playing"]) == 7
    assert not any("pb" in key for key in state["store"].writes)


def test_real_bulk_collection_flow_at_supported_widths(tmp_path):
    for width in WIDTHS:
        folder = tmp_path / str(width)
        with serve_collection(folder) as (base, state):
            with get_driver().launch() as page:
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-library-button")
                page.click(".ghost-library-button")
                exported, download = local_export_and_race(page, base, state, folder, width)
                page.click(".ghost-library-button")
                page.wait_for(".ghost-library-row")
                import_collection(page, exported, "Imported 23 ghosts")
                assert len(state["sharing"].imported_rows()) == 23
                assert {row["creator"] for row in rows(page)} == {"Local Runner"}
                import_collection(page, state["package"], f"Imported {FOREIGN_COUNT} ghosts")
                until(page, "document.querySelector('.ghost-library-creator').textContent.trim()", "Aster")
                first = rows(page)[:3]
                assert [(row["creator"], row["time"], row["strategy"]) for row in first] == [
                    ("Aster", format_igt_short(61), TARGET["strat"]),
                    ("René the Very Patient Speedrunner", format_igt_short(62), "Sideflip"),
                    ("Luigi Runner", format_igt_short(63), TARGET["strat"])]
                import_collection(page, state["package"], f"Imported 0 ghosts; {FOREIGN_COUNT} already in your library")
                assert len(state["sharing"].imported_rows()) == 23 + FOREIGN_COUNT
                assert not list((state["sharing"].root / "uploads").glob("*.upload"))
                imported_race(page, state, width)
                check_problems(page, download)


def test_race_keeps_library_open_when_target_changes_during_preservation(tmp_path):
    with serve_collection(tmp_path / "target-change") as (base, state):
        with get_driver().launch() as page:
            page.set_viewport(1500, 1200)
            page.goto(base)
            page.wait_for(".ghost-library-button")
            page.click(".ghost-library-button")
            page.wait_for(".ghost-library-row")
            selection = state["session"].payload()["selection"]["ghosts"]
            assert page.problems() == []
            preserve, targets = state["replay"].preserve_native, []

            def change_target_after_preserving(number):
                targets.append(state["session"]._read_target())
                answer = preserve(number)
                state["session"]._tracker.target = ("star", 2, 3)
                return answer

            state["replay"].preserve_native = change_target_after_preserving
            page.click(".ghost-library-row:first-child input")
            until(page, "document.querySelector('.ghost-library-count').textContent.trim()", "1 selected")
            click_race(page)
            error = "The practice target changed. Choose a ghost for the current target."
            until(page, "document.querySelector('.ghost-library [role=alert]')?.textContent.trim()", error)
            # The real PUT refused the stale target after the real preparation
            # saved its poses. The modal keeps both the selection and its error.
            assert targets == [TARGET]
            assert state["replay"].preserved == [1]
            assert state["session"].writes == []
            assert state["session"].payload()["selection"]["ghosts"] == selection
            assert rows(page)[0]["checked"]
            assert page.evaluate("document.querySelector('.ghost-library [role=alert]').getBoundingClientRect().height") > 0
            assert_fits_and_shoot(page, 1500, "target-changed")
            problems = page.problems()
            assert f"HTTP 422 {base}/api/ghost" in problems
            allowed = {f"HTTP 422 {base}/api/ghost",
                       "console.error: Failed to load resource: the server responded with a status of 422 (Unprocessable Content)",
                       "console.error: Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)"}
            assert len(problems) == 2 and all(problem in allowed for problem in problems), problems
