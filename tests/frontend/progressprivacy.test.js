// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/preact";
import { h } from "preact";
import { ProgressPrivacy } from "../../src/sm64_events/ui/components/progressprivacy.js";
import { downloadAccountData } from "../../src/sm64_events/ui/community.js";
import { stubLocalServer } from "./localserver.js";

vi.mock("../../src/sm64_events/ui/community.js",() => ({downloadAccountData:vi.fn()}));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
const UID = "10000000-0000-4000-8000-000000000001";
const OTHER = "10000000-0000-4000-8000-000000000002";
const privacy = (uid=UID) => ({available:true,status:"suspended",publication:{v:2,uid,revision:7,consent_revision:3,
  enabled:true,visible:false,publisher:null,notice_version:"progress-1",content_hash:"a".repeat(64),
  captured_utc:null,synced_utc:null,limits:{rows:4096,bytes:1048576}}});
const ok = value => ({ok:true,status:200,json:async () => structuredClone(value)});
const account = {uid:UID,status:"suspended",authenticated:true};

test("hidden retained snapshots can be paused and removed without granting new consent",async () => {
  const state = privacy(),writes=[];
  stubLocalServer((path,options={}) => {
    if (!options.method) return ok(state);
    expect(path).toBe("/api/community/progress/privacy/control");
    const body = JSON.parse(options.body); writes.push(body);
    expect(body.expected_revision).toBe(state.publication.revision);
    state.publication.revision++;
    if (body.action==="disable") state.publication.enabled=false;
    else if (body.action==="withdraw") state.publication.content_hash=null;
    else throw new Error("Privacy must never enable publication");
    return ok(state);
  });
  const view = render(h(ProgressPrivacy,{account}));
  await view.findByText("That stored snapshot is not visible on your player page.");
  fireEvent.click(view.getByRole("button",{name:"Pause PB updates"}));
  await waitFor(() => expect(view.queryByRole("button",{name:"Pause PB updates"})).toBeNull());
  fireEvent.click(view.getByRole("button",{name:"Remove stored PBs"}));
  let dialog = within(view.getByRole("dialog"));
  expect(dialog.queryByRole("checkbox")).toBeNull();
  fireEvent.click(dialog.getByRole("button",{name:"Cancel"}));
  expect(writes).toEqual([{action:"disable",expected_revision:7}]);
  fireEvent.click(view.getByRole("button",{name:"Remove stored PBs"}));
  dialog = within(view.getByRole("dialog"));
  fireEvent.click(dialog.getByRole("button",{name:"Remove stored PBs"}));
  await view.findByText("No saved-PB snapshot is stored for this account.");
  expect(writes).toEqual([{action:"disable",expected_revision:7},{action:"withdraw",expected_revision:8}]);
});

test.each(["uid","status"])("an account %s change cancels the previous read and cannot paint its late result",async change => {
  let finish,signal;
  let reads=0;
  stubLocalServer((_path,options={}) => {
    if (++reads===1) { signal=options.signal; return new Promise(resolve => { finish=resolve; }); }
    const fresh=privacy(change==="uid" ? OTHER : UID); fresh.publication.content_hash=null;
    return ok(fresh);
  });
  const view=render(h(ProgressPrivacy,{account}));
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  view.rerender(h(ProgressPrivacy,{account:{...account,[change]:change==="uid" ? OTHER : "consent_required"}}));
  await view.findByText("No saved-PB snapshot is stored for this account.");
  expect(signal.aborted).toBe(true);
  finish(ok(privacy()));
  await waitFor(() => expect(view.queryByRole("button",{name:"Remove stored PBs"})).toBeNull());
});

test("malformed and unavailable privacy responses show an honest retry",async () => {
  let reply={available:true,publication:null};
  stubLocalServer(() => ok(reply));
  const view=render(h(ProgressPrivacy,{account}));
  expect((await view.findByRole("alert")).textContent).toContain("could not be read");
  reply={available:false,reason:"Reconnect to review retained PB data."};
  fireEvent.click(view.getByRole("button",{name:"Retry"}));
  await view.findByText(reply.reason);
  expect(view.queryByRole("alert")).toBeNull();
  expect(view.getByRole("button",{name:"Retry"})).toBeTruthy();
});

test("export uses the existing account export door and never a public-page or upload route",async () => {
  const calls=[],value={profile:{uid:UID},stored_progress:{rows:[]}};
  stubLocalServer(path => { calls.push(path); return ok(path.endsWith("/export") ? value : privacy()); });
  const view=render(h(ProgressPrivacy,{account}));
  await view.findByRole("button",{name:"Remove stored PBs"});
  fireEvent.click(view.getByRole("button",{name:"Export account data"}));
  await waitFor(() => expect(downloadAccountData).toHaveBeenCalledWith(value));
  expect(calls).toEqual(["/api/community/progress/privacy","/api/community/account/export"]);
});
