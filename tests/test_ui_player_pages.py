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
from sm64_events.community.progress_snapshot import resolve_rows, unavailable_segment_keys  # noqa: E402
from sm64_events.server.player_comparison import compare  # noqa: E402
from tests.community_library_fixture import LibrarySession  # noqa: E402
from tests.player_pages_fixture import Account, INSTALLATION, LONG_PLAYER, OWN_RECORDING, PROFILE, PlayerBrowser, RIVAL, SAVED, UID  # noqa: E402

SCREENSHOTS = ROOT/".tasks"/"m2-player-pages"/"screenshots"
PLAYER = ".player-page"
ACCOUNT = ".modal:has(.progress-settings)"
PUBLICATION = ".modal:has(> .modal-body > .progress-sharing-preview)"
NAV = ".app-sidebar .nav-item"


@pytest.fixture
def browser(tmp_path,monkeypatch,request):
    original,holder = ui_fixture.create_app,{}
    account = Account(virgin=getattr(request,"param",{}).get("virgin",False))

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
    # Real startup reconciles canonical definitions before publishing. Legacy
    # bootstrap definitions otherwise look like edited/private ranked segments.
    with ui_fixture.serve_ui(seed=False,db_path=tmp_path/"practice.db",reconcile_full_corpus=True) as base:
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


def rank_shape(page):
    """Compare the familiar rendered Rank surface through both real entry points."""
    return page.evaluate("const card=document.querySelector('.rank-card');return {"
        "padding:getComputedStyle(card).padding,"
        "mainDisplay:getComputedStyle(card.querySelector('.rank-card-main')).display,"
        "mainGap:getComputedStyle(card.querySelector('.rank-card-main')).gap,"
        "bands:card.querySelectorAll('.rank-band').length,"
        "factors:card.querySelectorAll('.rank-factors > .rank-factor').length,"
        "tiles:card.querySelectorAll('.rank-coverage .entity-tile').length,"
        "rows:document.querySelectorAll('.rank-breakdown tbody > tr').length}")


def card_edges(page):
    return page.evaluate("return Array.from(document.querySelectorAll('.player-page .practice-card')).map(card=>{"
        "const box=card.getBoundingClientRect();return {name:card.className,left:box.left,right:box.right,width:box.width};})")


def verify_scorecard_name(page,width):
    assert page.count(".player-scorecard .score-line")==6
    assert page.evaluate("return Array.from(document.querySelectorAll('.player-scorecard .score-label-goal')).map(el=>el.textContent)")==[LONG_PLAYER]
    capture(page,f"player-scorecard-name-beta24-{width}.png",".player-scorecard")


def assert_card_alignment(edges):
    assert len(edges)==5,edges
    assert max(row["left"] for row in edges)-min(row["left"] for row in edges)<=1,edges
    assert max(row["right"] for row in edges)-min(row["right"] for row in edges)<=1,edges


def verify_card_alignment_guard(page,width):
    """The actual five cards must align; removing the shipped fix must fail."""
    aligned = card_edges(page)
    assert_card_alignment(aligned)
    removed = page.evaluate("for(const sheet of document.styleSheets){const rules=Array.from(sheet.cssRules);"
        "const index=rules.findIndex(rule=>rule.selectorText==='.player-page .practice-card');"
        "if(index>=0){window.__playerAlignmentRule={sheet,index,text:rules[index].cssText};sheet.deleteRule(index);return true;}}return false")
    assert removed,"The scoped alignment rule was not reached"
    try:
        broken = card_edges(page)
        with pytest.raises(AssertionError):
            assert_card_alignment(broken)
    finally:
        page.evaluate("const saved=window.__playerAlignmentRule;saved.sheet.insertRule(saved.text,saved.index);delete window.__playerAlignmentRule")
    assert_card_alignment(card_edges(page))
    (SCREENSHOTS/f"player-card-edges-round2-{width}.json").write_text(
        json.dumps({"aligned":aligned,"without_scoped_rule":broken},indent=2),encoding="utf-8")


