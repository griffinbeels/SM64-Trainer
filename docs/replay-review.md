# Reviewing an attempt

Open a replay from its attempt row. Click the video or press Space to play or
pause. Native recordings and downloaded videos share the playback controls.

The seek bar shows current / total time on its right. Mouse seeking returns
focus to review; keyboard focus still lets you adjust the slider normally.
The speaker toggles mute, with a red X when muted. Hover or keyboard-focus the
speaker to reveal volume. The speedometer and multiplier select playback speed.

- J shuttles backward; K pauses; L plays forward. Repeated J or L presses raise
  the requested speed through 1×, 2×, 4× and 8×. Reverse uses silent, bounded
  seeks because browser video does not reliably support negative playback rate.
- Left/Right step captured pictures where timing is available. Clicking a step,
  Start, or a position stops the shuttle so it cannot overwrite that selection.
- Drag across the input lanes to set and enable a loop, including both selected
  endpoint frames. A click still seeks. Unmapped or ambiguous video boundaries
  decline the selection instead of guessing.
- I sets In, O sets Out and X clears both markers. Completing both endpoints
  enables the loop. Play starts at In when outside the loop and continues from
  the current position when inside. Shift+I jumps to In without pausing playback.
- Hold K and tap J/L to step one picture backward/forward.
- Wheel over the input lanes to zoom around the pointer. The full-width bar
  below them shows the visible portion of the complete timeline; drag its thumb
  to pan. When focused, Left/Right pan and Home/End reach the two ends.
- Fullscreen includes the gameplay, controls and input timeline. Drag the
  horizontal divider, or focus it and press Up/Down, to adjust the split.
  The available range adapts to keep controls reachable in short windows.
  The input inspector and template tools sit beside the playback controls,
  above the lanes, and return below the lanes when leaving fullscreen.

Playback shortcuts belong to the active review. Clicking or tabbing outside
releases them; text fields retain their normal keys. Wheel gestures over the
lanes and navigator do not scroll or zoom the surrounding page.
When a newly opened replay becomes ready, its timeline receives focus only if
the opening is still the latest interaction and the tab stayed active. A slower
response cannot take ownership from a more recently opened replay. Clicking
inside a ready replay activates that replay's shortcuts.

Zoom and template shifting change inspection coordinates only. The input
inspector still follows delivered video pictures, with missing associations
left unknown. Downloaded footage does not acquire game-frame identity merely
by sharing a player.

Loop playback currently has a known boundary limitation: the browser can present
an out-of-range picture before the timer seeks back to A. The independently
decoded loop regression in `tests/test_ui_review_prototype.py` remains failing;
manual picture stepping passes. Its observer reads pixels before application
callbacks can seek, and retains bounds and media events with the failure.
Do not shorten the interval to hide this or treat the loop as frame-exact.
Browser frame callbacks are [best effort](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback).

## Extraction

Native H264 footage now copies its already-encoded video and audio into MP4.
The preceding keyframe stays in the file as decoder pre-roll, hidden by a
90 kHz edit list, so the visible cut begins on the same picture without another
video encode. Picture PTS, held intervals and source associations remain intact.
Only explicitly discarded negative-time packets are excluded from this native
output's visible picture index. Unsupported or reordered sources retain the
existing transcode/decoded-frame fallback.

The retained 30.198836-second Cavern attempt (#85, 880 pictures) measured
3.240 seconds before and 0.455 seconds after in an offline closed-footage
counterfactual. The entire source-PTS vector and visible timestamp vector matched
the original. The original segment CSV was removed at shutdown; the probe
reconstructed adjacent final holds and required this exact vector match. This
measurement excludes the live tail wait, HTTP and browser startup. It is not an
instant-completion claim. The original click-time stage measurements are absent.

Regression coverage includes `tests/test_replay_packet_probe.py`, pixel-for-pixel
native cuts in `tests/test_replay_picture_identity.py`, held-picture and audio
flash/click checks, and `tests/test_ui_replay_copy.py` for browser pre-roll handling.
The packet-copy mechanism is described in the
[FFmpeg streamcopy documentation](https://ffmpeg.org/ffmpeg.html#Streamcopy).
Use the
[profiling guide](profiling.md) to measure complete extraction and review latency;
the current service still waits for closed segments covering the post-attempt
tail. It has no during-attempt final-MP4 preparation worker or progressive tail.

## Resource cost

The [capture operation audit](replay-pipeline-cost.md) identifies which desktop-era
paths are inactive, which copies were removed, and which GPU/CPU transfers remain.
Accepted plugin pictures now prepare BGRA once; rejected pictures skip the full
conversion. The NUT feed wraps those bytes directly instead of copying through a
padded video frame and rawvideo encoder. Exact pixel/timestamp/audio regressions
and isolated before/after measurements are recorded in that audit.

The preceding optimization made bottom-up BGR to top-down BGRA conversion copy each channel in bulk
into a fresh owned buffer. This preserves row orientation, padding, alpha and
retained heartbeat pictures. Eight decoded pictures from the saved Whomp's
Fortress 100 Coins 1'29"96 example (1600×1200) were compared in six alternating
before/after rounds: median conversion CPU time fell from 7.08 to 3.42 ms per
picture, with identical output pixels. This isolates conversion cost; it does
not measure total machine load or live recording overhead.

During browser playback, static input bars and markers retain their rendered
subtree while the playhead and inspector follow delivered pictures. Source,
mapping, zoom, template and visibility changes invalidate the relevant cache.
`tests/test_ui_input_timeline_performance.py` checks a thousand-marker timeline
and verifies that a cached bar seeks the replacement map correctly.

The J/K/L convention follows the [DaVinci Resolve editor guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Editors-Guide.pdf).
Browser reverse playback limitations are described in
[MDN's playbackRate reference](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/playbackRate).
