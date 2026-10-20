// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/preact";
import { h } from "preact";
import { PlayerPage } from "../../src/sm64_events/ui/components/playerpage.js";
import { ProgressSettings } from "../../src/sm64_events/ui/components/progresssettings.js";
import { ReadonlyScorecard } from "../../src/sm64_events/ui/components/scorecard.js";
import { stubLocalServer } from "./localserver.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const UID = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const t = {view:{stars:[],segments:[]},standingRev:1,mareloRev:1};
const ok = value => ({ok:true,status:200,json:async () => structuredClone(value)});
const failed = (status,detail) => ({ok:false,status,json:async () => ({detail})});

function stubPlayerServer(fake) {
  // Rendered telemetry has its own real-browser persistence gate. It is
  // neither a public-profile mutation nor a publication consent action.
  return stubLocalServer((path,options) => path === "/api/uilog"
    ? ok({ok:true}) : fake(path,options));
}

function card() {
  return {rows:[{course_id:2,label:"Whomp's Fortress",kind:"course",tiles:[
    {key:"star:2:4",label:"Fall onto the Caged Island",clock:"igt",you_cs:1000,goal_cs:900,delta_cs:100},
    {key:"star:2:0",label:"Chip off Whomp's Block",clock:"igt",you_cs:null,goal_cs:2000,delta_cs:null},
  ],sum:{you_cs:1000,goal_cs:2900,delta_cs:100,counted:1,total:2}}],
    total:{you_cs:1000,goal_cs:2900,delta_cs:100,counted:1,total:2}};
}

function player(uid = UID) {
  return {player:{uid,name:uid===UID ? "Patient Runner" : "Another Player",claimed:true,
    claim_explanation:"A claimed Trainer account, not performance verification.",bio:"Practice and explore.",
    socials:[{kind:"twitch",url:"https://www.twitch.tv/example"},{kind:"website",url:"javascript:alert(1)"}],sheet_verified:false},
    progress:{published:true,revision:3,synced_utc:"2026-10-20T12:00:00Z",coverage:{complete:false,shared_rows:2,withheld_rows:1,unsupported_rows:0}},
    comparison:{card:card(),player_rank:null,you_rank:null,rank_reason:"Some custom segments are private.",context:{scope_id:"course:2",label:"Whomp's Fortress",regions:["us"],basis:"pb",calibration_revision:"fixture-edition"}},
    partial_reason:"Some custom segments are private."};
}

function ghost() {
  return {id:"a".repeat(64),player_id:UID,player_name:"Patient Runner",frames:400,
    target:{kind:"star",entity_key:"2-4",name:"Whomp's Fortress · Fall onto the Caged Island"},
    local_target:{kind:"star",entity_key:"2-4",strat:"TJ Owlless"},clock:"igt",game_version:"us",strategy:"TJ Owlless",
    can_add:true,unavailable_reason:null,saved_slot:null};
}

function publication() {
  return {available:true,uid:UID,publication:{v:2,uid:UID,revision:7,consent_revision:1,enabled:false,visible:false,
    publisher:null,notice_version:"progress-1",content_hash:null,captured_utc:null,synced_utc:null,limits:{rows:4096,bytes:1048576}},
    local:{publisher:{installation_id:"install",profile_id:"profile",label:"My practice profile"},dirty:false,last_error:null,conflict:false},
    preview:{rows:2,bytes:1024,coverage:{total_rows:3,shared_rows:2,withheld_rows:1,unsupported_rows:0,complete:false},default_rom:"us",oversize:false},
    consent:{version:"progress-1",text:"I agree to share this profile’s current saved PBs."},
    player_profile:{revision:2,bio:"",socials:[]}};
}

test("the shared comparison cards are read-only and signed differences have a neutral tone", () => {
  const view = render(h(ReadonlyScorecard,{t,card:card(),playerName:"Patient Runner"}));
  expect(view.container.querySelectorAll(".score-line")).toHaveLength(2);
  expect(view.container.querySelector(".score-label-goal").textContent).toBe("Patient Runner");
  expect(view.container.querySelector(".score-label-you").textContent).toBe("You");
  expect(view.container.querySelectorAll(".score-row-remove,.score-detail-goal-btn,.good,.bad")).toHaveLength(0);
  expect(view.container.querySelector(".score-card-foot").textContent).toContain('29"00');
  expect(view.queryByRole("button")).toBeNull();
});

