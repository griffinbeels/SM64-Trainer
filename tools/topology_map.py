"""Draw the world topology we believe in, so a human can check it.

The segment matcher now CANCELS a run on a move this table says is impossible
(spec 2026-08-01-topological-segment-validity), which makes a wrong or missing
edge expensive and invisible: an extra edge only makes the rules more
permissive, so `tools/measure_topology_cancels.py` -- which scores the rules by
how many real completions they destroy -- can never see one. The only way to
catch that class is for someone who knows the game to LOOK at the graph.

So this renders `memory/addresses.py`'s WORLD_EDGES_* exactly as the code reads
them, two ways at once:

  1. a map, laid out by castle region in gameflow order (grounds -> lobby ->
     basement -> courtyard -> upstairs), because a generic graph layout would
     scatter a world that has an obvious shape;
  2. a per-place list -- "the only valid path out of SSL is Basement" -- which
     is the form that is actually fast to check, sentence by sentence.

Positions are DERIVED, never authored: region membership comes from
`world_regions()` (the same BFS the segment-origin taxonomy uses) and the
within-region ordering from a BFS out of each region hub. Add an edge to the
registry and this redraws; there is no second copy of the world here to drift.

Usage:
    uv run python tools/topology_map.py [out.html]
"""
import html
import sys
from collections import deque
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.memory.addresses import (CASTLE_REGION_NODES,
                                          WORLD_EDGES_ONE_WAY,
                                          WORLD_EDGES_TWO_WAY, _world_node,
                                          node_key, node_label, world_regions)

HUB_X = 250          # the region-hub column
COL_STEP = 300       # horizontal gap per BFS depth out from the hub
ROW_STEP = 46        # vertical gap between siblings
BAND_PAD = 62        # blank space between bands -- also the channel a
                     # multi-column edge arcs through (see _over_path)
MARGIN = 40


def _edges():
    """(from_key, to_key, one_way) for every edge in the registry, with the
    two-way rows expanded into the single undirected pair they mean."""
    out = []
    for node_a, node_b in WORLD_EDGES_TWO_WAY:
        out.append((node_key(*_world_node(node_a)),
                    node_key(*_world_node(node_b)), False))
    for from_spec, to_spec in WORLD_EDGES_ONE_WAY:
        out.append((node_key(*_world_node(from_spec)),
                    node_key(*_world_node(to_spec)), True))
    return out


def _adjacency(edges):
    """Neighbours for LAYOUT depth, following one-way edges FORWARD ONLY.

    Undirected was the first version and it put every Bowser arena in the same
    column as the course it is entered from, because the arena's exit edge back
    to the castle made it a direct neighbour of the region hub. That collapsed
    all three one-way pairs into a stub too short to see, which is the opposite
    of what this page is for -- the one-way rows are exactly the ones a reader
    needs to check.
    """
    adjacency: dict[str, set] = {}
    for a, b, one_way in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set())
        if not one_way:
            adjacency[b].add(a)
    return adjacency


def _layout(edges):
    """node key -> (x, y), plus each node's band top and column index.

    Region bands top to bottom in gameflow order; within a band, BFS depth out
    from the hub becomes the column. A node PAST the first column is placed on
    its PARENT'S ROW rather than in an ordering of its own -- so Cavern of the
    Metal Cap sits level with Hazy Maze Cave and each Bowser arena level with
    its own course, turning what were long diagonals crossing three unrelated
    rows into short horizontal arrows.
    """
    adjacency = _adjacency(edges)
    regions = world_regions()
    hubs = [node_key(level, area) for level, area in CASTLE_REGION_NODES]

    positions: dict[str, tuple] = {}
    band_of: dict[str, float] = {}
    column_of: dict[str, int] = {}
    band_top = MARGIN
    bands = []
    for hub in hubs:
        members = {n for n, r in regions.items() if r == hub and n != hub}
        # BFS out from the hub, staying inside this region, so CotMC lands one
        # column past HMC and each Bowser arena one past its own course.
        depth, parent = {hub: 0}, {}
        queue = deque([hub])
        while queue:
            node = queue.popleft()
            for neighbour in sorted(adjacency.get(node, ())):
                if neighbour in depth or regions.get(neighbour) != hub:
                    continue
                depth[neighbour] = depth[node] + 1
                parent[neighbour] = node
                queue.append(neighbour)
        by_depth: dict[int, list] = {}
        for member in members:
            by_depth.setdefault(depth.get(member, 1), []).append(member)
        for nodes in by_depth.values():
            nodes.sort(key=lambda n: node_label(n).lower())

        first = by_depth.get(1, [])
        height = max(len(first), 1) * ROW_STEP
        positions[hub] = (HUB_X, band_top + height / 2)
        column_of[hub] = 0
        for index, node in enumerate(first):
            positions[node] = (HUB_X + COL_STEP,
                               band_top + index * ROW_STEP + ROW_STEP / 2)
            column_of[node] = 1
        taken: dict[int, set] = {}
        for level in sorted(d for d in by_depth if d >= 2):
            for node in by_depth[level]:
                row = positions.get(parent.get(node, hub), (0, band_top))[1]
                while row in taken.setdefault(level, set()):
                    row += ROW_STEP
                taken[level].add(row)
                positions[node] = (HUB_X + COL_STEP * level, row)
                column_of[node] = level
        bottom = max(positions[n][1] for n in members | {hub}) + ROW_STEP / 2
        height = bottom - band_top
        for node in members | {hub}:
            band_of[node] = band_top
        bands.append((hub, band_top, height))
        band_top += height + BAND_PAD
    return positions, bands, band_top + MARGIN, band_of, column_of


