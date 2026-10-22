"""Friends flow through shipping components and an isolated cached API fixture.

Pure model/request tests launch Node only. Browser cases are the parent-owned
gate: their fixture opens no app, SDK, emulator, sound or recording resource.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from friends_fixture import PRIVATE_CODE, NAMES, scene, serve_friends
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
MODEL = (ROOT / "src/sm64_events/ui/friends.js").as_uri()
API = (ROOT / "src/sm64_events/ui/api.js").as_uri()


def node(script):
    binary = shutil.which("node")
    if not binary:
        pytest.skip("Node is required for UI model checks")
    result = subprocess.run([binary, "--input-type=module"], input=script, text=True, encoding="utf-8", capture_output=True,
                            timeout=20, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_friends_model_retains_four_balanced_slots_and_authoritative_actions():
    states = {name: scene(name) for name in ("idle", "unavailable", "invites", "hosting", "full", "reconnecting", "error")}
    result = node(f"""
import {{friendsModel,memberState}} from {MODEL!r};
const states={json.dumps(states)};
const out=Object.fromEntries(Object.entries(states).map(([name,state])=>{{
 const model=friendsModel(state);
 return [name,{{slots:model.slots.length,count:model.count,invite:model.canInvite,badge:model.badge,
 states:model.members.map(memberState),transitioning:model.transitioning}}];
}}));
console.log(JSON.stringify(out));
""")
    assert all(value["slots"] == 4 for value in result.values())
    assert result["hosting"]["invite"] and not result["full"]["invite"]
    assert result["full"]["count"] == 4 and result["full"]["badge"] == "4/4"
    assert result["invites"]["badge"] == "1"
    assert result["reconnecting"]["states"][2] == "Reconnecting…"
    assert result["reconnecting"]["badge"] == result["error"]["badge"] == "!"


def test_friends_private_request_errors_never_report_codes_or_server_details():
    result = node(f"""
import {{friendsRequest}} from {MODEL!r};
import {{setApiFailureReporter}} from {API!r};
globalThis.location=new URL('http://127.0.0.1:9999/');
const reports=[],writes=[];setApiFailureReporter(error=>reports.push(error.message));
globalThis.fetch=async(url,options={{}})=>{{
 if(url==='/api/local-access')return new Response(JSON.stringify({{request_token:'fixture'}}));
 writes.push({{url,body:options.body,token:options.headers.get('X-SM64-Request-Token')}});
 return new Response(JSON.stringify({{detail:{json.dumps(PRIVATE_CODE)}}}),{{status:500}});
}};
let error='';try{{await friendsRequest('invite',{{code:{json.dumps(PRIVATE_CODE)}}});}}catch(problem){{error=problem.message;}}
console.log(JSON.stringify({{error,reports,writes}}));
""")
    assert PRIVATE_CODE not in result["error"] and "Check their player code" in result["error"]
    assert result["reports"] == []
    assert json.loads(result["writes"][0]["body"]) == {"code": PRIVATE_CODE}
    assert result["writes"][0]["token"] == "fixture"


def test_friends_dom_observation_reads_only_visible_phase_and_counts():
    result = node(f"""
import {{readFriends,responseSnapshot}} from {MODEL!r};
const root={{matches:()=>true,dataset:{{phase:'hosting'}},
 querySelectorAll:selector=>Array(selector==='.friends-seat.is-occupied'?4:1).fill(null),
 textContent:{json.dumps(PRIVATE_CODE)},value:{json.dumps(PRIVATE_CODE)}}};
console.log(JSON.stringify({{observation:readFriends(root),
 wrapped:responseSnapshot({{queued:true,snapshot:{{phase:'hosting'}}}}),
 flat:responseSnapshot({{queued:true,phase:'hosting'}})}}));
""")
    assert result["observation"] == {"surface": "friends", "phase": "hosting", "members": 4, "invitations": 1}
    assert PRIVATE_CODE not in json.dumps(result)
    assert result["wrapped"]["phase"] == result["flat"]["phase"] == "hosting"


def test_friends_component_module_loads_against_shipping_vendor_imports():
    component = (ROOT / "src/sm64_events/ui/components/friends.js").as_uri()
    vendor = ROOT / "src/sm64_events/ui/vendor"
    aliases = {"preact": (vendor / "preact.module.js").as_uri(),
               "preact/hooks": (vendor / "hooks.module.js").as_uri(), "htm": (vendor / "htm.module.js").as_uri()}
    result = node(f"""
