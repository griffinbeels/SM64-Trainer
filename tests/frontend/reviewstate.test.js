// @vitest-environment jsdom
import { h } from "preact";
import { render, act, cleanup, waitFor } from "@testing-library/preact";
import { afterEach, expect, test, vi } from "vitest";
import { useReviewState } from "../../src/sm64_events/ui/reviewstate.js";

afterEach(()=>{cleanup();vi.unstubAllGlobals();});

test("retry reloads preferences after the initial GET fails", async () => {
  let hook, fail=true;
  vi.stubGlobal("fetch",vi.fn(async()=> {
    if(fail) throw new Error("offline");
    return {ok:true,json:async()=>({template_offsets:{},zoom:{start:1,end:4},loop:null})};
  }));
  function Probe(){hook=useReviewState(913);return null;}
  render(h(Probe));
  await waitFor(()=>expect(hook.error).toContain("offline"));
  expect(hook.state).toBeNull(); fail=false;
  await act(async()=>hook.retry());
  await waitFor(()=>expect(hook.state?.zoom).toEqual({start:1,end:4}));
  expect(hook.error).toBeNull();
});

test("a late first write cannot overwrite later edits; closing preserves queued state", async () => {
  let hook, release;
  const writes=[];
  vi.stubGlobal("fetch",vi.fn(async (_url, options)=> {
    if (!options) return {ok:true,json:async()=>({template_offsets:{},zoom:null,loop:null})};
    writes.push(JSON.parse(options.body));
    if (writes.length===1) await new Promise(resolve=>release=resolve);
    return {ok:true};
  }));
  function Probe(){hook=useReviewState(912);return null;}
  const component=render(h(Probe));
  await waitFor(()=>expect(hook.state).not.toBeNull());
  act(()=>hook.change({zoom:{start:1,end:5}}));
  act(()=>hook.change({zoom:{start:2,end:4}}));
  component.unmount(); release();
  await waitFor(()=>expect(writes).toHaveLength(2));
  expect(writes[1].zoom).toEqual({start:2,end:4});
});
