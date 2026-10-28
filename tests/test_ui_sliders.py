"""Every slider reaches its ends: at its min and max nothing shows past the thumb.

His report, 2026-10-26, on the Scene viewer's frame slider at its last frame:
"when the sliders are at their mins/maxes, there should be NOTHING remaining
... otherwise it feels like there's a bug that prevents us from going to the
true min / max. We should fix this everywhere in our entire app." Chromium
paints a native range's track across its padding and border while the thumb
travels only the content box, so the shared input padding left 8.5px of track
past the thumb at max and fill before it at min. index.html's one
`input[type=range]` rule owns that geometry now.

The browser test renders every slider kind in its real stylesheets at min and
max and reads, from the pixels the input itself paints (the difference from
the same shot with the input hidden), how far anything reaches past the
thumb's disc on the side it has run out of. The source scan keeps the list
complete: a range added to any file fails until it is rendered here, and a
page that renders one must load the design system that owns the rule.
"""
import io
import math
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
SCALES = (1, 1.5)                       # his desktop runs at 150%
PAD = 6                                 # css px of page around each input in its shot

# Each kind: (name, source range as (file, nth range in that file), page it
# renders in, markup around it, its attributes). The markup is the component's
# own wrapper classes, so the real rules that reach the input apply.
KINDS = [
    ("replay-seek", ("components/replaytransport.js", 0), "index.html",
     '<div class="replay-controls" style="width:600px"><div class="replay-seek-row">{input}'
     '<span class="replay-media-time">0:00.00 / 0:10.00</span></div></div>', 'min="0" max="9.98" step="any"'),
    ("replay-volume", ("components/replayoptions.js", 0), "index.html",
     '<div class="replay-sound"><div class="replay-volume-popover" style="display:flex;position:static">'
     '{input}</div></div>', 'class="replay-volume" min="0" max="1" step=".01"'),
    ("how-it-works", ("components/howitworks.js", 0), "index.html",
     '<div class="hiw-frames-demo" style="width:320px"><label class="hiw-slider"><span>343 frames</span>{input}'
     '</label></div>', 'min="300" max="360"'),
    ("ghost-distance", ("components/ghostproximity.js", 0), "index.html",
     '<div class="ghost-proximity-body"><label class="ghost-field"><span class="ghost-field-name">Extra distance'
     '</span>{input}<span class="ghost-field-value">400 units</span></label></div>',
     'class="ghost-distance" min="0" max="400" step="10"'),
    ("ghost-name-box", ("components/ghostswitch.js", 0), "index.html",
     '<div class="ghost-name-controls"><div class="ghost-field ghost-name-box"><span class="ghost-field-name">'
     'Background</span>{input}<span class="ghost-field-value">60%</span></div></div>',
     'class="ghost-name-box-range" min="0" max="1" step="0.05"'),
    ("ghost-path", ("components/ghostswitch.js", 1), "index.html",
     '<div class="ghost-path-tune-body"><label class="ghost-field"><span class="ghost-field-name">Ahead</span>'
     '{input}<span class="ghost-field-value">3 s</span></label></div>',
     'class="ghost-path-range" min="0.5" max="10" step="0.5"'),
    ("ghost-visibility", ("components/ghostswitch.js", 2), "index.html",
     '<label class="ghost-field"><span class="ghost-field-name">Visibility</span>{input}'
     '<span class="ghost-field-value">100%</span></label>', 'class="ghost-opacity" min="0.15" max="1" step="0.05"'),
    ("ghost-volume", ("components/ghostswitch.js", 3), "index.html",
     '<label class="ghost-field ghost-volume"><span class="ghost-field-name">Volume</span>{input}'
     '<span class="ghost-field-value">30%</span></label>', 'class="ghost-volume-range" min="0" max="1" step="0.05"'),
    ("ghost-colour", ("components/ghostswitch.js", 4), "index.html",
     '<div class="ghost-field ghost-colour"><span class="ghost-field-name">Colour</span>{input}'
     '<span class="ghost-field-value">250°</span></div>', 'class="ghost-hue" min="0" max="359" step="1"'),
    ("scene-frame", ("components/sceneviewer.js", 0), "index.html",
     '<div class="scene-lab" style="width:900px"><div class="scene-lab-bar"><button type="button">Back 1</button>'
     '<button type="button" class="primary">Play</button><button type="button">Forward 1</button>{input}'
     '<span class="scene-lab-frame">Frame 120 of 120<small>level 9, area 1</small></span></div></div>',
     'min="0" max="119"'),
    ("scene-volume", ("components/sceneaudio.js", 0), "index.html",
     '<div class="scene-lab" style="width:900px"><div class="scene-lab-bar scene-lab-sound">'
     '<button type="button">Mute</button>{input}<span class="scene-lab-sound-note">sound</span></div></div>',
     'min="0" max="1" step="0.01"'),
    ("scene-rig", ("components/scenerigs.js", 0), "index.html",
     '<div class="scene-lab" style="width:900px"><div class="scene-lab-bar scene-lab-rig-options">'
     '<label class="scene-lab-rig-range">Speed{input}<output>30°/s</output></label></div></div>',
     'min="-180" max="180" step="1"'),
    ("scene-tilt", ("components/scenelens.js", 0), "index.html",
     '<div class="scene-lab" style="width:900px"><div class="scene-lab-bar scene-lab-lens">'
     '<label class="scene-lab-rig-range">Tilt{input}<output>0°</output></label></div></div>',
     'min="-180" max="180" step="1"'),
    ("scene-lens", ("components/scenelens.js", 1), "index.html",
     '<div class="scene-lab" style="width:900px"><div class="scene-lab-bar scene-lab-lens">'
     '<label class="scene-lab-rig-range">Lens{input}<output>31 mm</output></label></div></div>',
     'min="0" max="1000" step="1"'),
    ("scene-tune", ("tunecontrols.js", 0), "index.html",
     '<section class="practice-card scene-lab-tune" style="width:320px"><div class="scene-lab-tune-groups"><div>'
     '<div class="tune-row"><label>Fly speed</label><input type="number" value="1">{input}</div></div></div>'
     '</section>', 'min="0" max="10" step="0.5"'),
    ("stream-overlay-size", ("components/streamoverlay.js", 0), "index.html",
     '<fieldset class="sov-group" style="width:300px"><label class="sov-field"><span>Size</span>{input}'
     '<output>100%</output></label></fieldset>', 'min="0.5" max="3" step="0.05"'),
    ("compare-volume", ("components/videosync.js", 0), "index.html",
     '<div style="width:300px"><div class="vaudio"><button class="meta">M</button>{input}</div></div>',
     'min="0" max="1" step="0.05"'),
    ("marelo-bar-start", ("tunemarelo.js", 0), "tunemarelo.html",
     '<div class="tune-panel" style="position:static;height:auto;width:380px"><div class="tune-row">'
     '<label>Bar at start (%)</label><input type="number" value="0">{input}</div></div>', 'min="0" max="100" step="1"'),
    ("marelo-bar-end", ("tunemarelo.js", 1), "tunemarelo.html",
     '<div class="tune-panel" style="position:static;height:auto;width:380px"><div class="tune-row">'
     '<label>Bar at end (%)</label><input type="number" value="0">{input}</div></div>', 'min="0" max="100" step="1"'),
] + [
    (f"tuning-{page.removesuffix('.html')}", ("tunecontrols.js", 0), page,
     '<div class="tune-panel" style="position:static;height:auto;width:380px"><div class="tune-row">'
     '<label>Hold (ms)</label><input type="number" value="0">{input}</div></div>', 'min="0" max="1000" step="10"')
    for page in ("tune.html", "tunefeed.html", "tunelog.html", "tuneselector.html")
]