import {{registerHooks}} from 'node:module';
const aliases={json.dumps(aliases)};
registerHooks({{resolve(name,context,next){{return next(aliases[name]||name,context);}}}});
const component=await import({component!r});
console.log(JSON.stringify({{control:typeof component.FriendsControl}}));
""")
    assert result == {"control": "function"}


def driver():
    sys.path.insert(0, str(ROOT / "tools"))
    from find_uilab import find_uilab
    missing = find_uilab()
    if missing:
        pytest.skip(missing)
    from uilab.driver import get_driver
    return get_driver()


@pytest.fixture
def browser(request, tmp_path):
    options = getattr(request, "param", {})
    width = options.get("width", 1400)
    with serve_friends(options.get("scene", "idle"), loading=options.get("loading", False)) as (base, state):
        with driver().launch(headless=True, viewport=(width, 1000)) as page:
            page.goto(base + "/")
            page.wait_for(".friends-control")
            try:
                yield page, state, tmp_path
            finally:
                state.release.set()
                problems = page.problems()
                if problems:
                    (tmp_path / "friends-failure.png").write_bytes(page.screenshot())
                assert problems == [], problems


def wait(page, expression):
    deadline = time.monotonic() + 12
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, expression
        page.wait_ms(30)


def click(page, label, scope=".modal"):
    expression = ("Array.from(document.querySelectorAll(" + json.dumps(scope + " button") + "))"
                  ".find(button=>!button.disabled&&button.getClientRects().length&&button.textContent.trim()==="
                  + json.dumps(label) + ")")
    wait(page, f"Boolean({expression})")
    page.evaluate(f"({expression}).click()")


def open_panel(page):
    page.click(".friends-control")
    page.wait_for(".friends-surface")


@pytest.mark.parametrize("browser", [{"scene": "full", "width": 850}, {"scene": "full", "width": 1400}], indirect=True)
def test_friends_four_slots_render_equal_without_overflow(browser):
    page, _state, directory = browser
    open_panel(page)
    page.wait_for(".friends-seat.is-occupied")
    geometry = page.evaluate("""return {viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
      seats:[...document.querySelectorAll('.friends-seat')].map(el=>{const r=el.getBoundingClientRect();return {width:r.width,height:r.height};}),
      names:[...document.querySelectorAll('.friends-seat strong')].map(el=>el.textContent),
      modal:document.querySelector('.modal').getBoundingClientRect().toJSON()};""")
    assert len(geometry["seats"]) == 4 and geometry["names"] == NAMES
    assert max(row["width"] for row in geometry["seats"]) - min(row["width"] for row in geometry["seats"]) <= 1
    assert max(row["height"] for row in geometry["seats"]) - min(row["height"] for row in geometry["seats"]) <= 1
    assert not geometry["overflow"] and geometry["modal"]["right"] <= geometry["viewport"]
    assert page.count(".friends-full") == 1 and page.count(".friends-code") == 0
    (directory / f"friends-full-{geometry['viewport']}.png").write_bytes(page.screenshot())


@pytest.mark.parametrize("browser", [{"scene": "invites"}], indirect=True)
def test_friends_explicit_accept_and_closed_modal_keep_room_and_badge(browser):
    page, state, _directory = browser
    wait(page, "document.querySelector('.friends-badge')?.textContent==='1'")
    assert state.actions == []
    open_panel(page)
    click(page, "Accept")
    wait(page, "document.querySelectorAll('.friends-seat.is-occupied').length===2")
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
    wait(page, "!document.querySelector('.friends-surface')")
    assert [action for action, _body in state.actions] == ["join"]
    wait(page, "document.querySelector('.friends-badge')?.textContent==='2/4'")
    open_panel(page)
    assert page.count(".friends-seat.is-occupied") == 2


@pytest.mark.parametrize("browser", [{"scene": "invites"}], indirect=True)
def test_friends_decline_does_not_join_or_create_a_room(browser):
    page, state, _directory = browser
    open_panel(page)
    click(page, "Decline")
    wait(page, "!document.querySelector('.friends-invitation')")
    assert state.actions == [("decline", {"invite_id": "fixture-invite"})]
    assert state.data["room"] is None and page.count(".friends-start") == 1


def test_friends_code_copy_host_invite_and_safe_dom_telemetry(browser):
    page, state, _directory = browser
    page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__friendCopied=value;}}})")
    open_panel(page)
    click(page, "Copy my player code")
    wait(page, "document.querySelector('.friends-copy-state')?.textContent==='Player code copied.'")
    assert page.evaluate("return window.__friendCopied") == PRIVATE_CODE
    click(page, "Start a room")
    page.wait_for(".friends-code")
    page.evaluate("const input=document.querySelector('.friends-code');input.value=" + json.dumps(PRIVATE_CODE)
                  + ";input.dispatchEvent(new Event('input',{bubbles:true}));")
    click(page, "Invite friend")
    wait(page, "document.querySelector('.friends-invite-note')?.textContent==='Waiting for your friend…'")
    assert [action for action, _body in state.actions] == ["identity", "host", "invite"]
    assert state.actions[-1][1] == {"code": PRIVATE_CODE}
    wait(page, "Boolean(document.querySelector('.friends-surface[data-phase=hosting]'))")
    assert state.observations and PRIVATE_CODE not in json.dumps(state.observations)


@pytest.mark.parametrize("browser", [{"scene": "reconnecting"}], indirect=True)
def test_friends_reconnect_and_explicit_remove_leave_actions(browser):
    page, state, _directory = browser
    open_panel(page)
    wait(page, "document.querySelectorAll('.friends-seat.is-occupied').length===4")
    assert page.evaluate("return document.querySelector('.friends-phase').textContent") == "Reconnecting…"
    page.click("button[aria-label=" + json.dumps("Remove " + NAMES[2]) + "]")
    wait(page, "document.querySelectorAll('.friends-seat.is-occupied').length===3")
    assert state.actions[-1] == ("kick", {"member_id": "fixture-player-2"})
    click(page, "Leave room")
    wait(page, "Boolean(document.querySelector('.friends-start'))")
    assert state.actions[-1][0] == "leave"


@pytest.mark.parametrize("browser", [{"scene": "unavailable"}, {"scene": "error"}], indirect=True)
def test_friends_unavailable_and_error_are_actionable(browser):
    page, state, directory = browser
    open_panel(page)
    if not state.data["available"]:
        page.wait_for(".friends-unavailable")
        assert page.count(".friends-start button") == 0
    else:
        page.wait_for(".friends-error")
        assert "Couldn’t reconnect" in page.evaluate("return document.querySelector('.friends-error').textContent")
    (directory / "friends-unavailable-or-error.png").write_bytes(page.screenshot())


@pytest.mark.parametrize("browser", [{"loading": True}], indirect=True)
def test_friends_initial_loading_then_idle_is_reachable(browser):
    page, state, _directory = browser
    open_panel(page)
    assert page.evaluate("return document.querySelector('.friends-loading').textContent") == "Loading Friends…"
    state.release.set()
    wait(page, "Boolean(document.querySelector('.friends-start'))")
    assert state.actions == []


def test_friends_clipboard_fallback_is_private_and_clears_when_modal_closes(browser):
    page, state, _directory = browser
    page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('fixture denied');}}})")
    open_panel(page)
    click(page, "Copy my player code")
    page.wait_for("input[readonly][data-private-input]")
    assert page.evaluate("return document.querySelector('input[readonly]').value") == PRIVATE_CODE
    assert PRIVATE_CODE not in json.dumps(state.observations)
    page.click(".modal-close")
    open_panel(page)
    assert page.count("input[readonly]") == 0 and [action for action, _body in state.actions] == ["identity"]


@pytest.mark.parametrize("browser", [{"scene": "invites"}], indirect=True)
def test_friends_member_can_invite_without_host_removal_controls(browser):
    page, state, _directory = browser
    open_panel(page)
    click(page, "Accept")
    page.wait_for(".friends-invite-form")
    assert page.count(".friends-remove") == 0
    assert "Anyone here can invite friends" in page.evaluate("return document.querySelector('.friends-invite-note').textContent")
    page.evaluate("const input=document.querySelector('.friends-code');input.value='ET1.fixture-friend';input.dispatchEvent(new Event('input',{bubbles:true}));")
    click(page, "Invite friend")
    wait(page, "document.querySelector('.friends-invite-note').textContent.includes('Waiting for your friend')")
    assert [action for action, _body in state.actions] == ["join", "invite"]


@pytest.mark.parametrize('browser', [{'scene': 'disconnected', 'width': 850},
                                    {'scene': 'disconnected', 'width': 1400}], indirect=True)
def test_terminal_peer_failure_has_a_working_reconnect_control(browser):
    page, state, directory = browser
    open_panel(page)
    page.wait_for('.friends-seat.is-occupied')
    assert page.evaluate("return [...document.querySelectorAll('.friends-member-state')].some(el=>el.textContent==='Connection lost')")
    assert not page.evaluate('return document.documentElement.scrollWidth>innerWidth')
    (directory / 'friends-connection-lost.png').write_bytes(page.screenshot())
    page.click('button[aria-label=' + json.dumps('Reconnect ' + NAMES[2]) + ']')
    wait(page, "!document.querySelector('button[aria-label^=Reconnect]')")
    assert state.actions == [('retry_peer', {'member_id': 'fixture-player-2'})]
    assert page.evaluate("return [...document.querySelectorAll('.friends-member-state')].some(el=>el.textContent==='Reconnecting…')")


@pytest.mark.parametrize('browser', [{'scene': 'error'}], indirect=True)
def test_service_error_retry_mutates_instead_of_only_refreshing(browser):
    page, state, _ = browser
    open_panel(page)
    click(page, 'Retry')
    wait(page, "!document.querySelector('.friends-error')")
    assert state.actions == [('retry', {})]


@pytest.mark.parametrize('browser', [{'scene': 'display_problem', 'width': 850}], indirect=True)
def test_graphics_refusal_is_visible_without_hiding_room_controls(browser):
    page, _, directory = browser
    open_panel(page)
    page.wait_for('.friends-source')
    assert page.evaluate("return document.querySelector('.friends-source').textContent") == \
        'Live ghosts are unavailable with the current graphics settings.'
    assert page.count('.friends-seat.is-occupied') == 4
    assert not page.evaluate('return document.documentElement.scrollWidth>innerWidth')
    (directory / 'friends-graphics-refusal.png').write_bytes(page.screenshot())
