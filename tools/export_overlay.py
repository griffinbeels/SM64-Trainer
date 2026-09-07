# tools/export_overlay.py
"""Export one attempt's inputs as TRANSPARENT overlay layers for an editor.

    uv run python tools/export_overlay.py --attempt 40213
    uv run python tools/export_overlay.py --attempt 40213 --codec qtrle
    uv run python tools/export_overlay.py --attempt 40213 --layers stick

Writes one .mov per layer into `replays/overlays/`, all on the same canvas at
the same frame rate with the same frame 0 -- so they STACK in register in an
editor and he deletes the layer he does not want instead of cropping it.

**Why a browser renders these.** The pictures come from the same
`ControllerPanel` the timeline's frame inspector draws (`ui/overlay.html`
mounts it and nothing else). A second drawing routine in Python would be a
second controller, and the one people would notice drifting is the one that
ends up in a video.

**Why so few screenshots.** The pad holds still most of the time, so the plan
collapses a track to its DISTINCT pictures -- 45 s of real play is 1,348
frames and a few hundred pictures -- and the concat script points at them one
line per output frame. Exact by construction: the file count IS the frame
count.

**Alignment against the footage is NOT assumed here.** The clip is captured on
a wall clock and the inputs on the game's frame counter, so frame 0 lining up
is a claim. Record a clip with Usamune's own input display on and score this
overlay against Usamune's own pixels in that same footage before trusting a
cut to the frame.
"""
import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from find_uilab import find_uilab                       # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.core.paths import (candidate_server_ports,  # noqa: E402
                                    overlays_dir)
from sm64_events.inputs.overlay import (DEFAULT_CODEC, DEFAULT_VIDEO_FPS,
                                        CODECS, LAYERS, concat_script,
                                        encode_argv, mapped_concat_script,
                                        output_name,
                                        plan_overlay, plan_mapped_overlay)  # noqa: E402
from sm64_events.memory import addresses as A           # noqa: E402


def ffmpeg_path() -> str:
    from sm64_events.core.paths import bundled_ffmpeg
    bundled = bundled_ffmpeg()
    if bundled:
        return str(bundled)
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("no ffmpeg on PATH and none bundled")
    return found


def running_base() -> str | None:
    """A server already serving /ui, or None.

    The port list comes from `core/paths.py`, which owns it -- a literal here
    would be a second source of truth for the one fact that decides whether a
    tool talks to the exe, a dev server, or the instance he is actually
    playing on.
    """
    for port in candidate_server_ports():
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=0.6):
                return f"http://127.0.0.1:{port}"
        except Exception:
            continue
    return None


def track_from_server(base: str, attempt_id: int) -> dict:
    with urllib.request.urlopen(
            f"{base}/api/attempts/{attempt_id}/inputs") as response:
        return json.load(response)


