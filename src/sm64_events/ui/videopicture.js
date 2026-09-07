// The picture Chromium actually presented, shared by every reader of a video.
// A seek request/currentTime is not evidence that that picture was displayed.
const pictures = new WeakMap();

// undefined = no observer; null = observed, but no picture has been delivered.
export function presentedVideoTime(video) {
  return pictures.get(video)?.time;
}

export function watchVideoPicture(video, listener) {
  if (typeof video.requestVideoFrameCallback !== "function") {
    listener(null);
    return () => {};
  }
  let state = pictures.get(video);
  if (!state) {
    state = { listeners: new Set(), time: null, handle: null };
    pictures.set(video, state);
  }
  const notify = () => state.listeners.forEach((read) => read(state.time));
  if (!state.listeners.size) {
    const source = video.currentSrc || video.src;
    if (state.source !== source) state.time = null;
    state.source = source;
    const presented = (_now, meta) => {
      state.time = meta.mediaTime;
      state.handle = video.requestVideoFrameCallback(presented);
      notify();
    };
    state.emptied = () => {
      state.time = null;
      state.source = video.currentSrc || video.src;
      notify();
    };
    video.addEventListener("emptied", state.emptied);
    state.handle = video.requestVideoFrameCallback(presented);
  }
  state.listeners.add(listener);
  listener(state.time); // A late subscriber reads the same last presented picture.
  return () => {
    state.listeners.delete(listener);
    if (!state.listeners.size) {
      video.cancelVideoFrameCallback(state.handle);
      video.removeEventListener("emptied", state.emptied);
      state.handle = null;
    }
  };
}