def _box_width(key) -> int:
    """ONE answer, read by both the box drawing and the edge trimming below --
    an arrowhead has to land just outside the rectangle, and a second guess at
    how wide that rectangle is would bury it inside on the long labels only."""
    return max(96, 9 * len(node_label(key)) + 26)


ARROW_GAP = 5        # clear air between a box edge and the arrowhead touching it


def _node_box(key, x, y, is_hub):
    label = html.escape(node_label(key))
    width = _box_width(key)
    cls = "hub" if is_hub else "place"
    return (f'<g class="node {cls}"><rect x="{x - width/2:.0f}" y="{y - 15:.0f}" '
            f'width="{width}" height="30" rx="8"/>'
            f'<text x="{x:.0f}" y="{y + 5:.0f}">{label}</text>'
            f'<title>{label}  ({html.escape(key)})</title></g>')


def _edge_path(a, b, positions, bow=None):
    """A gentle curve between two boxes, TRIMMED to their edges.

    Both ends leave horizontally (every control point sits at its own
    endpoint's y), so trimming is a horizontal inset of half the box plus a
    little air -- which is what lets an arrowhead sit OUTSIDE the rectangle
    instead of underneath it. Centre-to-centre was the first version and hid
    every arrowhead inside a node box, so the three one-way edges read as stubs
    and direction was unreadable, which is the whole point of the marks.
    """
    x1, y1 = positions[a]
    x2, y2 = positions[b]
    inset_a = _box_width(a) / 2 + ARROW_GAP
    inset_b = _box_width(b) / 2 + ARROW_GAP
    if abs(x2 - x1) < 1:                       # same column: both bow leftward
        if bow is None:
            bow = min(120, max(30, abs(y2 - y1) * 0.35))
        return (f"M {x1 - inset_a:.0f} {y1:.0f} C {x1 - bow:.0f} {y1:.0f}, "
                f"{x2 - bow:.0f} {y2:.0f}, {x2 - inset_b:.0f} {y2:.0f}")
    mid = (x1 + x2) / 2
    sx = x1 + inset_a if mid > x1 else x1 - inset_a
    ex = x2 - inset_b if mid < x2 else x2 + inset_b
    return (f"M {sx:.0f} {y1:.0f} C {mid:.0f} {y1:.0f}, "
            f"{mid:.0f} {y2:.0f}, {ex:.0f} {y2:.0f}")


def _over_path(a, b, positions, band_of):
    """An edge spanning two or more columns, routed OVER the band instead of
    straight through it.

    Every such edge today is a Bowser arena's winning key-cutscene back to its
    castle region, and drawn as a straight line it runs the full width of the
    band -- right across the middle stack of courses, which is what made the
    right-hand side unreadable (human report 2026-08-02: "I'd also visually
    appreciate if the arrows weren't overlapping... Maybe the arrows should
    loop OVER the middle chunk of stages?"). It leaves and lands VERTICALLY, so
    the arrowhead points down into its destination and never shares an
    attachment point with the horizontal fan.
    """
    x1, y1 = positions[a]
    x2, y2 = positions[b]
    # UP, ACROSS, DOWN with rounded corners -- not a cubic. A cubic only
    # APPROACHES its control points, so lifting them above the band still let
    # the curve sag back through the topmost box on its way across (reported
    # 2026-08-02: "there's still an overlapping line here (behind Bob-omb
    # Battlefield)"). An explicit horizontal run at `peak` cannot sag: it is at
    # that y for its whole length, by construction rather than by tuning.
    peak = band_of[a] - 24            # clear of the band rect, inside BAND_PAD
    radius = 12
    turn = -radius if x2 < x1 else radius
    return (f"M {x1:.0f} {y1 - 16:.0f} "
            f"L {x1:.0f} {peak + radius:.0f} "
            f"Q {x1:.0f} {peak:.0f} {x1 + turn:.0f} {peak:.0f} "
            f"L {x2 - turn:.0f} {peak:.0f} "
            f"Q {x2:.0f} {peak:.0f} {x2:.0f} {peak + radius:.0f} "
            f"L {x2:.0f} {y2 - 16:.0f}")