# Every spelling of a range input in markup or script; `[type=range]` is a
# CSS or query selector, never a slider.
RANGE = re.compile(r"(?<!\[)\btype\s*=\s*[\"']?range\b|\btype\s*:\s*[\"']range[\"']"
                   r"|\.type\s*=\s*[\"']range[\"']|setAttribute\(\s*[\"']type[\"']\s*,\s*[\"']range[\"']")
IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+|<script[^>]*\bsrc=)["']([^"']+\.js)["']""")


def _style_block(page: str) -> str:
    source = (UI / "index.html").read_text(encoding="utf-8")
    sheets = [source[source.index("<style>") + 7: source.index("</style>")]]
    if page == "index.html":
        sheets.append((UI / "scenelab.css").read_text(encoding="utf-8"))
    else:
        rig = (UI / page).read_text(encoding="utf-8")
        rig = rig[rig.index('<style id="tune-style">') + 23:]
        sheets.append(rig[:rig.index("</style>")].replace("html:not([data-styled]) body { visibility: hidden; }", ""))
    return "".join(f"<style>{sheet}</style>" for sheet in sheets)


def _markup(source: str) -> str:
    """Script and markup only: HTML comments and <style> blocks hold no slider."""
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    source = re.sub(r"<style\b.*?</style>", "", source, flags=re.S)
    return strip_comments(source)


def ranges_in_source() -> set[tuple[str, int]]:
    found = set()
    for path in sorted(UI.rglob("*")):
        if path.suffix not in (".js", ".html", ".mjs") or "vendor" in path.parts:
            continue
        name = path.relative_to(UI).as_posix()
        found |= {(name, nth) for nth, _ in enumerate(RANGE.finditer(_markup(path.read_text(encoding="utf-8"))))}
    return found


def _reaches(page: Path, files: set[str]) -> bool:
    """Whether the page's module graph imports any of `files` (paths under ui/)."""
    seen, todo = set(), [page]
    while todo:
        path = todo.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for target in IMPORT.findall(_markup(path.read_text(encoding="utf-8"))):
            todo.append(UI / target.removeprefix("/ui/") if target.startswith("/ui/") else path.parent / target)
    return any((UI / name).resolve() in {p.resolve() for p in seen} for name in files)