test("a claimed page starts in the viewer scope and keeps only the actual ghost time in its inventory", async () => {
  const reads = [],writes = [];
  const row = ghost();
  stubPlayerServer(async (path,options={}) => {
    if (options.method) { writes.push({path,body:JSON.parse(options.body)}); return ok({state:"added",pins:{"0":row.id}}); }
    reads.push(path);
    if (path==="/api/marelo/scopes") return ok({active:"course:2",scopes:[{id:"course:2",label:"Whomp's Fortress"}]});
    if (path.includes("/recordings")) return ok({recordings:[row],next_cursor:null,complete:true});
    return ok(player());
  });
  const view = render(h(PlayerPage,{t,uid:UID,onClose:vi.fn()}));
  const button = await view.findByRole("button",{name:"Add ghost"});
  expect(reads).toContain(`/api/community/players/${UID}?scope=course%3A2`);
  expect(view.container.querySelector(".player-ghost-row .community-library-performance").textContent).toBe('13"33');
  expect(view.container.querySelector(".player-ghosts").textContent).not.toContain("saved PB in this comparison");
  expect(view.container.querySelector(".player-ghosts").textContent).not.toContain("Recording times may differ");
  expect(view.getByLabelText("Claimed Trainer account")).toBeTruthy();
  expect(view.container.textContent).toContain("Some PBs are unavailable. Full rank unavailable.");
  expect(view.container.querySelector(".player-profile-header").textContent).not.toContain("publication");
  expect(view.getByRole("button",{name:"Refresh shared page"}).textContent).toBe("");
  expect(view.container.querySelectorAll("a[href^='javascript:']")).toHaveLength(0);
  fireEvent.click(button);
  await view.findByRole("button",{name:"Remove"});
  expect(writes).toEqual([{path:"/api/ghost/rivals/saved",body:{target:row.local_target,action:"add",id:row.id,scope:"strategy",version:"us"}}]);
});

test("switching UUID drops a pending previous player instead of relabelling their data", async () => {
  let finishFirst;
  stubPlayerServer(path => {
    if (path==="/api/marelo/scopes") return ok({active:"overall",scopes:[{id:"overall",label:"Overall"}]});
    if (path.includes("/recordings")) return ok({recordings:[],next_cursor:null,complete:true});
    if (path.includes(UID)) return new Promise(resolve => { finishFirst = resolve; });
    return ok(player(OTHER));
  });
  const props = {t,onClose:vi.fn()};
  const view = render(h(PlayerPage,{...props,uid:UID}));
  await waitFor(() => expect(finishFirst).toBeTypeOf("function"));
  view.rerender(h(PlayerPage,{...props,uid:OTHER}));
  await view.findByRole("heading",{name:/Another Player/});
  await waitFor(() => expect(view.container.querySelector(".score-label-goal").textContent).toBe("Another Player"));
  finishFirst(ok(player(UID)));
  await waitFor(() => expect(view.container.textContent).not.toContain("Patient Runner"));
  expect(view.container.querySelector(".player-page").dataset.playerId).toBe(OTHER);
});

test("the PB sharing checkbox enables directly and pausing preserves the separate removal choice", async () => {
  const state = publication(),writes = [];
  stubPlayerServer((path,options={}) => {
    if (!options.method) return ok(state);
    const body = JSON.parse(options.body); writes.push({path,body});
    state.publication.revision++;
    if (body.action==="enable") Object.assign(state.publication,{enabled:true,visible:true,publisher:state.local.publisher,content_hash:"a".repeat(64)});
    if (body.action==="disable") state.publication.enabled=false;
    if (body.action==="withdraw") Object.assign(state.publication,{visible:false,content_hash:null});
    return ok(state);
  });
  const view = render(h(ProgressSettings,{onOpenPlayer:vi.fn()}));
  const checkbox = await view.findByRole("checkbox",{name:/Automatically share my PBs/});
  expect(checkbox.checked).toBe(false);
  expect(writes).toHaveLength(0);
  fireEvent.click(checkbox);
  await waitFor(() => expect(checkbox.checked).toBe(true));
  expect(view.queryByRole("dialog")).toBeNull();
  expect(writes[0]).toEqual({path:"/api/community/progress/control",body:{action:"enable",expected_revision:7,notice_version:"progress-1"}});
  fireEvent.click(checkbox);
  await waitFor(() => expect(checkbox.checked).toBe(false));
  expect(writes[1]).toEqual({path:"/api/community/progress/control",body:{action:"disable",expected_revision:8}});
  expect(view.getByRole("button",{name:"Remove shared PBs"})).toBeTruthy();
  expect(view.container.textContent).toContain("Previously shared PBs stay visible");
});

test("pending default-on sharing stays checked and refreshes to the canonical publication without writes", async () => {
  const state = publication(),writes = [];
  Object.assign(state.publication,{revision:0,consent_revision:0});
  Object.assign(state.local,{default_pending:true,share_pbs:true});
  let reads = 0,finishRead;
  stubPlayerServer((path,options={}) => {
    if (options.method) { writes.push({path,body:JSON.parse(options.body)}); return ok(state); }
    reads++;
    return reads===1 ? ok(state) : new Promise(resolve => { finishRead = resolve; });
  });
  const view = render(h(ProgressSettings,{}));
  const checkbox = await view.findByRole("checkbox",{name:/Automatically share my PBs/});
  expect(checkbox.checked).toBe(true);
  expect(view.container.textContent).toContain("PB sharing is on. Waiting for the first sync.");
  expect(writes).toEqual([]);
  await waitFor(() => expect(finishRead).toBeTypeOf("function"),{timeout:3500});
  Object.assign(state.publication,{revision:2,consent_revision:1,enabled:true,visible:true,
    publisher:state.local.publisher,content_hash:"a".repeat(64)});
  state.local.default_pending=false;
  finishRead(ok(state));
  await view.findByRole("button",{name:"Sync now"});
  expect(checkbox.checked).toBe(true);
  expect(view.container.textContent).not.toContain("Waiting for the first sync");
  expect(reads).toBe(2);
  expect(writes).toEqual([]);
});