def checkbox_alignment(page):
    return page.evaluate("return Array.from(document.querySelectorAll('.community-check')).filter(label=>label.getBoundingClientRect().height).map(label=>{"
        "const input=label.querySelector('input'),span=label.querySelector('span');"
        "const text=Array.from(span.childNodes).find(node=>node.nodeType===Node.TEXT_NODE&&node.textContent.trim());"
        "const range=document.createRange();range.selectNodeContents(text);const lines=Array.from(range.getClientRects());"
        "const box=input.getBoundingClientRect(),first=lines[0],style=getComputedStyle(input);return {text:text.textContent.trim(),lines:lines.length,"
        "offset:Math.abs(box.top+box.height/2-first.top-first.height/2),input:{top:box.top,height:box.height,marginTop:style.marginTop,"
        "padding:style.padding,lineHeight:style.lineHeight,boxSizing:style.boxSizing},first:{top:first.top,height:first.height}};})")


def verify_checkbox_alignment(page):
    geometry = checkbox_alignment(page)
    capture(page,"progress-checkbox-current-round2-850.png",".community-form > .community-check")
    (SCREENSHOTS/"progress-checkbox-current-round2-850.json").write_text(json.dumps(geometry,indent=2),encoding="utf-8")
    assert len(geometry)==2 and all(row["offset"]<=1.5 for row in geometry),geometry
    page.evaluate("for(const span of document.querySelectorAll('.community-check > span'))span.style.maxWidth='180px'")
    wrapped = checkbox_alignment(page)
    assert all(row["lines"]>=2 and row["offset"]<=1.5 for row in wrapped),wrapped
    capture(page,"progress-checkbox-wrapped-round2-850.png",".progress-settings")
    page.evaluate("for(const span of document.querySelectorAll('.community-check > span'))span.style.removeProperty('max-width')")
    (SCREENSHOTS/"progress-checkbox-geometry-round2-850.json").write_text(
        json.dumps({"normal":geometry,"wrapped":wrapped},indent=2),encoding="utf-8")


def assert_own_row_disabled(browser,row,button="button"):
    page = browser.page
    selector = row+" "+button
    assert page.evaluate("return document.querySelector("+json.dumps(selector)+").disabled")
    assert "PB ghost is included automatically" in page.evaluate("return document.querySelector("+json.dumps(row)+").textContent")
    writes_before = len(browser.session.requests)
    page.evaluate("document.querySelector("+json.dumps(selector)+").click()")
    assert len(browser.session.requests)==writes_before
    assert page.count(row+" .community-library-unavailable")==0
    verify_own_tooltip(page,row)


def verify_own_tooltip(page,row):
    hint = row+" .community-library-add-hint"
    accessible = page.evaluate("const hint=document.querySelector("+json.dumps(hint)+");return {tab:hint.tabIndex,"
        "reason:document.getElementById(hint.getAttribute('aria-describedby')).textContent}")
    assert accessible=={"tab":0,"reason":"Your PB ghost is included automatically."}
    width = page.evaluate("return window.innerWidth")
    surface = "player" if row.startswith(".player") else "library"
    page.hover(row+" .community-library-performance")
    assert page.evaluate("return getComputedStyle(document.querySelector("+json.dumps(hint)+"),'::after').visibility")=="hidden"
    page.hover(hint)
    page.wait_ms(150)
    assert_tooltip_visible(page,hint)
    (SCREENSHOTS/f"own-{surface}-tooltip-hover-round3-{width}.png").write_bytes(page.screenshot())
    page.hover(row+" .community-library-performance")
    page.evaluate("document.querySelector("+json.dumps(hint)+").focus({focusVisible:true})")
    page.wait_ms(150)
    assert page.evaluate("return document.activeElement===document.querySelector("+json.dumps(hint)+")")
    assert_tooltip_visible(page,hint)
    (SCREENSHOTS/f"own-{surface}-tooltip-focus-round3-{width}.png").write_bytes(page.screenshot())
    page.evaluate("document.activeElement.blur()")


