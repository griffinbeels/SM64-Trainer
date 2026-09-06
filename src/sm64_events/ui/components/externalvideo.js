// One external recording player for the Library and attempt replay panel.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { videoSource, youtubeEmbed } from "./librarymodel.js";
import { Icon } from "./icons.js";
import { publicRecordingUrl } from "./recordinglink.js";
import { ReplayTransport } from "./replaytransport.js";
import { jumpToStart } from "../frame.js";

const html = htm.bind(h);

// One shared volume for every replay player — current and future. The last
// user adjustment wins everywhere: changing volume on any player fans out
// to all mounted players and persists (localStorage) for players not yet
// opened, including after a reload. Default 30% — game audio is loud
// against an otherwise-silent page; a stored user choice overrides it.
const VOLUME_KEY = "replay_volume";

function storedVolume() {
  let v = NaN;
  try { v = parseFloat(localStorage.getItem(VOLUME_KEY)); } catch { /* storage unavailable */ }
  return v >= 0 && v <= 1 ? v : 0.3;   // NaN fails both comparisons
}

let applyingVolume = false; // re-entrancy guard: our fan-out, not the user

export function attachSharedVolume(el) {
  el.volume = storedVolume(); // before addEventListener: must not self-fire
  el.addEventListener("volumechange", () => {
    if (applyingVolume) return;
    try { localStorage.setItem(VOLUME_KEY, String(el.volume)); } catch { /* storage unavailable */ }
    applyingVolume = true;
    document.querySelectorAll(".replay-player video").forEach((v) => {
      if (v !== el) v.volume = el.volume;
    });
    applyingVolume = false;
  });
}



function ProviderMedia({ url, label, source, startS }) {
  const [bsky, setBsky] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    if (source?.kind === "bsky" && !source.embed) {
      fetch("https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"
        + `?handle=${encodeURIComponent(source.actor)}`)
        .then((response) => response.json())
        .then((body) => {
          if (alive && body.did) setBsky(
            `https://embed.bsky.app/embed/${body.did}/app.bsky.feed.post/${source.rkey}`);
        }).catch(() => alive && setFailed(true));
    }
    return () => { alive = false; };
  }, [url]);
  const embed = source?.kind === "youtube" && startS != null
    ? youtubeEmbed(url, startS) : source?.embed || bsky;
  if (source?.kind === "file" && !failed)
    return html`<video class="library-example-thumb" src=${url} controls muted
      preload="metadata" title=${label} onerror=${() => setFailed(true)} />`;
  if (embed && !failed)
    return html`<iframe class="library-embed" src=${embed} title=${label}
      allow="autoplay; encrypted-media" allowfullscreen onerror=${() => setFailed(true)} />`;
  return html`<div class="library-example-thumb library-example-placeholder">
    <span>Watch this recording on ${source?.site || "the original site"}.</span>
  </div>`;
}

function CachedVideo({ media, label, startS, onError, replayActions, onCompare }) {
  const videoRef = useRef(null);
  const initialSeek = useRef(false);
  const [playing, setPlaying] = useState(false);
  const stepS = Number(media.frame_step_s);
  const canStep = Number.isFinite(stepS) && stepS > 0;
  function step(direction) {
    const video = videoRef.current;
    if (!video || !canStep) return;
    video.pause();
    video.currentTime = Math.max(0, Math.min(video.duration || Infinity,
      (Math.floor(video.currentTime / stepS) + direction + 0.5) * stepS));
  }
  function toStart() {
    const video = videoRef.current;
    jumpToStart(video, Math.min(startS ?? media.start_s ?? 0, video?.duration || Infinity));
  }
  function togglePlay() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  }
  function saveReplay() {
    const link = document.createElement("a");
    link.href = media.clip_url; link.download = "recording.mp4";
    link.click();
  }
  return html`<div class="replay-player external-video-local">
    <video class="library-example-thumb" src=${media.clip_url} title=${label}
      controls preload="metadata" ref=${(element) => {
        videoRef.current = element;
        if (element && !element.dataset.sharedVolume) {
          element.dataset.sharedVolume = "1"; attachSharedVolume(element);
        }
      }} onerror=${onError} onplay=${() => setPlaying(true)} onpause=${() => setPlaying(false)}
      onloadedmetadata=${() => {
        if (initialSeek.current) return;
        initialSeek.current = true;
        const video = videoRef.current;
        video.currentTime = Math.min(startS ?? media.start_s ?? 0, video.duration || Infinity);
        video.play().catch(() => {});
      }} />
    <${ReplayTransport} playing=${playing} onStart=${toStart} onStep=${step}
      onToggle=${togglePlay} canStep=${canStep} frameKind="encoded video" />
    ${replayActions && html`<div class="replay-actions">
      <button onclick=${saveReplay} title="Save a copy of this recording">
        <${Icon} name="save" size=${15} /> Save replay</button>
      ${onCompare && html`<button onclick=${() => onCompare({ clip_url: media.clip_url,
          start_s: startS ?? media.start_s ?? 0 })} title="Open this run in the Compare tab">
        <${Icon} name="compare" size=${15} /> Compare</button>`}
    </div>`}
  </div>`;
}