test("an unavailable publication remains readable without a local default state", async () => {
  const writes = [];
  stubPlayerServer((path,options={}) => {
    if (options.method) writes.push(path);
    return ok({available:false,reason:"This Trainer window cannot publish saved PBs."});
  });
  const view = render(h(ProgressSettings,{}));
  await view.findByText("This Trainer window cannot publish saved PBs.");
  expect(view.queryByRole("checkbox")).toBeNull();
  expect(writes).toEqual([]);
});

test("oversize preview blocks new publication but never blocks withdrawal or invents a zero-row preview", async () => {
  const state = publication(),writes = [];
  Object.assign(state.publication,{enabled:true,visible:true,publisher:state.local.publisher,content_hash:"a".repeat(64)});
  state.preview={rows:null,bytes:null,coverage:null,oversize:true,reason:"This saved-PB snapshot is too large to publish."};
  stubPlayerServer((path,options={}) => {
    if (options.method) { writes.push(JSON.parse(options.body)); state.publication.visible=false; }
    return ok(state);
  });
  const view = render(h(ProgressSettings,{}));
  expect((await view.findByRole("button",{name:"Sync now"})).disabled).toBe(true);
  fireEvent.click(view.getByRole("button",{name:"Remove shared PBs"}));
  const confirm = within(view.getByRole("dialog")).getByRole("button",{name:"Remove shared PBs"});
  expect(confirm.disabled).toBe(false);
  fireEvent.click(confirm);
  await waitFor(() => expect(writes).toEqual([{action:"withdraw",expected_revision:7}]));
  expect(view.container.textContent).not.toContain("0 current saved PB rows");
});

test("a stale publication revision is shown and reconciled without automatically replaying the write", async () => {
  const state = publication(),writes = [];
  stubPlayerServer((path,options={}) => {
    if (!options.method) return ok(state);
    writes.push(JSON.parse(options.body)); state.publication.revision=9;
    return failed(409,"Publication changed on another Trainer. Review it again.");
  });
  const view = render(h(ProgressSettings,{}));
  fireEvent.click(await view.findByRole("checkbox",{name:/Automatically share my PBs/}));
  await view.findByRole("alert");
  await waitFor(() => expect(view.queryByRole("dialog")).toBeNull());
  expect(writes).toHaveLength(1);
  expect(view.container.textContent).toContain("Review it again");
});

test("an own recording stays visible with a described disabled action and no ghost write", async () => {
  const writes = [];
  const row = {...ghost(),can_add:false,is_own:true,unavailable_reason:"Your PB ghost is included automatically."};
  stubPlayerServer((path,options={}) => {
    if (options.method) { writes.push(path); return ok({}); }
    if (path==="/api/marelo/scopes") return ok({active:"overall",scopes:[{id:"overall",label:"Overall"}]});
    if (path.includes("/recordings")) return ok({recordings:[row],next_cursor:null,complete:true});
    return ok(player());
  });
  const view = render(h(PlayerPage,{t,uid:UID,onClose:vi.fn()}));
  const action = await view.findByRole("button",{name:"Add ghost"});
  expect(action.disabled).toBe(true);
  expect(view.container.querySelectorAll(".player-ghost-row")).toHaveLength(1);
  expect(view.container.querySelector(".player-ghost-row .community-library-unavailable")).toBeNull();
  const hint = view.getByRole("group",{name:"Add ghost unavailable"});
  expect(hint.tabIndex).toBe(0);
  const reason = document.getElementById(hint.getAttribute("aria-describedby"));
  expect(reason.textContent).toBe("Your PB ghost is included automatically.");
  expect(action.getAttribute("aria-describedby")).toBe(reason.id);
  action.click();
  expect(writes).toEqual([]);
});

test("a different player's unavailable ghost keeps its visible explanation", async () => {
  const row = {...ghost(),can_add:false,is_own:false,unavailable_reason:"This target has no local rank standards yet."};
  stubPlayerServer(path => {
    if (path==="/api/marelo/scopes") return ok({active:"overall",scopes:[{id:"overall",label:"Overall"}]});
    if (path.includes("/recordings")) return ok({recordings:[row],next_cursor:null,complete:true});
    return ok(player());
  });
  const view = render(h(PlayerPage,{t,uid:UID,onClose:vi.fn()}));
  expect((await view.findByRole("button",{name:"Add ghost"})).disabled).toBe(true);
  expect(view.container.querySelector(".player-ghost-row .community-library-unavailable").textContent).toBe(row.unavailable_reason);
  expect(view.queryByRole("group",{name:"Add ghost unavailable"})).toBeNull();
});