def overhang(shown: Image.Image, hidden: Image.Image, at: str) -> float:
    """Device px that anything the input paints reaches past the thumb's disc,
    on the side the thumb has run out of (right at max, left at min)."""
    a, b = shown.load(), hidden.load()
    painted = [(x, y) for x in range(shown.width) for y in range(shown.height)
               if max(abs(p - q) for p, q in zip(a[x, y], b[x, y], strict=True)) > 24]
    assert painted, "the input painted nothing"
    columns = {}
    for x, y in painted:
        low, high = columns.get(x, (y, y))
        columns[x] = (min(low, y), max(high, y))
    heights = {x: high - low + 1 for x, (low, high) in columns.items()}
    tall = max(heights.values())
    track = sorted(heights.values())[len(heights) // 2]
    assert tall > track, (f"the track is as tall as the thumb ({tall} device px), so its round ends "
                          "show around the thumb at either end")
    thumb = [x for x, height in heights.items() if height >= (tall + track) / 2]
    cx = (min(thumb) + max(thumb) + 1) / 2
    low, high = columns[max(heights, key=heights.get)]
    cy, radius = (low + high + 1) / 2, tall / 2
    beyond = [(x, y) for x, y in painted if (x + .5 > cx if at == "max" else x + .5 < cx)]
    return max((math.hypot(x + .5 - cx, y + .5 - cy) - radius for x, y in beyond), default=0.0)


def _shot(page, clip) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB")


def measure(scale: float) -> dict[str, float]:
    """Every kind at min and max: its overhang in CSS px."""
    seen = {}
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=scale)
            for host in sorted({kind[2] for kind in KINDS}):
                rows = []
                for name, _source, where, wrapper, attrs in KINDS:
                    if where != host:
                        continue
                    for at in ("min", "max"):
                        value = re.search(rf'{at}="([^"]+)"', attrs).group(1)
                        field = f'<input id="{name}-{at}" type="range" {attrs} value="{value}">'
                        rows.append(f'<div style="padding:16px">{wrapper.format(input=field)}</div>')
                # A loud page, so a thumb's dark rim reads as thumb rather than background.
                page.set_content(f"<!doctype html><html><head><meta charset=utf-8>{_style_block(host)}</head>"
                                 f"<body style='background:#f0f'>{''.join(rows)}</body></html>")
                for name, *_rest in (kind for kind in KINDS if kind[2] == host):
                    for at in ("min", "max"):
                        field = page.locator(f"#{name}-{at}")
                        field.scroll_into_view_if_needed()
                        box = field.bounding_box()
                        clip = {"x": box["x"] - PAD, "y": box["y"] - PAD,
                                "width": box["width"] + 2 * PAD, "height": box["height"] + 2 * PAD}
                        shown = _shot(page, clip)
                        field.evaluate("e => e.style.visibility = 'hidden'")
                        hidden = _shot(page, clip)
                        field.evaluate("e => e.style.visibility = ''")
                        seen[f"{name}-{at}"] = overhang(shown, hidden, at) / scale
        finally:
            browser.close()
    return seen