def _svg(edges, positions, bands, height, band_of, column_of):
    width = max(x for x, _ in positions.values()) + 220
    parts = [f'<svg viewBox="0 0 {width:.0f} {height:.0f}" '
             f'width="{width:.0f}" height="{height:.0f}">',
             "<defs>" + "".join(
                 f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="9" '
                 'refY="5" markerWidth="8" markerHeight="8" '
                 'orient="auto-start-reverse">'
                 f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{colour}"/></marker>'
                 for name, colour in (("two", "#6fa8ff"), ("one", "#ffd166"),
                                      ("spine", "#8ad6c0"))) + "</defs>"]
    for hub, top, band_height in bands:
        parts.append(f'<rect class="band" x="{MARGIN/2:.0f}" y="{top - 8:.0f}" '
                     f'width="{width - MARGIN:.0f}" height="{band_height + 16:.0f}" rx="14"/>')
        parts.append(f'<text class="bandlabel" x="{MARGIN:.0f}" '
                     f'y="{top + 6:.0f}">{html.escape(node_label(hub)).upper()} REGION</text>')
    hub_set = {node_key(level, area) for level, area in CASTLE_REGION_NODES}
    spine = 0
    for a, b, one_way in edges:
        if a not in positions or b not in positions:
            continue
        # A castle door between two REGIONS joins two boxes in the same column,
        # so each gets its own bow width -- otherwise the five of them overlap
        # into one unreadable bundle down the left edge.
        if a in hub_set and b in hub_set:
            spine += 1
            path = _edge_path(a, b, positions, bow=70 + spine * 34)
            cls, kind = "edge spine", "spine"
        elif abs(column_of.get(a, 0) - column_of.get(b, 0)) >= 2:
            path = _over_path(a, b, positions, band_of)
            cls = "edge oneway" if one_way else "edge twoway"
            kind = "one" if one_way else "two"
        elif one_way:
            path = _edge_path(a, b, positions)
            cls, kind = "edge oneway", "one"
        else:
            path = _edge_path(a, b, positions)
            cls, kind = "edge twoway", "two"
        # EVERY edge is marked, and the HEAD COUNT is the direction: one
        # arrowhead means the return trip is a different route, two means you
        # can walk back the way you came. Unmarked-means-two-way needs a legend
        # to decode; two heads say it on the line itself.
        marks = f' marker-end="url(#arrow-{kind})"'
        if not one_way:
            marks = f' marker-start="url(#arrow-{kind})"' + marks
        parts.append(f'<path class="{cls}" d="{path}"{marks}/>')
    hub_keys = {node_key(level, area) for level, area in CASTLE_REGION_NODES}
    for key, (x, y) in positions.items():
        parts.append(_node_box(key, x, y, key in hub_keys))
    parts.append("</svg>")
    return "\n".join(parts)


def _checklist(edges):
    """The form that is actually fast to verify: one sentence per place."""
    outgoing: dict[str, set] = {}
    for a, b, one_way in edges:
        outgoing.setdefault(a, set()).add(b)
        outgoing.setdefault(b, set())
        if not one_way:
            outgoing[b].add(a)
    rows = []
    for key in sorted(outgoing, key=lambda k: (node_label(k).lower(), k)):
        destinations = sorted(outgoing[key], key=lambda k: node_label(k).lower())
        names = ", ".join(html.escape(node_label(d)) for d in destinations)
        rows.append(f"<tr><th>{html.escape(node_label(key))}</th>"
                    f"<td>{names or '<em>nothing (dead end)</em>'}</td></tr>")
    return "\n".join(rows)


