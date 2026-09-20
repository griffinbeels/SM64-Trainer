// A native Mario frame carries its pixels and position in one packet. Canvas
// bytes keep their source alpha; the window applies the user's opacity once.
export const MAX_CROP_DIMENSION = 4096;
export const MAX_CROP_PIXELS = 4_000_000;

function base64(bytes) {
  const parts = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  }
  return btoa(parts.join(''));
}

function cropRect(canvas, draw) {
  const {dx, dy, dw, dh} = draw;
  if (![dx, dy, dw, dh].every(Number.isFinite) || dw <= 0 || dh <= 0) return null;
  const vw = canvas.width, vh = canvas.height;
  if (!Number.isSafeInteger(vw) || !Number.isSafeInteger(vh) || vw <= 0 || vh <= 0) {
    return null;
  }
  const bounds = canvas.getBoundingClientRect();
  if (![bounds.width, bounds.height].every(Number.isFinite)
      || bounds.width <= 0 || bounds.height <= 0) return null;
  const sx = vw / bounds.width, sy = vh / bounds.height;
  const x = Math.min(vw, Math.max(0, Math.floor(dx * sx)));
  const y = Math.min(vh, Math.max(0, Math.floor(dy * sy)));
  const right = Math.min(vw, Math.max(0, Math.ceil((dx + dw) * sx)));
  const bottom = Math.min(vh, Math.max(0, Math.ceil((dy + dh) * sy)));
  return {x, y, width: right - x, height: bottom - y};
}

function bounded(rect) {
  return rect && rect.width > 0 && rect.height > 0
    && rect.width <= MAX_CROP_DIMENSION && rect.height <= MAX_CROP_DIMENSION
    && rect.width * rect.height <= MAX_CROP_PIXELS;
}

// draw uses canvas-local CSS pixels, as returned by marioDraw. Packet geometry
// uses backing pixels, including on displays whose horizontal/vertical ratios
// differ. A hidden packet clears the previous native frame without pixel data.
export function cropPacket(canvas, draw, sequence, mode, presentable) {
  const packet = {
    sequence, mode, visible: false, x: 0, y: 0, width: 0, height: 0,
    viewport_width: canvas?.width ?? 0,
    viewport_height: canvas?.height ?? 0,
    pixels: null,
  };
  if (!presentable) return packet;
  if (mode === 'whole') return {...packet, visible: true};
  if (mode !== 'mario' || !canvas || !draw) return packet;
  const rect = cropRect(canvas, draw);
  if (!bounded(rect)) return packet;
  const {x, y, width, height} = rect;
  try {
    const bytes = canvas.getContext('2d').getImageData(x, y, width, height).data;
    return {...packet, visible: true, x, y, width, height, pixels: base64(bytes)};
  } catch {
    // A tainted/unavailable canvas must hide the last frame, never retain it.
    return packet;
  }
}

// submit never creates per-frame waiters: one send is active and one newest
// packet may wait. idle() shares the promise for the current drain, including
// any replacements submitted before it finishes. Failed sends are not retried.
export function createPresenter(send) {
  let pending = null, running = false;
  let idlePromise = Promise.resolve(), resolveIdle = null;

  async function drain() {
    while (pending !== null) {
      const packet = pending;
      pending = null;
      try { await send(packet); } catch { /* The next frame can still succeed. */ }
    }
    running = false;
    resolveIdle();
    resolveIdle = null;
  }

  function submit(packet) {
    if (packet == null) return;
    pending = packet;
    if (running) return;
    running = true;
    idlePromise = new Promise((resolve) => { resolveIdle = resolve; });
    void drain();
  }

  return {submit, idle: () => idlePromise};
}
