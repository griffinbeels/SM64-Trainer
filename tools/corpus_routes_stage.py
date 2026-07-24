"""The 37 Stage RTA routes (spec 2026-07-24 §7.2) — per-course ordered star
lists, no movement steps. start_condition is entering the course, so the run
clock starts on the painting rather than on F1, which is what a stage RTA
actually times.

Content is transcribed from
docs/superpowers/specs/2026-07-24-default-routes-corpus-sources.md
§"Per-course ordered star lists".
"""
from sm64_events.memory.addresses import star_name

from corpus_vocab import STAGE_RTA, route, stars

# (slug, display name, course_id, level_id, [star ids])
# An int is one star; a TUPLE is one step collecting BOTH (the "+ 100 Coins"
# pair, whose order depends on when the coin count crosses); a SET is a
# documented either/or (need=1).
STAGES = [
    ("bob-120", "BoB — 120", 1, 9, [5, 0, 1, (3, 6), 4, 2]),
    ("bob-70", "BoB — 70", 1, 9, [5, 2]),
    ("wf-120-70", "WF — 120 / 70", 2, 24, [5, 4, (3, 6), 2, 0, 1]),
    ("jrb-120", "JRB — 120", 3, 12, [0, 5, (3, 6), 4, 2, 1]),
    ("ccm-120", "CCM — 120", 4, 5, [5, 3, 1, 0, (2, 6), 4]),
    ("ccm-70-ccm17", "CCM — 70 (CCM17)", 4, 5, [5, 1, (0, 6)]),
    ("ccm-70-ccm18", "CCM — 70 (CCM18)", 4, 5, [5, 1, 0, (2, 6)]),
    ("ccm-16", "CCM — 16", 4, 5, [5, 1]),
    ("bbh-120", "BBH — 120", 5, 4, [0, (3, 6), 5, 1, 4, 2]),
    ("bbh-70", "BBH — 70", 5, 4, [4, 2]),
    ("hmc-120", "HMC — 120", 6, 7, [(1, 6), 0, 3, 2, 4, 5]),
    ("hmc-70", "HMC — 70", 6, 7, [0, 2, 4, 5]),
    ("hmc-16", "HMC — 16", 6, 7, [0, 4, 5]),
    ("lll-120", "LLL — 120", 7, 22, [3, 2, 0, 1, (4, 6), 5]),
    ("lll-70", "LLL — 70", 7, 22, [3, 2, 0, 1, 4, 5]),
    ("lll-16", "LLL — 16", 7, 22, [3, 2, 0, {4, 5}]),
    ("ssl-120", "SSL — 120", 8, 8, [2, 3, 0, 4, (5, 6), 1]),
    ("ssl-70-16", "SSL — 70 / 16", 8, 8, [2, 0, 1]),
    ("ddd-120", "DDD — 120", 9, 23, [(2, 6), 1, 0, 4, 3, 5]),
    ("ddd-70", "DDD — 70", 9, 23, [1, 0, 4]),
    ("ddd-16", "DDD — 16", 9, 23, [0]),
    ("sl-120", "SL — 120", 10, 10, [0, 5, (4, 6), 2, 3, 1]),
    ("sl-70-ttc100", "SL — 70 (HMC Late + TTC100)", 10, 10, [0, 2, 3, 1]),
    ("sl-70", "SL — 70 (HMC Early or no TTC100)", 10, 10, [0, 2, 4, 3, 1]),
    ("wdw-120", "WDW — 120", 11, 11, [5, (4, 6), 3, 1, 2, 0]),
    ("wdw-120-beginner", "WDW — 120 (Beginner)", 11, 11,
     [5, (2, 6), 3, 1, 4, 0]),
    ("wdw-70", "WDW — 70", 11, 11, [(2, 6), 3, 1, 0]),
    ("ttm-120", "TTM — 120", 12, 36, [0, 1, 4, 5, (2, 6), 3]),
    ("ttm-70", "TTM — 70", 12, 36, [0, 4, 5, 2, 3]),
    ("thi-120", "THI — 120", 13, 13, [5, 3, (4, 6), 0, 1, 2]),
    ("thi-70", "THI — 70", 13, 13, [3, 1, 0]),
    ("thi-70-reds", "THI — 70 (with THI Reds)", 13, 13, [3, 1, 4, 0]),
    ("ttc-120-70", "TTC — 120 / 70", 14, 14, [(3, 6), 0, 1, 2, 4, 5]),
    ("ttc-70-no-100", "TTC — 70 (no TTC100)", 14, 14, [0, 1, 2, 3, 4, 5]),
    ("rr-120-beginner", "RR — 120 (Beginner)", 15, 15, [0, (1, 6), 5, 4, 2, 3]),
    ("rr-120-expert", "RR — 120 (Expert)", 15, 15, [0, (5, 6), 1, 4, 2, 3]),
    ("rr-70", "RR — 70", 15, 15, [0, 4, 2, 3]),
]


def _step(course, entry):
    """int -> one star; tuple -> collect BOTH; set -> pick EITHER.

    Labels come from star_name, so a star rename can never leave a stale
    label behind in the seed."""
    if isinstance(entry, tuple):
        label = " + ".join(star_name(course, s) for s in entry)
        return stars(course, list(entry), label)
    if isinstance(entry, set):
        ids = sorted(entry)
        label = " or ".join(star_name(course, s) for s in ids)
        return stars(course, ids, label, need=1)
    return stars(course, [entry], star_name(course, entry))


ROUTES = [route(f"route:stage-{slug}", name, STAGE_RTA,
                [_step(course, entry) for entry in order],
                start_condition={"type": "level_enter", "to": level})
          for slug, name, course, level, order in STAGES]
