"""Claimed player pages and consent through real app routes and ProgressSync."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import time

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
from sm64_events.core import paths, uilog  # noqa: E402
from sm64_events.community.progress_state import PROFILE_KEY  # noqa: E402
from tests.community_library_fixture import LibrarySession  # noqa: E402
from tests.player_pages_fixture import Account, INSTALLATION, LONG_PLAYER, PROFILE, PlayerBrowser, RIVAL, SAVED, UID  # noqa: E402

SCREENSHOTS = ROOT/".tasks"/"m2-player-pages"/"screenshots"
PLAYER = ".player-page"
ACCOUNT = ".modal:has(.progress-settings)"
PUBLICATION = ".modal:has(> .modal-body > .progress-sharing-preview)"
NAV = ".app-sidebar .nav-item"


@pytest.fixture
def browser(tmp_path,monkeypatch):
    original,holder = ui_fixture.create_app,{}
    account = Account()

    def create(*args,**kwargs):
        service = kwargs["service"]
        service.db.set_state(PROFILE_KEY,PROFILE)
        service.db.insert_pb(2,4,"TJ Owlless","igt",360,None,SAVED,imported_from="sheet:fixture",game_version="us")
        service.db.insert_pb(2,0,"Standard","igt",540,None,SAVED,imported_from="manual",game_version=None)
        session = LibrarySession(service)
        kwargs.update(ghost=session,community_account=account)
        app = original(*args,**kwargs)
        app.state.progress._installation = INSTALLATION
        account.seed(service,session)
        holder.update(service=service,session=session,progress=app.state.progress)

        @app.get("/api/replay/available")
        def inactive_replays():
            # This fixture has no recorder/replay service. Match that real
            # read model with an empty inventory instead of a browser 404.
            return {"available":[]}

        @app.middleware("http")
        async def observe(request,call_next):
            if request.method not in ("GET","HEAD","OPTIONS") and request.url.path!="/api/uilog":
                session.requests.append((request.method,request.url.path))
            return await call_next(request)

        return app

    monkeypatch.setattr(ui_fixture,"create_app",create)
    monkeypatch.setattr(paths,"progress_installation_path",lambda:tmp_path/"installation-id")
    monkeypatch.setattr(uilog,"log_path",lambda:tmp_path/"ui_log.jsonl")
    with ui_fixture.serve_ui(seed=False,db_path=tmp_path/"practice.db") as base:
        ui_fixture._run_coro(holder["service"].set_target(2,1,"Standard"))
        with driver.get_driver().launch(headless=True,viewport=(1400,1100)) as page:
            page.goto(base+"/ui/index.html")
            page.wait_for(".log-list-card")
            try:
                yield PlayerBrowser(page,base,holder["service"],holder["session"],account,holder["progress"],tmp_path/"ui_log.jsonl")
            finally:
                problems = page.problems()
                if problems:
                    SCREENSHOTS.mkdir(parents=True,exist_ok=True)
                    (SCREENSHOTS/"player-pages-failure.png").write_bytes(page.screenshot())
                    print("Player-page browser problems:",problems,flush=True)
                    print("Player-page text:",page.evaluate("return document.body.innerText.slice(-6000)"),flush=True)
                    print("Offline account calls:",[(method,path) for method,path,_body,_params in account.calls],flush=True)
                assert problems==[],problems
    assert not (tmp_path/"installation-id").exists(),"Injected installation must not write external identity state"


def click_text(page,selector,text):
    result = page.evaluate("const items=Array.from(document.querySelectorAll("+json.dumps(selector)+"));"
        "const item=items.find(row=>row.textContent.trim()==="+json.dumps(text)+");"
        "if(!item)return false;item.click();return true;")
    assert result,(selector,text)


def capture(page,name,selector=PLAYER):
    SCREENSHOTS.mkdir(parents=True,exist_ok=True)
    page.evaluate("document.querySelector("+json.dumps(selector)+").scrollIntoView({block:'start'})")
    page.wait_ms(100)
    (SCREENSHOTS/name).write_bytes(page.screenshot())


def open_search(browser, *, course=False):
    page = browser.page
    page.click(NAV+"[title='Rank']")
    page.wait_for(".player-search input")
    if course:
        page.evaluate("const pick=document.querySelector('.rank-page .route-focus-control select');"
            "pick.value='course:2';pick.dispatchEvent(new Event('change',{bubbles:true}));")
        page.wait_ms(150)
    page.evaluate("const box=document.querySelector('.player-search input');box.value="+json.dumps(LONG_PLAYER)+";"
        "box.dispatchEvent(new Event('input',{bubbles:true}));")
    page.wait_for(".player-search-results button")
    page.click(".player-search-results button")
    page.wait_for(PLAYER+" .player-ghost-row")


def open_account(browser):
    page = browser.page
    page.click(".app-sidebar .settings-link")
    page.wait_for(".settings-drawer")
    click_text(page,".settings-section-head button","Account")
    page.wait_for(ACCOUNT+" .progress-sharing-controls")


def consent(page,label):
    page.wait_for(PUBLICATION)
    page.click(PUBLICATION+" > .modal-body > .community-check input")
    click_text(page,PUBLICATION+" > .modal-actions button",label)


def assert_no_viewer_writes(browser):
    forbidden = {"/api/target","/api/scorecard/goal","/api/scorecard/regions","/api/marelo/exclude","/api/ranks/mode"}
    assert not [request for request in browser.session.requests if request[1] in forbidden]


def assert_rendered_log(browser,surface,predicate):
    deadline = time.monotonic()+default_wait_ms()/1000
    while time.monotonic()<deadline:
        if any(entry.get("surface")==surface and predicate(entry) for entry in uilog.read(browser.log_path)):
            return
        browser.page.wait_ms(100)
    pytest.fail(f"Rendered {surface} facts never reached the isolated UI log")


@pytest.mark.parametrize("width",[1400,850])
def test_claimed_page_preserves_viewer_scope_and_practice_with_faster_pb_slower_ghost(browser,width):
    page = browser.page
    page.set_viewport(width,1100)
    before = browser.practice()
    open_search(browser,course=True)
    assert page.evaluate("return document.querySelector('.player-comparison').dataset.scope")=="course:2"
    assert page.evaluate("return document.querySelector('.player-page').dataset.playerId")==RIVAL
    assert page.evaluate("return document.querySelector('.player-profile-header h2').textContent")==LONG_PLAYER
    assert page.count(".player-claimed")==1
    assert page.count(".player-scorecard .score-line")==6
    assert page.count(".player-scorecard .score-row-remove,.player-scorecard .score-detail-goal-btn,.player-scorecard .good,.player-scorecard .bad")==0
    assert '11"00 saved PB in this comparison' in page.evaluate("return document.querySelector('.player-ghosts').textContent")
    assert '20"00 recorded' in page.evaluate("return document.querySelector('.player-ghosts').textContent")
    assert_rendered_log(browser,"player_page",lambda entry: entry["title"]==LONG_PLAYER and any(
        row.get("name")=="Fall onto the Caged Island" and row.get("time")=='12"00'
        and row.get("player")=='11"00' and row.get("delta")=="+1.00" for row in entry.get("rows",[])))
    capture(page,f"player-page-{width}.png")
    capture(page,f"player-ghosts-{width}.png",".player-ghosts")
    assert page.evaluate("const root=document.querySelector('.player-page');return root.scrollWidth<=root.clientWidth+1")
    page.click(".player-ghost-row button")
    page.wait_ms(150)
    assert page.evaluate("return document.querySelector('.player-ghost-row button').textContent")=="Remove"
    assert browser.practice()==before
    page.click(".player-ghost-row button")
    page.wait_ms(150)
    assert page.evaluate("return document.querySelector('.player-ghost-row button').textContent")=="Add rival"
    # The second entry door is the actual player name on a Library recording.
    page.click(NAV+"[title='Library']")
    page.wait_for(".library-ghost-filter input")
    page.click(".library-ghost-filter input")
    page.wait_for(".community-library-group")
    page.click(".community-library-group:first-child")
    page.wait_for(".community-library-player button.textlink")
    page.click(".community-library-recording:first-child .community-library-player button")
    page.wait_for(PLAYER+" .player-ghost-row")
    assert page.evaluate("return document.querySelector('.player-page').dataset.playerId")==RIVAL
    assert browser.practice()==before
    assert_no_viewer_writes(browser)
    assert page.problems()==[]


def test_explicit_publication_cancel_enable_self_preview_pause_and_withdraw(browser):
    page = browser.page
    before = browser.practice()
    open_account(browser)
    assert_rendered_log(browser,"progress_publication",lambda entry: "No saved PBs are currently public" in entry.get("note",""))
    page.evaluate("const bio=document.querySelector('.progress-profile-details textarea');bio.value='Practice with curiosity.';bio.dispatchEvent(new Event('input',{bubbles:true}));")
    page.wait_ms(50)
    page.evaluate("const link=document.querySelectorAll('.progress-profile-details input[type=url]')[0];link.value='https://www.twitch.tv/fixture_player';link.dispatchEvent(new Event('input',{bubbles:true}));")
    click_text(page,ACCOUNT+" button","Save public details")
    page.wait_for(".progress-profile-details [role='status']")
    assert browser.account.profile["bio"]=="Practice with curiosity."
    assert browser.account.profile["socials"]==[{"kind":"twitch","url":"https://www.twitch.tv/fixture_player"}]
    assert all(path=="/v2/account/player-profile" for path,_body in browser.account.writes)
    details_writes = len(browser.account.writes)
    page.set_viewport(850,1100)
    capture(page,"progress-settings-850.png",".progress-settings")
    assert page.evaluate("const el=document.querySelector('.progress-settings');return el.scrollWidth<=el.clientWidth+1")
    page.set_viewport(1400,1100)
    click_text(page,ACCOUNT+" button","Share saved PBs")
    page.wait_for(".progress-sharing-preview")
    assert page.evaluate("return document.querySelector("+json.dumps(PUBLICATION+" > .modal-actions .primary-button")+").disabled")
    click_text(page,PUBLICATION+" > .modal-actions button","Cancel")
    assert len(browser.account.writes)==details_writes
    click_text(page,ACCOUNT+" button","Share saved PBs")
    consent(page,"Share saved PBs")
    page.wait_for(ACCOUNT+" button:has-text('Pause PB updates')")
    assert browser.account.publication["visible"] and UID in browser.account.snapshots
    assert [path for path,_ in browser.account.writes[details_writes:]]==["/v2/account/progress/control","/v2/account/progress/snapshot"]
    click_text(page,ACCOUNT+" button","Preview shared page")
    page.wait_for(PLAYER+" .player-scorecard")
    assert page.evaluate("return document.querySelector('.player-page').dataset.playerId")==UID
    shared = page.evaluate("return Array.from(document.querySelectorAll('.player-scorecard .score-line .score-gap')).map(el=>el.textContent.trim()).filter(value=>value!=='—')")
    assert len(shared)==2 and set(shared)=={"+0.00"}
    assert page.count(".player-scorecard .score-card")==17
    capture(page,"player-self-overall-1400.png")
    assert browser.practice()==before
    open_account(browser)
    click_text(page,ACCOUNT+" button","Pause PB updates")
    page.wait_ms(180)
    assert not browser.account.publication["enabled"] and browser.account.publication["visible"]
    click_text(page,ACCOUNT+" button","Remove shared PBs")
    page.wait_for(".modal:has(.modal-heading h2)")
    click_text(page,".modal-actions button","Remove shared PBs")
    page.wait_ms(180)
    assert not browser.account.publication["visible"] and UID not in browser.account.snapshots
    assert browser.practice()==before
    assert_no_viewer_writes(browser)
    assert page.problems()==[]


def test_publication_conflict_requires_a_fresh_deliberate_publisher_switch(browser):
    page = browser.page
    open_account(browser)
    click_text(page,ACCOUNT+" button","Share saved PBs")
    page.wait_for(".progress-sharing-preview")
    browser.account.external_publisher()
    consent(page,"Share saved PBs")
    page.wait_for(ACCOUNT+" [role='alert']")
    assert len(browser.account.writes)==1
    expected = page.problems()
    assert expected and all("409" in problem for problem in expected),expected
    click_text(page,ACCOUNT+" button","Publish from this profile")
    page.wait_for(".progress-sharing-preview")
    before = deepcopy(browser.account.publication)
    capture(page,"publishing-switch-preview-1400.png",PUBLICATION)
    click_text(page,PUBLICATION+" > .modal-actions button","Cancel")
    assert browser.account.publication==before
    click_text(page,ACCOUNT+" button","Publish from this profile")
    consent(page,"Use this profile")
    page.wait_for(ACCOUNT+" button:has-text('Pause PB updates')")
    assert browser.account.publication["publisher"]["installation_id"]==INSTALLATION
    assert browser.account.publication["visible"]
    assert page.problems()==[]


def test_partial_private_and_offline_are_distinct_read_states(browser):
    page = browser.page
    browser.account.partial=True
    open_search(browser)
    assert "Partial publication" in page.evaluate("return document.querySelector('.player-profile-header').textContent")
    assert "complete published rank cannot be shown" in page.evaluate("return document.querySelector('.player-profile-header').textContent")
    browser.account.private=True
    click_text(page,PLAYER+" button","Refresh shared page")
    page.wait_ms(180)
    assert page.count(".player-scorecard")==0 and page.count(".player-ghost-row")==1
    assert "has not shared saved PBs" in page.evaluate("return document.querySelector('.player-profile-header').textContent")
    browser.account.offline=True
    click_text(page,PLAYER+" button","Refresh shared page")
    page.wait_for(PLAYER+" [role='alert']")
    expected = page.problems()
    assert expected and all("503" in problem for problem in expected),expected
    browser.account.offline=False
    click_text(page,PLAYER+" button","Retry")
    page.wait_ms(180)
    assert page.count(PLAYER+" [role='alert']")==0
    assert page.problems()==[]


def test_lapsed_owner_can_export_pause_and_remove_a_hidden_stored_snapshot(browser):
    page = browser.page
    browser.account.retain_owner_snapshot(browser.service)
    assert browser.account.publication["content_hash"] and not browser.account.publication["visible"]
    browser.account.state["status"] = "suspended"
    browser.account._epoch += 1
    first_call = len(browser.account.calls)
    before = browser.practice()
    page.goto(browser.base+"/ui/index.html")
    page.wait_for(".log-list-card")
    page.set_viewport(850,1100)
    page.click(".app-sidebar .settings-link")
    page.wait_for(".settings-drawer")
    click_text(page,".settings-section-head button","Account")
    page.wait_for(".progress-privacy-state")
    assert page.count(".progress-settings")==0
    assert "not visible on your player page" in page.evaluate("return document.querySelector('.progress-privacy').textContent")
    capture(page,"privacy-lapsed-850.png",".progress-privacy")
    click_text(page,".progress-privacy button","Pause PB updates")
    page.wait_for(".progress-privacy [role='status']:has-text('PB updates paused.')")
    assert not browser.account.publication["enabled"] and browser.account.publication["content_hash"]
    click_text(page,".progress-privacy button","Export account data")
    page.wait_for(".progress-privacy [role='status']:has-text('Account export prepared.')")
    assert any(path=="/v2/account/player-export" for _method,path,_body,_params in browser.account.calls[first_call:])
    click_text(page,".progress-privacy button","Remove stored PBs")
    page.wait_for(".modal-actions button:has-text('Remove stored PBs')")
    click_text(page,".modal-actions button","Cancel")
    assert browser.account.publication["content_hash"]
    click_text(page,".progress-privacy button","Remove stored PBs")
    click_text(page,".modal-actions button","Remove stored PBs")
    page.wait_for(".progress-privacy-state p:has-text('No saved-PB snapshot is stored')")
    assert browser.account.publication["content_hash"] is None and UID not in browser.account.snapshots
    assert browser.practice()==before
    calls = browser.account.calls[first_call:]
    assert all(path in ("/v2/account/progress","/v2/account/progress/control","/v1/account/export","/v2/account/player-export")
               for _method,path,_body,_params in calls)
    assert [body["action"] for method,path,body,_params in calls if method=="PUT"]==["disable","withdraw"]
    assert page.problems()==[]