def assert_tooltip_visible(page,hint):
    result = page.evaluate("const hint=document.querySelector("+json.dumps(hint)+");const css=getComputedStyle(hint,'::after');"
        "const box=hint.getBoundingClientRect(),n=name=>parseFloat(css.getPropertyValue(name))||0;"
        "const width=n('width')+(css.boxSizing==='border-box'?0:n('padding-left')+n('padding-right')+n('border-left-width')+n('border-right-width'));"
        "const shift=css.transform==='none'?0:new DOMMatrix(css.transform).m41;"
        "const rawLeft=parseFloat(css.left),anchor=Number.isFinite(rawLeft)?box.left+rawLeft:box.right-n('right')-width;"
        "const left=anchor+shift;return {visibility:css.visibility,opacity:css.opacity,text:css.content,"
        "cssLeft:css.left,cssRight:css.right,transform:css.transform,focus:document.activeElement===hint,"
        "rules:Array.from(document.querySelectorAll('style')).flatMap(tag=>Array.from(tag.sheet.cssRules)).filter(rule=>rule.selectorText?.includes('community-library-add-hint')).map(rule=>rule.cssText),"
        "left,right:left+width,viewport:document.documentElement.clientWidth,windowWidth:window.innerWidth}")
    name = "player" if hint.startswith(".player") else "library"
    (SCREENSHOTS/f"own-{name}-tooltip-metrics-round3-{result['windowWidth']}.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    assert result["visibility"]=="visible" and float(result["opacity"])>.99,result
    assert "Your PB ghost is included automatically." in result["text"]
    assert result["left"]>=0 and result["right"]<=result["viewport"]+1,result


def own_course_rank(page,width):
    page.click(NAV+"[title='Rank']")
    page.wait_for(".rank-card .route-focus-control select")
    page.evaluate("const pick=document.querySelector('.rank-card select');"
        "pick.value='course:2';pick.dispatchEvent(new Event('change',{bubbles:true}));")
    page.wait_for(".scorecard-card[data-scope='course:2'] .score-line")
    page.wait_for(".rank-card .rank-ladder")
    result = rank_shape(page)
    assert result["bands"]==9 and result["factors"]==2 and result["tiles"]>=6
    capture(page,f"own-rank-round3-{width}.png",".rank-card")
    return result


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


def set_pb_sharing(page,enabled):
    selector = ACCOUNT+" .progress-sharing-controls > .community-check input"
    if page.evaluate("return document.querySelector("+json.dumps(selector)+").checked") != enabled:
        page.click(selector)
    page.wait_for(selector+(":checked" if enabled else ":not(:checked)"))
    page.wait_for(selector+":not(:disabled)")
    page.wait_for(ACCOUNT+(" button:has-text('Sync now')" if enabled
        else " .progress-publication-summary p:has-text('PB updates are paused')"))


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
    own_shape = own_course_rank(page,width)
    open_search(browser,course=True)
    capture(page,f"player-page-round3-{width}.png")
    capture(page,f"player-rank-round3-{width}.png",".rank-card")
    verify_card_alignment_guard(page,width)
    assert page.evaluate("return document.querySelector('.player-comparison').dataset.scope")=="course:2"
    assert page.evaluate("return document.querySelector('.player-page').dataset.playerId")==RIVAL
    assert page.evaluate("return document.querySelector('.player-profile-header h2').textContent")==LONG_PLAYER
    assert page.count(".player-claimed")==1
    assert rank_shape(page)==own_shape
    assert page.count(PLAYER+" .scope-chip")>=1
    assert page.evaluate("return document.querySelector('.rank-ladder-you').textContent")=="PLAYER"
    assert "with published PBs" in page.evaluate("return document.querySelector('.rank-factors').textContent")
    assert "not run yet" not in page.evaluate("return document.querySelector('.rank-factors').textContent")
    assert page.evaluate("return Array.from(document.querySelectorAll('.rank-breakdown .unpracticed .rank-cell-next')).every(cell=>cell.textContent==='no published PB')")
    assert page.count(PLAYER+" .rank-coverage .entity-tile:not(.is-static),"+PLAYER+" .rank-coverage .editicon")==0
    assert page.count(PLAYER+" .rank-breakdown tbody button.chip")==0
    assert "Ignore" not in page.evaluate("return document.querySelector('.rank-breakdown').textContent")
    verify_scorecard_name(page,width)
    assert page.count(".player-scorecard .score-row-remove,.player-scorecard .score-detail-goal-btn,.player-scorecard .good,.player-scorecard .bad")==0
    assert page.evaluate("return document.querySelector('.player-ghost-row .community-library-performance').textContent.trim()")== '20"00'
    ghost_words = page.evaluate("return document.querySelector('.player-ghosts').textContent")
    assert "saved PB in this comparison" not in ghost_words and "Recording times may differ" not in ghost_words
    assert_rendered_log(browser,"player_page",lambda entry: entry["title"]==LONG_PLAYER and any(
        row.get("name")=="Fall onto the Caged Island" and row.get("time")=='12"00'
        and row.get("player")=='11"00' and row.get("delta")=="+1.00" for row in entry.get("rows",[])))
    capture(page,f"player-ghosts-round3-{width}.png",".player-ghosts")
    assert page.evaluate("const root=document.querySelector('.player-page');return root.scrollWidth<=root.clientWidth+1")
    page.click(".player-ghost-row button")
    page.wait_ms(150)
    assert page.evaluate("return document.querySelector('.player-ghost-row button').textContent")=="Remove"
    assert browser.practice()==before
    page.click(".player-ghost-row button")
    page.wait_ms(150)
    assert page.evaluate("return document.querySelector('.player-ghost-row button').textContent")=="Add ghost"
    # The second entry door is the actual player name on a Library recording.
    page.click(NAV+"[title='Library']")
    page.wait_for(".library-ghost-filter input")
    page.click(".library-ghost-filter input")
    page.wait_for(".community-library-group")
    page.click(".community-library-group:first-child")
    page.wait_for(".community-library-player button.textlink")
    own_row = ".community-library-recording[data-recording-id='"+OWN_RECORDING+"']"
    assert_own_row_disabled(browser,own_row,".community-library-actions button")
    page.click(".community-library-recording:first-child .community-library-player button")
    page.wait_for(PLAYER+" .player-ghost-row")
    assert page.evaluate("return document.querySelector('.player-page').dataset.playerId")==RIVAL
    assert browser.practice()==before
    assert_no_viewer_writes(browser)
    assert page.problems()==[]


def test_explicit_publication_checkbox_self_preview_pause_and_withdraw(browser):
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
    verify_checkbox_alignment(page)
    capture(page,"progress-settings-round2-850.png",".progress-settings")
    assert page.evaluate("const el=document.querySelector('.progress-settings');return el.scrollWidth<=el.clientWidth+1")
    page.set_viewport(1400,1100)
    assert len(browser.account.writes)==details_writes
    set_pb_sharing(page,True)
    assert page.count(".progress-sharing-preview")==0
    assert browser.account.publication["visible"] and UID in browser.account.snapshots
    assert [path for path,_ in browser.account.writes[details_writes:]]==["/v2/account/progress/control","/v2/account/progress/snapshot"]
    click_text(page,ACCOUNT+" button","Preview shared page")
    page.wait_for(PLAYER+" .player-scorecard")
    assert page.evaluate("return document.querySelector('.player-page').dataset.playerId")==UID
    shared = page.evaluate("return Array.from(document.querySelectorAll('.player-scorecard .score-line .score-gap')).map(el=>el.textContent.trim()).filter(value=>value!=='—')")
    assert len(shared)==2 and set(shared)=={"+0.00"}
    assert page.count(".player-scorecard .score-card")==17
    for width in (850,1400):
        page.set_viewport(width,1100)
        assert_own_row_disabled(browser,".player-ghost-row[data-recording-id='"+OWN_RECORDING+"']")
    capture(page,"player-self-overall-round2-1400.png")
    assert browser.practice()==before
    open_account(browser)
    set_pb_sharing(page,False)
    assert not browser.account.publication["enabled"] and browser.account.publication["visible"]
    click_text(page,ACCOUNT+" button","Remove shared PBs")
    page.wait_for(".modal:has(.modal-heading h2)")
    click_text(page,".modal-actions button","Remove shared PBs")
    page.wait_ms(180)
    assert not browser.account.publication["visible"] and UID not in browser.account.snapshots
    assert browser.practice()==before
    assert_no_viewer_writes(browser)
    assert page.problems()==[]


@pytest.mark.parametrize("browser",[{"virgin":True}],indirect=True)
def test_unchosen_account_defaults_to_sharing_and_an_explicit_uncheck_survives_reinitialization(browser):
    page = browser.page
    open_account(browser)
    deadline = time.monotonic()+5
    while not browser.account.publication["enabled"] and time.monotonic()<deadline:
        page.wait_ms(100)
    assert browser.account.publication["enabled"],{
        "publication":browser.account.publication,"calls":browser.account.calls,
        "task":None if browser.progress._task is None else browser.progress._task.done()}
    page.wait_for(ACCOUNT+" .progress-sharing-controls > .community-check input:checked")
    page.wait_for(ACCOUNT+" button:has-text('Sync now')")
    assert browser.account.publication["visible"] and UID in browser.account.snapshots
    assert page.count(".progress-sharing-preview")==0
    assert [body["action"] for path,body in browser.account.writes if path=="/v2/account/progress/control"]==["enable"]
    set_pb_sharing(page,False)
    before = len(browser.account.writes)
    ui_fixture._run_coro(browser.progress.initialize_defaults())
    assert not browser.account.publication["enabled"] and browser.account.publication["visible"]
    assert len(browser.account.writes)==before
    assert page.problems()==[]


def test_publication_conflict_requires_a_fresh_deliberate_publisher_switch(browser):
    page = browser.page
    open_account(browser)
    browser.account.external_publisher()
    page.click(ACCOUNT+" .progress-sharing-controls > .community-check input")
    page.wait_for(ACCOUNT+" [role='alert']")
    assert len(browser.account.writes)==1
    expected = page.problems()
    assert expected and all("409" in problem for problem in expected),expected
    click_text(page,ACCOUNT+" button","Publish from this profile")
    page.wait_for(".progress-sharing-preview")
    before = deepcopy(browser.account.publication)
    capture(page,"publishing-switch-preview-round2-1400.png",PUBLICATION)
    click_text(page,PUBLICATION+" > .modal-actions button","Cancel")
    assert browser.account.publication==before
    click_text(page,ACCOUNT+" button","Publish from this profile")
    consent(page,"Use this profile")
    # The previous publisher already had sharing checked. Wait for this
    # switch's acknowledged result, including the completed snapshot upload.
    page.wait_for(ACCOUNT+" .progress-sharing-controls:not(:has(.progress-sharing-preview)) > .community-check input:checked")
    assert browser.account.publication["publisher"]["installation_id"]==INSTALLATION
    assert browser.account.publication["visible"]
    assert page.problems()==[]


def test_partial_private_and_offline_are_distinct_read_states(browser):
    page = browser.page
    browser.account.partial=True
    open_search(browser)
    assert page.count(".rank-card .rank-ladder")==1,partial_rank_diagnostics(browser)
    assert "Full rank unavailable" not in page.evaluate("return document.querySelector('.rank-card').textContent")
    assert "publication" not in page.evaluate("return document.querySelector('.player-profile-header').textContent")
    browser.account.partial,browser.account.unsupported = False,True
    page.click(PLAYER+" button[aria-label='Refresh shared page']")
    page.wait_for(".rank-card:has-text('Full rank unavailable.')")
    assert page.count(".rank-card .rank-ladder")==0
    browser.account.private=True
    page.click(PLAYER+" button[aria-label='Refresh shared page']")
    page.wait_ms(180)
    assert page.count(".player-scorecard")==0 and page.count(".player-ghost-row")==1
    assert "No shared PBs yet." in page.evaluate("return document.querySelector('.rank-card').textContent")
    browser.account.offline=True
    page.click(PLAYER+" button[aria-label='Refresh shared page']")
    page.wait_for(PLAYER+" [role='alert']")
    expected = page.problems()
    assert expected and all("503" in problem for problem in expected),expected
    browser.account.offline=False
    click_text(page,PLAYER+" button","Retry")
    page.wait_ms(180)
    assert page.count(PLAYER+" [role='alert']")==0
    assert page.problems()==[]


def partial_rank_diagnostics(browser):
    content = browser.account.published(RIVAL)["content"]
    definitions = browser.service.db.segment_defs()
    rows,_unbound = resolve_rows(content,definitions)
    unavailable = unavailable_segment_keys(content,definitions,rows)
    result = compare(browser.service,content,"overall")
    members = [row for row in result["you_rank"]["entities"] if row["key"] in unavailable and not row["excluded"]]
    return {"coverage":result["coverage"],"rank_reason":result["rank_reason"],
        "blocking_members":members[:10],"definitions":[{key:row.get(key) for key in ("id","name","seed_key","enabled")}
            for row in definitions if f"segment:{row['id']}" in {member["key"] for member in members}]}


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