STYLE = """
:root { color-scheme: dark; }
body { margin:0; background:#0b1020; color:#e8ecff;
       font:15px/1.55 ui-sans-serif,system-ui,'Segoe UI',sans-serif; }
main { max-width:1400px; margin:0 auto; padding:32px 24px 80px; }
h1 { font-size:26px; margin:0 0 4px; color:#ffd166; letter-spacing:.2px; }
p.lede { margin:0 0 28px; color:#9fb0d9; max-width:70ch; }
p.lede strong { color:#e8ecff; }
.legend { display:flex; gap:22px; flex-wrap:wrap; margin:0 0 20px;
          color:#9fb0d9; font-size:13px; align-items:center; }
.swatch { display:inline-block; width:26px; height:0; border-top:2px solid #6fa8ff;
          vertical-align:middle; margin-right:7px; }
.swatch.one { border-top:2px solid #ffd166; }
.swatch.spineswatch { border-top:2px solid #8ad6c0; }
.scroll { overflow-x:auto; border:1px solid #26325c; border-radius:14px;
          background:#0e1530; padding:8px; }
svg { display:block; }
.band { fill:#141d3d; stroke:#26325c; }
.bandlabel { fill:#5f74ad; font-size:11px; font-weight:700; letter-spacing:1.4px; }
.edge { fill:none; stroke-width:1.6; }
.twoway { stroke:#6fa8ff; opacity:.5; }
.spine { stroke:#8ad6c0; opacity:.9; stroke-width:2.2; }
.oneway { stroke:#ffd166; opacity:.85; }

.node rect { fill:#1b2650; stroke:#3a4a86; }
.node.hub rect { fill:#2a2140; stroke:#ffd166; stroke-width:1.6; }
.node text { fill:#e8ecff; font-size:12.5px; text-anchor:middle;
             font-weight:500; pointer-events:none; }
.node.hub text { fill:#ffd166; font-weight:700; }
h2 { font-size:18px; margin:44px 0 10px; color:#ffd166; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { text-align:left; padding:7px 12px; border-bottom:1px solid #1d2748;
         vertical-align:top; }
th { color:#9fb0d9; font-weight:600; white-space:nowrap; width:1%; }
.caveat { margin-top:34px; padding:16px 20px; border-left:3px solid #ff8f6f;
          background:#181227; border-radius:0 10px 10px 0; color:#d7c6c0;
          font-size:14px; max-width:80ch; }
.caveat b { color:#ff8f6f; }
"""


def render() -> str:
    edges = _edges()
    positions, bands, height, band_of, column_of = _layout(edges)
    two_way = sum(1 for *_, one in edges if not one)
    one_way = len(edges) - two_way
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>SM64 world topology — what the tracker believes</title>
<style>{STYLE}</style></head><body><main>
<h1>What the tracker thinks connects to what</h1>
<p class="lede">Drawn straight from the world table the segment matcher now uses
to decide whether a run is still going. <strong>{len(positions)} places,
{two_way} two-way connections, {one_way} one-way.</strong> If a line here is
wrong or missing, the matcher is wrong in the same place — nothing else in the
project can catch that, so this page exists to be looked at.</p>
<div class="legend">
  <span><span class="swatch"></span>arrowheads at BOTH ends — you can walk back the way you came</span>
  <span><span class="swatch one"></span>ONE arrowhead — the return trip is a different route</span>
  <span><span class="swatch spineswatch"></span>castle doors between regions</span>
  <span>Gold boxes are the five castle regions, top to bottom in the order the castle opens up.</span>
</div>
<div class="scroll">{_svg(edges, positions, bands, height, band_of, column_of)}</div>

<h2>Where you can go from each place</h2>
<table><tbody>
{_checklist(edges)}
</tbody></table>

<div class="caveat">
<b>What this page cannot tell you.</b> These are the connections under normal
movement — doors, paintings, pipes, course exits and deaths. The Usamune warp
menu can fabricate any of them, and the matcher stays permissive about
<em>arriving</em> somewhere by warp; what it refuses is letting a movement
survive one. Two known blind spots in how this was checked: an edge that should
not be here is invisible to the journal scoring (an extra edge only makes the
rules more permissive, so nothing gets destroyed and the score stays clean), and
only the parts of the castle actually played are exercised at all. One row has
already been wrong once — a "DDD sub bay → BitFS" edge that does not exist,
removed 2026-07-27 after live capture.
</div>
</main></body></html>
"""


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "topology_map.html")
    out.write_text(render(), encoding="utf-8", newline="")
    print(f"wrote {out.resolve()}")


if __name__ == "__main__":
    main()
