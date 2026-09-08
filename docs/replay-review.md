# Reviewing an attempt

Open a replay from its attempt row. Click the video or press Space to play or
pause. Native recordings and downloaded videos share the playback controls.

- J shuttles backward; K pauses; L plays forward. Repeated J or L presses raise
  the requested speed through 1×, 2×, 4× and 8×. Reverse uses silent, bounded
  seeks because browser video does not reliably support negative playback rate.
- Left/Right step captured pictures where timing is available. Clicking a step,
  Start, or a position stops the shuttle so it cannot overwrite that selection.
- Wheel over the input lanes to zoom around the pointer. The full-width bar
  below them shows the visible portion of the complete timeline; drag its thumb
  to pan. When focused, Left/Right pan and Home/End reach the two ends.
- Fullscreen includes the gameplay, controls and input timeline. Drag the
  horizontal divider, or focus it and press Up/Down, to adjust the split.
  The available range adapts to keep controls reachable in short windows.

Playback shortcuts belong to the active review. Clicking or tabbing outside
releases them; text fields retain their normal keys. Wheel gestures over the
lanes and navigator do not scroll or zoom the surrounding page.

Zoom and template shifting change inspection coordinates only. The input
inspector still follows delivered video pictures, with missing associations
left unknown. Downloaded footage does not acquire game-frame identity merely
by sharing a player.

The A/B loop currently has a known boundary limitation: the browser can present
an out-of-range picture before the timer seeks back to A. The independently
decoded loop regression in `tests/test_ui_review_prototype.py` remains failing;
manual picture stepping passes. Its observer reads pixels before application
callbacks can seek, and retains bounds and media events with the failure.
Do not shorten the interval to hide this or treat the loop as frame-exact.
Browser frame callbacks are [best effort](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback).

## Extraction

Native H264 timestamp probing can read packet PTS instead of decoding pixels.
The fast path requires the native encoder contract and verified stream/packet
properties; unsupported or reordered media uses decoded-frame probing. This
does not change encoding, audio, cut boundaries or source-PTS association.
Regression coverage lives in `tests/test_replay_packet_probe.py`, alongside the
real encoder picture-identity and held-picture tests. Use the
[profiling guide](profiling.md) to measure complete extraction and review latency;
a faster timestamp probe alone does not establish immediate replay readiness.

The J/K/L convention follows the [DaVinci Resolve editor guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Editors-Guide.pdf).
Browser reverse playback limitations are described in
[MDN's playbackRate reference](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/playbackRate).