def clip_view_from_server(base: str, attempt_id: int) -> dict | None:
    """The attempt's replay view -- carrying the clip's `frame_map` when one
    was recorded. None when there is no clip to line up with (no footage,
    aged out): the uniform script is then the only honest export."""
    request = urllib.request.Request(
        f"{base}/api/attempts/{attempt_id}/replay", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 409:
            try:
                detail = json.loads(error.read(65536)).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if detail in ("no footage in the replay buffer",
                          "no footage overlaps the requested span"):
                return None
        raise SystemExit(f"could not load the replay for overlay export: {error}") from error
    except OSError as error:
        raise SystemExit(f"could not load the replay for overlay export: {error}") from error


def _show(page, state, settings) -> dict:
    """Draw one state and return the stage's box.

    A BARE expression, never an arrow: uilab's driver wraps what it is given
    in `() => { return (...); }`, so handing it a function returns the
    function and comes back None -- which looks exactly like a page that drew
    nothing (measured 2026-08-21).
    """
    return page.evaluate(
        "window.showState(%s, %s)"
        % (json.dumps(list(state) if state else None), json.dumps(settings)))


def widest_canvas(page, table, settings) -> dict:
    """ONE canvas for every layer and every frame, sized by the widest state.

    Every layer must share a canvas or the files do not stack in register, and
    register is the whole reason there are layers instead of one file to crop.
    So the box is taken once, from the COMBINED layer holding every button
    with the stick at full deflection -- the largest thing any layer can draw
    -- and then used for all of them.
    """
    every_button = 0
    for bit, _name in table:
        every_button |= bit
    reach = settings["stickMax"]
    box = _show(page, (every_button, reach, reach, 0),
                {**settings, "layer": "combined"})
    if not box:
        raise SystemExit(
            "the overlay page returned no stage box -- it did not render, so "
            "there is nothing to size the canvas from")
    return {"x": float(box["x"]), "y": float(box["y"]),
            "width": float(box["width"]), "height": float(box["height"])}


def render_states(base: str, plan, out_dir: Path, table, stick_max,
                  dead_zone, size: int, canvas: dict | None = None,
                  angle_units: int = 0x10000):
    """One transparent PNG per distinct picture, drawn by the real component."""
    missing = find_uilab()
    if missing:
        raise SystemExit(missing)
    from uilab.driver import get_driver

    files: list[Path] = []
    settings = {"buttons": table, "stickMax": stick_max,
                "deadZone": dead_zone, "layer": plan.layer, "size": size,
                "angleUnits": angle_units}
    with get_driver().launch() as page:
        page.goto(f"{base}/ui/overlay.html")
        page.wait_for("#stage")
        # The page lifts the design system out of index.html before it is
        # ready. Shooting before that lands renders UNSTYLED shapes -- an SVG
        # circle with no declared fill is black, which drew the stick box as a
        # filled disc (2026-08-21).
        for _ in range(100):
            if page.evaluate("window.overlayReady === true"):
                break
            if page.evaluate("window.overlayError || null"):
                raise SystemExit(
                    "the overlay page could not load the design system: "
                    f"{page.evaluate('window.overlayError')}")
            page.wait_ms(50)
        else:
            raise SystemExit("the overlay page never became ready")
        if canvas is None:
            canvas = widest_canvas(page, table, settings)
        for index, state in enumerate(plan.states):
            # The blank state draws nothing at all -- a hole in capture is a
            # hole in the overlay, not the last pad held on screen.
            shots = {}
            for background in ("black", "white"):
                _show(page, state, {**settings, "bg": background})
                page.wait_ms(16)
                shots[background] = page.screenshot(clip=canvas)
            target = out_dir / f"s{index:05d}.png"
            target.write_bytes(recover_alpha(shots["black"], shots["white"], empty=state is None))
            files.append(target)
    return files, canvas


def recover_alpha(over_black: bytes, over_white: bytes, *, empty: bool = False) -> bytes:
    """True RGBA from two OPAQUE shots of the same picture.

    Compositing says `P = C*a + B*(1 - a)`, so over black `Pb = C*a` and over
    white `Pw = C*a + (1 - a)`; subtracting gives `a = 1 - (Pw - Pb)` and then
    `C = Pb / a`. Exact, anti-aliased edges included.

    This exists because the shared browser rig does not expose a transparent
    capture. Its permanent home is uilab's own `screenshot(omit_background=)`,
    and this function is what should be DELETED when that lands. It was not
    added there today because that repo was mid-change with uncommitted work
    in the two files this would touch, and several projects depend on it.

    Without this the export looked completely correct on every metadata check
    -- `pix_fmt=yuva444p12le`, an alpha channel present and named -- while
    every pixel in it was opaque, because the browser had painted its own
    background underneath (measured 2026-08-21).
    """
    from PIL import Image

    black = Image.open(io.BytesIO(over_black)).convert("RGB")
    white = Image.open(io.BytesIO(over_white)).convert("RGB")
    if black.size != white.size:
        raise SystemExit("the two capture passes disagree on size")
    out = Image.new("RGBA", black.size)
    black_px, white_px, out_px = black.load(), white.load(), out.load()
    for y in range(black.size[1]):
        for x in range(black.size[0]):
            br, bg, bb = black_px[x, y]
            wr, wg, wb = white_px[x, y]
            # One alpha per pixel, averaged over the channels: they agree in
            # theory and differ by a unit or two in practice (rounding in the
            # PNG encode), and averaging is steadier than picking one.
            alpha = 255 - ((wr - br) + (wg - bg) + (wb - bb)) / 3
            alpha = max(0.0, min(255.0, alpha))
            if alpha <= 0.5:
                out_px[x, y] = (0, 0, 0, 0)
                continue
            scale = 255.0 / alpha
            out_px[x, y] = (min(255, round(br * scale)),
                            min(255, round(bg * scale)),
                            min(255, round(bb * scale)),
                            round(alpha))
    if empty:
        # The shared inspector still draws scaffolding for unknown input.
        # An unknown overlay interval must instead be completely transparent.
        out.putalpha(0)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    return buffer.getvalue()


def encode(plan, files: list[Path], out_path: Path, work: Path) -> None:
    script = work / f"{plan.layer}.ffconcat"
    if plan.frame_times is not None:
        text = mapped_concat_script(plan, lambda index: files[index].name)
    else:
        text = concat_script(plan, lambda index: files[index].name)
    script.write_text(text, encoding="utf-8", newline="\n")
    argv = encode_argv(ffmpeg_path(), str(script), str(out_path), plan)
    result = subprocess.run(argv, capture_output=True, text=True,
                            encoding="utf-8", cwd=str(work),
                            **quiet_spawn_kwargs())
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed:\n{result.stderr[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--plain", action="store_true",
                        help="export the input track on the game clock without replay alignment")
    parser.add_argument("--layers", default=",".join(LAYERS),
                        help=f"comma-separated; any of {', '.join(LAYERS)}")
    parser.add_argument("--codec", default=DEFAULT_CODEC,
                        choices=sorted(CODECS))
    parser.add_argument("--fps", type=int, default=DEFAULT_VIDEO_FPS,
                        help="frame rate for a plain track; mapped clips retain their picture times")
    parser.add_argument("--size", type=int, default=200,
                        help="the stick box's edge, in pixels")
    parser.add_argument("--out", default=None)
    parser.add_argument("--base", default=None,
                        help="a server base URL, instead of hunting "
                             "the known ports")
    args = parser.parse_args()

    base = args.base or running_base()
    if base is None:
        return _no_server()
    view = None if args.plain else clip_view_from_server(base, args.attempt)
    # A mapped picture already carries its pad. Fetching a raw-counter track
    # can reject an otherwise usable clip spanning two source occurrences.
    data = (track_from_server(base, args.attempt) if view is None else {
        "buttons": A.BUTTON_BITS, "stick_max": A.STICK_MAX,
        "dead_zone": A.STICK_DEAD_ZONE, "angle_units": A.ANGLE_UNITS})
    if view is None and not data["runs"]:
        print(f"attempt #{args.attempt} has no captured input, so there is "
              "nothing to draw. That is a finding, not an error.")
        return 3

    plans = []
    for layer in [name.strip() for name in args.layers.split(",") if name.strip()]:
        settings = dict(layer=layer, codec=args.codec, video_fps=args.fps)
        try:
            plans.append(plan_mapped_overlay(view, **settings) if view is not None
                         else plan_overlay(data["runs"], **settings))
        except ValueError as error:
            raise SystemExit(f"{error}. Use --plain for a track without replay alignment.") from error
    out_dir = Path(args.out) if args.out else overlays_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"attempt-{args.attempt}"
    written = []
    canvas = None      # decided once, shared by every layer -- see widest_canvas
    for plan in plans:
        with tempfile.TemporaryDirectory(prefix="sm64-overlay-") as work_name:
            work = Path(work_name)
            files, canvas = render_states(
                base, plan, work, data["buttons"], data["stick_max"],
                data["dead_zone"], args.size, canvas,
                data.get("angle_units", 0x10000))
            out_path = out_dir / output_name(stem, plan)
            encode(plan, files, out_path, work)
        written.append((out_path, plan, len(files)))

    print(f"read attempt #{args.attempt} from {base}\n")
    for out_path, plan, pictures in written:
        print(f"  {out_path}")
        clock = (f"over {plan.duration_s:.6f}s at the clip's picture times"
                 if plan.frame_times is not None else f"at {plan.video_fps} fps")
        print(f"    {plan.video_frames} frames {clock}, {pictures} distinct pictures, {plan.codec}")
    if view is not None:
        print("\nMapped to the clip's captured states and picture times. Drop it "
              "at 0:00 over that clip; unknown intervals are transparent.")
    else:
        print("\nPlain input track on the game clock; no replay alignment is available.")
    print("\nEvery layer is the same canvas, clock and frame 0, so they "
          "stack in register — drop them on the timeline and delete the one "
          "you do not want. Nothing needs cropping.")
    print("Alignment against footage is NOT verified by this tool: record a "
          "clip with Usamune's own input display on and score this against "
          "its pixels before cutting to the frame.")
    return 0


def _no_server() -> int:
    ports = "/".join(str(port) for port in candidate_server_ports())
    print(f"no trainer server answered on {ports}.\n"
          "This tool reads the track over the API rather than the db so it "
          "cannot disagree with what the timeline draws — start the app (or "
          "run-test-server.bat) and try again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
