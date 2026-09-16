// Incremental consumption for the opt-in fragmented source. No whole-response
// ArrayBuffer is retained in JS; MSE still owns its admitted decoder buffers.
const APPEND_BYTES = 256 * 1024;

function append(buffer, data, signal) {
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      buffer.removeEventListener("updateend", done);
      buffer.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
      if (error) reject(error); else resolve();
    };
    const done = () => finish();
    const failed = () => finish(new Error("Fragment decode failed."));
    const aborted = () => finish(new DOMException("Review source replaced.", "AbortError"));
    buffer.addEventListener("updateend", done);
    buffer.addEventListener("error", failed);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) { aborted(); return; }
    try { buffer.appendBuffer(data); } catch (error) { finish(error); }
  });
}

export async function appendReviewStream(mediaSource, source, end, signal, progressed) {
  if (signal.aborted) throw new DOMException("Review source replaced.", "AbortError");
  const response = await fetch(source.url, { signal });
  if (signal.aborted) {
    await response.body?.cancel();
    throw new DOMException("Review source replaced.", "AbortError");
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`Media request failed (${response.status}).`);
  }
  if (!response.body) throw new Error("Review media response has no stream.");
  const reader = response.body.getReader();
  const aborted = () => { reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", aborted, { once: true });
  try {
    const buffer = mediaSource.addSourceBuffer(source.mime_type);
    buffer.timestampOffset = source.timestamp_offset_s;
    // Keep the preceding GOP. Only the exclusive Out limits admitted pictures.
    buffer.appendWindowEnd = Math.round(end * source.video_timescale) / source.video_timescale;
    mediaSource.duration = end;
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      // MSE can parse a box across appends; no concatenation or extra full-clip
      // copy is needed. Await each append before requesting the next chunk.
      for (let offset = 0; offset < value.byteLength; offset += APPEND_BYTES) {
        await append(buffer, value.subarray(offset, offset + APPEND_BYTES), signal);
        progressed(buffer);
      }
    }
    if (signal.aborted) throw new DOMException("Review source replaced.", "AbortError");
  } finally {
    signal.removeEventListener("abort", aborted);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
