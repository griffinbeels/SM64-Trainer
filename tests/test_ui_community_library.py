"""Drive the actual Library and saved-rival routes without native capture or cloud IO."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))

pytestmark = pytest.mark.skipif(shutil.which("node") is None,reason="node not on PATH")
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING,allow_module_level=True)

import ui_fixture  # noqa: E402
from uilab import driver  # noqa: E402
from uilab.driver import default_wait_ms  # noqa: E402
from sm64_events.core import uilog  # noqa: E402
from tests.community_library_fixture import LibraryBrowser, LibrarySession, LONG_PLAYER, TARGET  # noqa: E402

SCREENSHOTS = ROOT/".tasks"/"m1-library"/"screenshots"
FILTER = ".library-page .library-ghost-filter input"
LIBRARY = ".app-sidebar .nav-item[title='Library']"
SURFACE = ".library-page .community-library"
RECORD = SURFACE+" .community-library-recording"
GROUP = SURFACE+" .community-library-group"


@pytest.fixture
def browser(tmp_path,monkeypatch):
    original = ui_fixture.create_app
    holder = {}

    def create(*args,**kwargs):
        session = LibrarySession(kwargs["service"])
        kwargs["ghost"] = session
        app = original(*args,**kwargs)

        @app.middleware("http")
        async def observe(request,call_next):
            if request.url.path.startswith(("/api/library/community","/api/ghost/rivals/saved")):
                session.requests.append((request.method,request.url.path,request.url.query))
            return await call_next(request)

        holder["session"],holder["service"] = session,kwargs["service"]
        return app

    monkeypatch.setattr(ui_fixture,"create_app",create)
    monkeypatch.setattr(uilog,"log_path",lambda:tmp_path/"ui_log.jsonl")
    with ui_fixture.serve_ui(seed=False,db_path=tmp_path/"practice.db") as base:
        session,service = holder["session"],holder["service"]
        ui_fixture._run_coro(service.set_target(2,1,"Standard"))
        with driver.get_driver().launch(headless=True,viewport=(1400,1000)) as page:
            page.goto(base+"/ui/index.html")
            try:
                page.wait_for(".log-list-card")
            except Exception:
                SCREENSHOTS.mkdir(parents=True,exist_ok=True)
                (SCREENSHOTS/"community-startup-failure.png").write_bytes(page.screenshot())
                print("Browser startup problems:",page.problems(),flush=True)
                print("Browser startup text:",page.evaluate("return document.body.innerText.slice(0,2500)"),flush=True)
                raise
            # A known navigation control proves event dispatch before Has ghost.
            page.click(LIBRARY)
            page.wait_for(".library-page .library-find-input")
            assert page.evaluate("return document.querySelector(\".nav-item[title='Library']\").getAttribute('aria-current')") == "page"
            yield LibraryBrowser(page,session,service,base,tmp_path/"ui_log.jsonl")


def click_text(page,selector,text):
    result = page.evaluate("const buttons=Array.from(document.querySelectorAll("+json.dumps(selector)+"));"
        "const button=buttons.find(row=>row.textContent.trim()==="+json.dumps(text)+");"
        "if (!button) return false;button.click();return true;")
    assert result,f"No {text!r} control in {selector}"


def show_ghosts(browser):
    page = browser.page
    page.click(FILTER)
    page.wait_for(GROUP)
    assert page.evaluate("return document.querySelector('.library-ghost-filter input').checked")


def open_recordings(browser):
    browser.page.click(GROUP+":first-child")
    browser.page.wait_for(RECORD)
    assert browser.page.count(RECORD)==5


def capture(page,name):
    SCREENSHOTS.mkdir(parents=True,exist_ok=True)
    page.evaluate("document.querySelector('.library-page').scrollIntoView({block:'start'})")
    page.wait_ms(100)
    (SCREENSHOTS/name).write_bytes(page.screenshot())


def assert_layout(page):
    result = page.evaluate("""
      const root=document.querySelector('.community-library');
      const outer=root.getBoundingClientRect();
      return Array.from(root.querySelectorAll('.community-library-recording, .community-library-actions button'))
        .map(el=>({name:el.className,box:el.getBoundingClientRect()}))
        .filter(({box})=>box.left<outer.left-1||box.right>outer.right+1||box.width<1)
        .map(({name,box})=>({name,left:box.left,right:box.right,outerLeft:outer.left,outerRight:outer.right}));
    """)
    assert result==[],result


def assert_rendered_log(browser):
    deadline = time.monotonic()+default_wait_ms()/1000
    while time.monotonic()<deadline:
        entries = uilog.read(browser.log_path)
        if any(entry["surface"]=="community_library" and any(row.get("name")==LONG_PLAYER
               and row.get("time") and row.get("strategy") for row in entry.get("rows",[])) for entry in entries):
            return
        browser.page.wait_ms(100)
    pytest.fail("Rendered community player/time/strategy never reached isolated ui_log.jsonl")


@pytest.mark.parametrize("width",[1400,850])
def test_discover_save_refresh_restore_and_remove_without_changing_practice(browser,width):
    page,session = browser.page,browser.session
    page.set_viewport(width,1100)
    before = browser.practice_snapshot()
    show_ghosts(browser)
    assert page.count(GROUP)==2
    assert page.evaluate("return document.querySelector('.community-library').textContent.includes('Community-only method')")
    open_recordings(browser)
    assert page.evaluate("return document.querySelector('.community-library-player strong').textContent") == LONG_PLAYER
    assert page.count(RECORD+" video")==0
    assert_rendered_log(browser)
    capture(page,f"community-recordings-{width}.png")
    assert_layout(page)
    page.click(RECORD+":first-child .community-library-actions button")
    page.wait_for(RECORD+":first-child .community-library-saved")
    assert session._rivals.pins[session._rivals._scope_key(TARGET,"overall")] == {"0":session.rows[0]["id"]}
    assert browser.practice_snapshot()==before
    # Header Refresh remounts the leaf: Remove must derive from persisted pins.
    click_text(page,".library-page .workshop-hero-actions button","Refresh")
    page.wait_for(GROUP)
    open_recordings(browser)
    assert page.evaluate("return document.querySelector('.community-library-actions button').textContent.trim()") == "Remove"
    page.click(RECORD+":first-child .community-library-actions button")
    page.wait_ms(160)
    assert page.evaluate("return document.querySelector('.community-library-actions button').textContent.trim()") == "Add ghost"
    assert session._rivals.pins[session._rivals._scope_key(TARGET,"overall")] == {}
    assert browser.practice_snapshot()==before
    assert page.problems()==[]


def test_full_pins_picker_cancel_is_local_then_explicit_replacement_preserves_hidden_pin(browser):
    page,session = browser.page,browser.session
    rivals = session._rivals
    rivals.options["count"] = 2
    key = rivals._scope_key(TARGET,"overall")
    rivals.pins[key] = {"0":session.rows[1]["id"],"1":session.rows[2]["id"],"3":session.rows[3]["id"]}
    rivals._store()
    before = deepcopy(rivals.pins),browser.practice_snapshot()
    show_ghosts(browser)
    open_recordings(browser)
    page.click(RECORD+":first-child .community-library-actions button")
    page.wait_for(".community-library-seats")
    assert page.count(".community-library-seat")==4
    assert page.count(".community-library-seat:disabled")==2
    capture(page,"community-full-pins-1400.png")
    click_text(page,".modal-actions button","Cancel")
    page.wait_ms(120)
    assert page.count(".community-library-seats")==0
    assert (rivals.pins,browser.practice_snapshot())==before
    page.click(RECORD+":first-child .community-library-actions button")
    page.wait_for(".community-library-seats")
    page.click(".community-library-seat:nth-child(3)")
    page.wait_for(RECORD+":first-child .community-library-saved")
    assert rivals.pins[key] == {"0":session.rows[1]["id"],"1":session.rows[0]["id"],"3":session.rows[3]["id"]}
    assert browser.practice_snapshot()==before[1]
    assert page.problems()==[]


def test_replacing_us_pin_with_jp_updates_both_region_views_of_the_same_lineup(browser):
    page,session = browser.page,browser.session
    jp = {**deepcopy(session.rows[0]),"id":"a"*64,"game_version":"jp",
          "player_id":"33333333-3333-4333-8333-333333333333","player_name":"JP Community Runner",
          "ghost":SimpleNamespace(origin="ghost:"+"a"*64)}
    session.rows.append(jp)
    session._rivals.options["count"] = 1
    before = browser.practice_snapshot()
    show_ghosts(browser)

    def open_region(region):
        found = page.evaluate("const rows=Array.from(document.querySelectorAll('.community-library-group'));"
            "const row=rows.find(item=>item.textContent.includes("+json.dumps(TARGET["strat"])+")"
            "&&item.querySelector('img.region-flag')?.alt==="+json.dumps(region)+");"
            "if(!row)return false;row.click();return true;")
        assert found,region
        page.wait_for(RECORD)

    def back():
        click_text(page,".community-library-header button","Back to ghost targets")
        page.wait_for(GROUP)

    open_region("US")
    page.click(RECORD+":first-child .community-library-actions button")
    page.wait_for(RECORD+":first-child .community-library-saved")
    back()
    open_region("JP")
    page.click(RECORD+":first-child .community-library-actions button")
    page.wait_for(".community-library-seats")
    page.click(".community-library-seat:nth-child(2)")
    page.wait_for(RECORD+":first-child .community-library-saved")
    assert session._rivals.pins[session._rivals._scope_key(TARGET,"overall")] == {"0":jp["id"]}
    back()
    open_region("US")
    assert page.evaluate("return document.querySelector('.community-library-actions button').textContent.trim()") == "Add ghost"
    back()
    open_region("JP")
    assert page.evaluate("return document.querySelector('.community-library-actions button').textContent.trim()") == "Remove"
    assert browser.practice_snapshot()==before
    assert page.problems()==[]


def test_target_region_hidden_and_feedback_states_reach_real_endpoints(browser):
    page,session = browser.page,browser.session
    # Reach a target through the existing Sheet search before selecting ghosts.
    page.evaluate("const box=document.querySelector('.library-find-input');box.value='caged';box.dispatchEvent(new Event('input',{bubbles:true}));")
    page.wait_for(".library-result")
    page.click(".library-result:first-child")
    page.wait_for(".library-target")
    show_ghosts(browser)
    assert page.count(GROUP)==1
    assert session._community.calls[-1][0]=="summary"
    assert session._community.calls[-1][2]["entity_key"]=="2-4"
    # Both regions start visible; hiding JP asks the actual service for US.
    region_start = len(session._community.calls)
    page.click(".library-page .version-switch[aria-label='Regions shown'] button[aria-label='JP']")
    # This remount schedules its read after paint. Wait for the US answer's
    # rendered acknowledgement, rather than sampling the previous request.
    page.wait_for(SURFACE+"[data-entity='star:2:4'][data-rom='us'][aria-busy='false'] .community-library-group")
    reads = session._community.calls[region_start:]
    assert reads and all(call[0]=="summary" and call[2]["entity_key"]=="2-4" and call[3]=="us" for call in reads),reads
    assert any(path=="/api/library/community" and "rom=us" in query for _method,path,query in session.requests)
    before = len(session._community.calls)
    page.click(".app-sidebar .nav-item[title='Practice']")
    page.wait_ms(700)
    assert len(session._community.calls)==before
    page.click(LIBRARY)
    page.wait_for(GROUP)
    session._community.mode = "empty"
    click_text(page,".library-page .workshop-hero-actions button","Refresh")
    page.wait_for(".community-library-empty")
    assert "No shared ghost targets" in page.evaluate("return document.querySelector('.community-library-empty').textContent")
    session._community.state["status"] = "signed_out"
    click_text(page,".library-page .workshop-hero-actions button","Refresh")
    page.wait_for(".community-library-feedback")
    assert "Sign in" in page.evaluate("return document.querySelector('.community-library-feedback').textContent")
    session._community.state["status"] = "active"
    session._community.mode = "offline"
    click_text(page,".community-library-feedback button","Retry")
    page.wait_for(".community-library-feedback [role='alert']")
    assert "could not be reached" in page.evaluate("return document.querySelector('.community-library-feedback').textContent")
    # Deliberately induced HTTP503 is expected; no unrelated runtime fault is.
    expected = page.problems()
    assert expected and all("503" in problem for problem in expected),expected
    session._community.mode = "online"
    click_text(page,".community-library-feedback button","Retry")
    page.wait_for(GROUP)
    assert page.problems()==[]
