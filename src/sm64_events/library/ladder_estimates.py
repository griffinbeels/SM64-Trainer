"""Provisional inputs for sheet rows without a submitted community time.

An own best/ideal anchors one cutoff; it does not invent a distribution. The
three unanchored rows in the 2026-08-10 snapshot use audited related rows. These
are estimates of a different variant, not claims that their timings coincide.
Keep that uncertainty and the source identity beside the resulting ladder.
"""
from sm64_events.library.audit import row_key
from sm64_events.library.sheet import version_of


# Each relationship states the timed stretch explicitly. In particular, never
# substitute a whole star's duration for an empty subsection. Names and lineage
# ids protect against the repeated subsection labels found throughout the sheet.
_RELATED_ROWS = {
    ("4. Cool, Cool Mountain", "Slide + 100c No teleporter route",
     "Inside the slide (75c)", ("1", "2")): (
        "Slip Slidin' Away + 100c", "Inside the slide (76/77c)", ("1", "2"),
        "Uses the related 76/77-coin slide subsection; the 75-coin route has "
        "no submitted time, so its timing difference is not yet measured."),
    ("10. Snowman's Land", "Reds + 100c Pond spindrift early route",
     "Post igloo (US, 67/68c -)", ("2",)): (
        "Reds + 100c Pond spindrift early route",
        "Post igloo (JP, 67/68c -)", ("1",),
        "Uses this route's JP post-igloo subsection as a provisional US "
        "estimate; its US timing difference is not yet measured."),
    ("Bowser Courses", "Bowser in the Fire Sea Course",
     "BLJ w/ dive rollout onto elevator + full dive -> LJ ending", ("8",)): (
        "Bowser in the Fire Sea Course",
        "BLJ w/ dive rollout onto elevator + low dive -> SJ ending", ("6",),
        "Uses the same BLJ and dive-rollout beginning with the low-dive/SJ "
        "ending; the full-dive/LJ ending's timing difference is not yet measured."),
}


def _key(target, item):
    return row_key(target, item["name"], item.get("ids", ()))


def _anchor(item):
    """Prefer a published best, respecting annotated regional bests, then ideal."""
    for version in ("us", "jp"):
        best = (item.get("times") or {}).get(version)
        if best is not None and best > 0:
            return best, version, "best"
    for field, method in (("best_cs", "best"), ("ideal_cs", "ideal")):
        anchor = item.get(field)
        if anchor is not None and anchor > 0:
            return anchor, version_of(item["name"]), method
    return None


def estimate_times(target, kind, item, populations):
    """Return (provisional times, intended ROM, provenance), with no data writes.

    `populations` contains only actual row observations selected by the fitter;
    a refresh therefore updates proxies regardless of source/recipient order.
    An unknown unanchored row stays explicitly without evidence rather than
    quietly inheriting an unrelated target's duration.
    """
    anchor = _anchor(item)
    if anchor:
        time_cs, version, method = anchor
        return [time_cs], version, {
            "method": method, "source_rows": [_key(target, item)],
            "source_samples": 0, "source_version": version,
            "note": f"No submitted times; uses this row's published {method} time.",
        }
    identity = (target.get("section"), target.get("label"), item["name"],
                tuple(item.get("ids", ())))
    related = _RELATED_ROWS.get(identity)
    if related:
        label, name, ids, note = related
        for peer_target, peer_kind, peer, times, version in populations:
            if (times and peer_kind == kind
                    and peer_target.get("section") == target.get("section")
                    and peer_target.get("label") == label
                    and peer["name"] == name and tuple(peer.get("ids", ())) == ids):
                return times, version_of(item["name"]) or version, {
                    "method": "related_row", "source_rows": [_key(peer_target, peer)],
                    "source_samples": len(times), "source_version": version,
                    "note": note,
                }
    return [], None, None
