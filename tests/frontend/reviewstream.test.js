import { afterEach, expect, test, vi } from "vitest";
import { appendReviewStream } from "../../src/sm64_events/ui/reviewstream.js";

afterEach(() => vi.unstubAllGlobals());
const descriptor = {url:"/review.mp4",mime_type:"video/mp4",timestamp_offset_s:0,video_timescale:90000};

test("incremental append preserves every byte with bounded, serial submissions", async () => {
  const data = Uint8Array.from({length:700000}, (_, i) => i % 251);
  const body = new ReadableStream({start(controller) {controller.enqueue(data);controller.close();}});
  vi.stubGlobal("fetch",vi.fn(async () => new Response(body)));
  let busy = false, offset = 0, submissions = 0;
  const buffer = new EventTarget();
  buffer.appendBuffer = chunk => {
    expect(busy).toBe(false); busy=true; submissions++;
    expect(chunk.byteLength).toBeLessThanOrEqual(256*1024);
    expect(chunk).toEqual(data.subarray(offset,offset+chunk.byteLength)); offset+=chunk.byteLength;
    queueMicrotask(() => {busy=false;buffer.dispatchEvent(new Event("updateend"));});
  };
  const media = {addSourceBuffer:() => buffer};
  await appendReviewStream(media,descriptor,.3,new AbortController().signal,() => {});
  expect(offset).toBe(data.byteLength); expect(submissions).toBe(3);
  expect(buffer.appendWindowEnd).toBe(.3);
  expect(buffer.timestampOffset).toBe(0);
  expect(body.locked).toBe(false);
});

test("replacing a source cancels a pending read and releases the reader", async () => {
  const cancel = vi.fn();
  const body = new ReadableStream({start(controller) {controller.enqueue(new Uint8Array([1]));},cancel});
  vi.stubGlobal("fetch",vi.fn(async () => new Response(body)));
  const buffer = new EventTarget();
  buffer.appendBuffer = () => queueMicrotask(() => buffer.dispatchEvent(new Event("updateend")));
  const abort = new AbortController();
  let ready;
  const progressed = new Promise(resolve => {ready=resolve;});
  const pending = appendReviewStream({addSourceBuffer:() => buffer},descriptor,4,abort.signal,ready);
  const failed = expect(pending).rejects.toMatchObject({name:"AbortError"});
  await progressed; abort.abort(); await failed;
  expect(cancel).toHaveBeenCalledTimes(1); expect(body.locked).toBe(false);
});

test("decoder setup failure cancels unread response bytes", async () => {
  const cancel = vi.fn();
  const body = new ReadableStream({cancel});
  vi.stubGlobal("fetch",vi.fn(async () => new Response(body)));
  await expect(appendReviewStream({addSourceBuffer:() => {throw new Error("codec unavailable");}},
    descriptor,4,new AbortController().signal,() => {})).rejects.toThrow("codec unavailable");
  expect(cancel).toHaveBeenCalledTimes(1); expect(body.locked).toBe(false);
});
