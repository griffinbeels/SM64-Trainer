// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { useRef } from "preact/hooks";
import { useStandardWrites } from "../../src/sm64_events/ui/standardwrites.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function fixture() {
  const requests = [];
  vi.stubGlobal("fetch", vi.fn((path, options) => new Promise((resolve) => {
    requests.push({ path, ...options, finish(error = null) {
      resolve({ ok: !error, status: error ? 409 : 200,
        json: async () => error ? { detail: error } : { ok: true } });
    } });
  })));
  let writes;
  function Editor({ entity = "star:2:4", load, changed }) {
    writes = useStandardWrites(entity, load, changed, useRef(0));
    return h("div", null, writes.message);
  }
  const load = vi.fn(async () => {}), changed = vi.fn();
  const view = render(h(Editor, { load, changed }));
  return { requests, load, changed, view, writes: () => writes,
    replace: (props) => view.rerender(h(Editor, { load, changed, ...props })) };
}

test("earlier blurs cannot overtake a final value or a following reset", async () => {
  const f = fixture();
  const first = f.writes().put("Standard", "Mario", 81.7);
  const last = f.writes().put("Standard", "Mario", 81.32);
  const reset = f.writes().reset();
  await waitFor(() => expect(f.requests).toHaveLength(1));
  expect(f.load).not.toHaveBeenCalled();
  f.requests[0].finish();
  await waitFor(() => expect(f.requests).toHaveLength(2));
  expect(f.load).not.toHaveBeenCalled();
  f.requests[1].finish();
  await waitFor(() => expect(f.requests).toHaveLength(3));
  f.requests[2].finish();
  await Promise.all([first, last, reset]);
  expect(f.requests.map(r => r.body ? JSON.parse(r.body).seconds : r.method))
    .toEqual([81.7, 81.32, "POST"]);
  expect(f.load).toHaveBeenCalledTimes(1);
  expect(f.changed).toHaveBeenCalledTimes(1);
  expect(f.writes().pending.current).toBe(0);
});

test("another cell succeeding cannot hide a failed edit, and retry clears that cell's error", async () => {
  const f = fixture();
  const first = f.writes().put("Owl", "Mario", 16);
  const next = f.writes().put("Standard", "Mario", 11);
  await waitFor(() => expect(f.requests).toHaveLength(1));
  f.requests[0].finish("Owl cutoff was rejected");
  await waitFor(() => expect(f.requests).toHaveLength(2));
  f.requests[1].finish();
  await Promise.all([first, next]);
  expect((await f.view.findByRole("alert")).textContent).toBe("Owl cutoff was rejected");
  const retry = f.writes().put("Owl", "Mario", 17);
  await waitFor(() => expect(f.requests).toHaveLength(3));
  f.requests[2].finish();
  await retry;
  await waitFor(() => expect(f.view.queryByRole("alert")).toBeNull());
});

test("a failed refresh is visible and still notifies the other readers", async () => {
  const f = fixture();
  f.load.mockRejectedValue(new Error("offline"));
  const saved = f.writes().put("Standard", "Mario", 11);
  await waitFor(() => expect(f.requests).toHaveLength(1));
  f.requests[0].finish();
  await saved;
  expect((await f.view.findByRole("alert")).textContent)
    .toBe("Saved standards could not be refreshed: offline");
  expect(f.changed).toHaveBeenCalledTimes(1);
});

test("pending JP intent is known before refresh, while writes retain their original entity", async () => {
  const f = fixture();
  const saved = f.writes().put("Standard", "Mario", 11, "jp");
  expect(f.writes().hasJpWrite("Standard")).toBe(true);
  const freshLoad = vi.fn(async () => {});
  f.replace({ entity: "star:1:1", load: freshLoad });
  expect(f.writes().hasJpWrite("Standard")).toBe(false);
  await waitFor(() => expect(f.requests).toHaveLength(1));
  expect(f.requests[0].path).toBe("/api/ranks/standards/star%3A2%3A4/Standard/Mario?version=jp");
  f.requests[0].finish();
  await saved;
  expect(freshLoad).toHaveBeenCalledTimes(1);
  expect(f.load).not.toHaveBeenCalled();
});

test("a successful JP clear retires an earlier failure but preserves later queued intent", async () => {
  const f = fixture();
  const failed = f.writes().put("Standard", "Mario", 11, "jp");
  await waitFor(() => expect(f.requests).toHaveLength(1));
  f.requests[0].finish("JP cutoff rejected");
  await failed;
  expect(await f.view.findByRole("alert")).toBeTruthy();
  const cleared = f.writes().clearJp("Standard");
  const later = f.writes().put("Standard", "Mario", 12, "jp");
  await waitFor(() => expect(f.requests).toHaveLength(2));
  f.requests[1].finish();
  await cleared;
  await waitFor(() => expect(f.view.queryByRole("alert")).toBeNull());
  expect(f.writes().hasJpWrite("Standard")).toBe(true);
  await waitFor(() => expect(f.requests).toHaveLength(3));
  f.requests[2].finish();
  await later;
  const reset = f.writes().reset();
  await waitFor(() => expect(f.requests).toHaveLength(4));
  f.requests[3].finish();
  await reset;
  expect(f.writes().hasJpWrite("Standard")).toBe(false);
});