export function ExternalVideo({ url, label = "Public recording", autoplay = false, startS = null,
                                closable = false, replayActions = false, onCompare }) {
  const [media, setMedia] = useState(null);
  const [playing, setPlaying] = useState(autoplay);
  const [localFailed, setLocalFailed] = useState(false);
  const [downloadAttempt, setDownloadAttempt] = useState(0);
  const safeUrl = publicRecordingUrl(url);
  const source = videoSource(safeUrl,
    typeof location !== "undefined" ? location.hostname : null);

  useEffect(() => {
    if (!safeUrl || source?.kind === "image") return undefined;
    let alive = true;
    let timer;
    const endpoint = `/api/media?url=${encodeURIComponent(safeUrl)}`;
    function accept(result) {
      if (!alive) return;
      setMedia(result);
      if (result.state === "running" && playing && downloadAttempt)
        timer = setTimeout(() => getJSON(endpoint).then(accept).catch(failed), 1000);
    }
    function failed(failure) {
      if (alive) setMedia({ state: "error", error: String(failure) });
    }
    getJSON(endpoint).then(async (result) => {
      if (!alive) return;
      // Opening/playing the provider never downloads. Only Download opts into
      // preparation, which also probes timing when a cached file already exists.
      if (playing && downloadAttempt)
        result = await send("POST", "/api/media", {
          url: safeUrl, retry: downloadAttempt > 1 || result.state === "error",
        });
      accept(result);
    }).catch(failed);
    return () => { alive = false; clearTimeout(timer); };
  }, [safeUrl, playing, downloadAttempt]);

  if (!source) return null;
  const useLocal = downloadAttempt > 0 && !localFailed && media?.state === "ready";
  function download() {
    setMedia({ state: "running", start_s: media?.start_s });
    setLocalFailed(false);
    setDownloadAttempt((attempt) => attempt + 1);
  }
  function closePlayback() {
    setMedia(null);
    setLocalFailed(false);
    setPlaying(false);
    setDownloadAttempt(0);
  }
  return html`<div class="library-example-media external-video">
    <${VideoBody} source=${source} playing=${playing} useLocal=${useLocal}
      url=${safeUrl} media=${media} label=${label} startS=${startS}
      replayActions=${replayActions} onCompare=${onCompare}
      onPlay=${() => setPlaying(true)} onError=${() => setLocalFailed(true)} />
    <${VideoActions} url=${safeUrl} playing=${playing} media=${media}
      showOriginal=${!replayActions} localFailed=${localFailed}
      downloadRequested=${downloadAttempt > 0} canDownload=${source.kind !== "image"}
      onDownload=${download}>
      ${playing && closable && html`<button onclick=${closePlayback}>Close recording</button>`}
    <//>
  </div>`;
}

function VideoBody({ source, playing, useLocal, url, media, label, startS, onPlay, onError,
                     replayActions, onCompare }) {
  const image = source.kind === "image";
  return image ? html`<img class="library-example-thumb" src=${source.thumb} alt=${label} loading="lazy" />`
      : !playing ? html`<button class="library-example-thumb library-example-placeholder is-clickable"
        onclick=${onPlay} title="Play inline" aria-label=${`Play ${label}`}>
        <${Icon} name="play" size=${30} />
        <span class="library-example-site">${useLocal ? "Play recording" : `Play on ${source.site}`}</span>
        ${source.thumb && html`<img class="library-example-thumb library-example-thumb-img"
          src=${source.thumb} alt="" loading="lazy"
          onerror=${(event) => event.target.remove()} />`}
      </button>` : useLocal ? html`<${CachedVideo} key=${media.clip_url} media=${media}
        label=${label} startS=${startS} onError=${onError}
        replayActions=${replayActions} onCompare=${onCompare} />`
      : !media ? html`<div class="library-example-thumb library-example-placeholder" role="status">
          Loading recording…</div>`
      : html`<${ProviderMedia} key=${url} url=${url} source=${source}
          label=${label} startS=${startS ?? media.start_s} />`;
}

function VideoActions({ url, playing, media, showOriginal, localFailed, downloadRequested,
                        canDownload, onDownload, children }) {
  return html`<div class="external-video-actions">
      ${showOriginal && html`<a href=${url} target="_blank" rel="noopener noreferrer">Open original</a>`}
      ${playing && canDownload && !downloadRequested && html`
        <button onclick=${onDownload}>Download</button>
        <span class="meta">Download to enable full replay features.</span>`}
      ${playing && downloadRequested && media?.state === "running"
        && html`<span role="status">Downloading…</span>`}
      ${playing && downloadRequested && media?.state === "error" && html`<span role="status">
        Local video unavailable. <button title=${media.error || "Try preparing the video again"}
          onclick=${onDownload}>Retry download</button></span>`}
      ${localFailed && html`<span role="status">Local playback unavailable; showing original.</span>`}
      ${children}
    </div>`;
}