def test_every_slider_shows_nothing_past_its_thumb_at_min_and_max():
    for scale in SCALES:
        seen = measure(scale)
        assert len(seen) == 2 * len(KINDS)
        # One device pixel of antialiasing on the thumb's edge is all there may be.
        past = {name: round(px, 2) for name, px in seen.items() if px > 1 / scale}
        assert not past, f"at {scale}x, track or fill shows past the thumb (CSS px): {past}"


def test_every_slider_in_the_source_is_rendered_by_the_test_above():
    rendered = {kind[1] for kind in KINDS}
    found = ranges_in_source()
    assert found - rendered == set(), f"a slider nobody renders at min and max: {sorted(found - rendered)}"
    assert rendered - found == set(), f"the test renders a slider the source no longer has: {sorted(rendered - found)}"
    # The wrappers are the components' own classes, so a renamed one shows here.
    everything = "".join(path.read_text(encoding="utf-8") for path in UI.rglob("*")
                         if path.suffix in (".js", ".html", ".css") and "vendor" not in path.parts)
    for name, _source, _page, wrapper, attrs in KINDS:
        for cls in re.findall(r'class="([^"]+)"', wrapper + attrs):
            missing = [token for token in cls.split() if token not in everything]
            assert not missing, f"{name}: wrapper classes no longer in the UI: {missing}"


def test_every_page_with_a_slider_loads_the_rule_that_owns_its_geometry():
    files = {name for name, _nth in ranges_in_source()}
    for page in sorted(UI.glob("*.html")):
        if page.name == "index.html" or not _reaches(page, files):
            continue
        source = page.read_text(encoding="utf-8")
        assert 'fetch("/ui/index.html")' in source and 'indexOf("<style>")' in source, (
            f"{page.name} renders a slider without index.html's stylesheet")


def test_the_guards_can_still_fail():
    # The scan sees each spelling of a slider, and no selector or comment.
    assert len(RANGE.findall('<input type="range" /> <input type=range> h("input", { type: "range" })'
                             ' el.type = "range"; el.setAttribute("type", "range")')) == 5
    assert RANGE.findall(_markup('input[type=range] { } q("input[type=\\"range\\"]")\n  // type="range"\n'
                                 '<!-- <input type="range"> --><style>input[type="range"]{}</style>')) == []
    # The pixel reader: a thumb flush with the track's end reads zero, 8px of
    # track past it reads 8.
    def slider(track_end: int) -> tuple[Image.Image, Image.Image]:
        hidden = Image.new("RGB", (140, 40), (5, 12, 22))
        shown = hidden.copy()
        px = shown.load()
        for x in range(0, track_end):
            for y in range(18, 22):
                px[x, y] = (90, 90, 90)
        for x in range(100, 120):
            for y in range(10, 30):
                if (x + .5 - 110) ** 2 + (y + .5 - 20) ** 2 <= 100:
                    px[x, y] = (240, 189, 50)
        return shown, hidden
    assert overhang(*slider(120), "max") <= 1
    assert 7 <= overhang(*slider(128), "max") <= 9
