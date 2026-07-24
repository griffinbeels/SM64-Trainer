"""The 13 main-category routes (spec 2026-07-24 §7.1).

Steps MUST be in completion-event order — RunTracker only ever considers
steps[current], so a misordered step stalls a run permanently (spec §5.1).
Two consequences show up all over this file:

  * a movement step sits immediately BEFORE its destination's star block (it
    completes on entering that level, which precedes every star there);
  * a castle-secret star grabbed DURING a movement sits immediately before
    that movement's step — which is also exactly how the wiki reads
    ("Upstairs Toad (near TTM) | Tall, Tall Mountain").

Content is transcribed from
docs/superpowers/specs/2026-07-24-default-routes-corpus-sources.md; the
community nicknames (Cannonless, Pless, Owlless, ...) are resolved by that
file's alias glossary.
"""
from corpus_vocab import MAIN, route, segment, star, stars

# Star blocks reused across variants. Course-page order (see sources §"Per-
# course ordered star lists"); each helper returns a list of steps.

WF_ALL = [
    star(2, 5, "WF — Blast Away the Wall (Cannonless)"),
    star(2, 4, "WF — Fall onto the Caged Island (Owl Star)"),
    stars(2, [3, 6], "WF — Red Coins on the Floating Isle + 100 Coins"),
    star(2, 2, "WF — Shoot into the Wild Blue"),
    star(2, 0, "WF — Chip off Whomp's Block"),
    star(2, 1, "WF — To the Top of the Fortress"),
]
JRB_ALL = [
    star(3, 0, "JRB — Plunder in the Sunken Ship"),
    star(3, 5, "JRB — Through the Jet Stream"),
    stars(3, [3, 6], "JRB — Red Coins on the Ship Afloat + 100 Coins"),
    star(3, 4, "JRB — Blast to the Stone Pillar"),
    star(3, 2, "JRB — Treasure of the Ocean Cave"),
    star(3, 1, "JRB — Can the Eel Come Out to Play?"),
]
CCM_ALL = [
    star(4, 5, "CCM — Wall Kicks Will Work"),
    star(4, 3, "CCM — Frosty Slide for 8 Red Coins"),
    star(4, 1, "CCM — Li'l Penguin Lost"),
    star(4, 0, "CCM — Slip Slidin' Away"),
    stars(4, [2, 6], "CCM — Big Penguin Race + 100 Coins"),
    star(4, 4, "CCM — Snowman's Lost His Head"),
]
BBH_ALL = [
    star(5, 0, "BBH — Go on a Ghost Hunt"),
    stars(5, [3, 6], "BBH — Seek the 8 Red Coins + 100 Coins"),
    star(5, 5, "BBH — Eye to Eye in the Secret Room"),
    star(5, 1, "BBH — Ride Big Boo's Merry-Go-Round"),
    star(5, 4, "BBH — Big Boo's Balcony"),
    star(5, 2, "BBH — Secret of the Haunted Books"),
]
LLL_ALL = [
    star(7, 3, "LLL — Red-Hot Log Rolling"),
    star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
    star(7, 0, "LLL — Boil the Big Bully"),
    star(7, 1, "LLL — Bully the Bullies"),
    stars(7, [4, 6], "LLL — Hot-Foot-It into the Volcano + 100 Coins"),
    star(7, 5, "LLL — Elevator Tour in the Volcano"),
]
LLL_70 = [
    star(7, 3, "LLL — Red-Hot Log Rolling"),
    star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
    star(7, 0, "LLL — Boil the Big Bully"),
    star(7, 1, "LLL — Bully the Bullies"),
    star(7, 4, "LLL — Hot-Foot-It into the Volcano"),
    star(7, 5, "LLL — Elevator Tour in the Volcano"),
]
SSL_ALL = [
    star(8, 2, "SSL — Inside the Ancient Pyramid (Pillarless)"),
    star(8, 3, "SSL — Stand Tall on the Four Pillars"),
    star(8, 0, "SSL — In the Talons of the Big Bird"),
    star(8, 4, "SSL — Free Flying for 8 Red Coins"),
    stars(8, [5, 6], "SSL — Pyramid Puzzle + 100 Coins"),
    star(8, 1, "SSL — Shining Atop the Pyramid"),
]
SSL_70 = [
    star(8, 2, "SSL — Inside the Ancient Pyramid (Pillarless)"),
    star(8, 0, "SSL — In the Talons of the Big Bird"),
    star(8, 1, "SSL — Shining Atop the Pyramid"),
]
WDW_ALL = [
    star(11, 5, "WDW — Quick Race Through Downtown!"),
    stars(11, [4, 6], "WDW — Go to Town for Red Coins + 100 Coins"),
    star(11, 3, "WDW — Express Elevator--Hurry Up!"),
    star(11, 1, "WDW — Top o' the Town"),
    star(11, 2, "WDW — Secrets in the Shallows & Sky"),
    star(11, 0, "WDW — Shocking Arrow Lifts!"),
]
WDW_70 = [
    stars(11, [2, 6], "WDW — Secrets in the Shallows & Sky + 100 Coins"),
    star(11, 3, "WDW — Express Elevator--Hurry Up!"),
    star(11, 1, "WDW — Top o' the Town"),
    star(11, 0, "WDW — Shocking Arrow Lifts!"),
]
THI_ALL = [
    star(13, 5, "THI — Make Wiggler Squirm"),
    star(13, 3, "THI — Five Itty Bitty Secrets"),
    stars(13, [4, 6], "THI — Wiggler's Red Coins + 100 Coins"),
    star(13, 0, "THI — Pluck the Piranha Flower"),
    star(13, 1, "THI — The Tip Top of the Huge Island"),
    star(13, 2, "THI — Rematch with Koopa the Quick"),
]
THI_70 = [
    star(13, 3, "THI — Five Itty Bitty Secrets"),
    star(13, 1, "THI — The Tip Top of the Huge Island"),
    star(13, 0, "THI — Pluck the Piranha Flower"),
]
TTM_ALL = [
    star(12, 0, "TTM — Scale the Mountain"),
    star(12, 1, "TTM — Mystery of the Monkey Cage"),
    star(12, 4, "TTM — Breathtaking View from Bridge"),
    star(12, 5, "TTM — Blast to the Lonely Mushroom"),
    stars(12, [2, 6], "TTM — Scary 'Shrooms, Red Coins + 100 Coins"),
    star(12, 3, "TTM — Mysterious Mountainside"),
]
TTM_70 = [
    star(12, 0, "TTM — Scale the Mountain"),
    star(12, 4, "TTM — Breathtaking View from Bridge"),
    star(12, 5, "TTM — Blast to the Lonely Mushroom"),
    star(12, 2, "TTM — Scary 'Shrooms, Red Coins"),
    star(12, 3, "TTM — Mysterious Mountainside"),
]
SL_ALL = [
    star(10, 0, "SL — Snowman's Big Head"),
    star(10, 5, "SL — Into the Igloo"),
    stars(10, [4, 6], "SL — Shell Shreddin' for Red Coins + 100 Coins"),
    star(10, 2, "SL — In the Deep Freeze"),
    star(10, 3, "SL — Whirl from the Freezing Pond"),
    star(10, 1, "SL — Chill with the Bully"),
]
SL_70_TTC100 = [
    star(10, 0, "SL — Snowman's Big Head"),
    star(10, 2, "SL — In the Deep Freeze"),
    star(10, 3, "SL — Whirl from the Freezing Pond"),
    star(10, 1, "SL — Chill with the Bully"),
]
SL_70_REDS = [
    star(10, 0, "SL — Snowman's Big Head"),
    star(10, 2, "SL — In the Deep Freeze"),
    star(10, 4, "SL — Shell Shreddin' for Red Coins"),
    star(10, 3, "SL — Whirl from the Freezing Pond"),
    star(10, 1, "SL — Chill with the Bully"),
]
TTC_ALL = [
    stars(14, [3, 6], "TTC — Stomp on the Thwomp + 100 Coins"),
    star(14, 0, "TTC — Roll into the Cage"),
    star(14, 1, "TTC — The Pit and the Pendulums"),
    star(14, 2, "TTC — Get a Hand"),
    star(14, 4, "TTC — Timed Jumps on Moving Bars"),
    star(14, 5, "TTC — Stop Time for Red Coins"),
]
TTC_NO_100 = [
    star(14, 0, "TTC — Roll into the Cage"),
    star(14, 1, "TTC — The Pit and the Pendulums"),
    star(14, 2, "TTC — Get a Hand"),
    star(14, 3, "TTC — Stomp on the Thwomp"),
    star(14, 4, "TTC — Timed Jumps on Moving Bars"),
    star(14, 5, "TTC — Stop Time for Red Coins"),
]
RR_ALL = [
    star(15, 0, "RR — Cruiser Crossing the Rainbow"),
    stars(15, [1, 6], "RR — The Big House in the Sky + 100 Coins"),
    star(15, 5, "RR — Somewhere Over the Rainbow"),
    star(15, 4, "RR — Tricky Triangles!"),
    star(15, 2, "RR — Coins Amassed in a Maze"),
    star(15, 3, "RR — Swingin' in the Breeze"),
]
RR_70 = [
    star(15, 0, "RR — Cruiser Crossing the Rainbow"),
    star(15, 4, "RR — Tricky Triangles!"),
    star(15, 2, "RR — Coins Amassed in a Maze"),
    star(15, 3, "RR — Swingin' in the Breeze"),
]
HMC_ALL = [
    stars(6, [1, 6], "HMC — Elevate for 8 Red Coins + 100 Coins"),
    star(20, 0, "Cavern of the Metal Cap"),
    star(6, 0, "HMC — Swimming Beast in the Cavern"),
    star(6, 3, "HMC — Navigating the Toxic Maze"),
    star(6, 2, "HMC — Metal-Head Mario Can Move!"),
    star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
    star(6, 5, "HMC — Watch for Rolling Rocks"),
]
HMC_70 = [
    star(6, 0, "HMC — Swimming Beast in the Cavern"),
    star(6, 2, "HMC — Metal-Head Mario Can Move!"),
    star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
    star(6, 5, "HMC — Watch for Rolling Rocks"),
]
DDD_70 = [
    star(9, 1, "DDD — Chests in the Current"),
    star(9, 0, "DDD — Board Bowser's Sub"),
    star(9, 4, "DDD — The Manta Ray's Reward"),
]

BOWSER_1 = [segment("seg:bitdw-pipe", "BitDW Pipe Entry"),
            segment("seg:bowser-1", "Bowser Battle 1")]
BOWSER_2 = [segment("seg:bitfs-pipe", "BitFS Pipe Entry"),
            segment("seg:bowser-2", "Bowser Battle 2")]
BOWSER_3 = [segment("seg:bits-pipe", "BitS Pipe Entry"),
            segment("seg:bowser-3", "Bowser Battle 3")]

ROUTES = []

# --- 16 Star ---------------------------------------------------------------

_16_TAIL = [
    segment("seg:mips-clip", "MIPS Clip"),
    star(9, 0, "DDD — Board Bowser's Sub"),
    segment("seg:ddd->bitfs", "→ BitFS"),
    *BOWSER_2,
    segment("seg:bowser2->upstairs", "→ Upstairs"),
    segment("seg:bits-entry", "Endless Staircase BLJ"),
    *BOWSER_3,
]

ROUTES.append(route(
    "route:16-no-lblj-standard", "16 Star — No LBLJ (Standard)", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:castle-entry->bob", "→ BoB"),
        star(1, 5, "BoB — Behind Chain Chomp's Gate"),
        segment("seg:bob->wf", "→ WF"),
        star(2, 5, "WF — Blast Away the Wall (Cannonless)"),
        star(2, 2, "WF — Shoot into the Wild Blue"),
        star(2, 0, "WF — Chip off Whomp's Block"),
        star(2, 1, "WF — To the Top of the Fortress"),
        star(2, 4, "WF — Fall onto the Caged Island (Owl Star)"),
        segment("seg:wf->ccm", "→ CCM"),
        star(4, 5, "CCM — Wall Kicks Will Work"),
        star(4, 1, "CCM — Li'l Penguin Lost"),
        segment("seg:ccm->bitdw", "→ BitDW"),
        star(16, 0, "BitDW — 8 Red Coins"),
        *BOWSER_1,
        segment("seg:bowser1->ssl", "→ SSL"),
        star(8, 0, "SSL — In the Talons of the Big Bird"),
        star(8, 1, "SSL — Shining Atop the Pyramid"),
        segment("seg:ssl->lll", "→ LLL"),
        star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
        # The Toad star is grabbed DURING the walk to HMC ("get this before
        # entering HMC"), so its step precedes the movement's — the movement
        # completes on entering HMC (spec §5.1).
        star(0, 0, "HMC Toad"),
        segment("seg:lll->hmc", "→ HMC"),
        star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
        star(6, 5, "HMC — Watch for Rolling Rocks"),
        *_16_TAIL,
    ]))

ROUTES.append(route(
    "route:16-no-lblj-beginner", "16 Star — No LBLJ (Beginner, no DW Reds)",
    MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:castle-entry->bob", "→ BoB"),
        star(1, 5, "BoB — Behind Chain Chomp's Gate"),
        segment("seg:bob->wf", "→ WF"),
        star(2, 5, "WF — Blast Away the Wall (Cannonless)"),
        star(2, 2, "WF — Shoot into the Wild Blue"),
        star(2, 0, "WF — Chip off Whomp's Block"),
        star(2, 1, "WF — To the Top of the Fortress"),
        star(2, 4, "WF — Fall onto the Caged Island (Owl Star)"),
        segment("seg:wf->ccm", "→ CCM"),
        star(4, 5, "CCM — Wall Kicks Will Work"),
        star(4, 1, "CCM — Li'l Penguin Lost"),
        segment("seg:ccm->bitdw", "→ BitDW"),
        # no BitDW reds — that is what makes this the beginner route
        *BOWSER_1,
        segment("seg:bowser1->ssl", "→ SSL"),
        star(8, 0, "SSL — In the Talons of the Big Bird"),
        star(8, 1, "SSL — Shining Atop the Pyramid"),
        segment("seg:ssl->lll", "→ LLL"),
        star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
        segment("seg:lll->hmc", "→ HMC"),
        # Emergency Exit EXITS the course, so the Toad star is grabbed between
        # HMC stars and needs no movement step of its own.
        star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
        star(0, 0, "HMC Toad"),
        star(6, 0, "HMC — Swimming Beast in the Cavern"),
        star(6, 5, "HMC — Watch for Rolling Rocks"),
        *_16_TAIL,
    ]))

ROUTES.append(route(
    "route:16-no-lblj-wf100c", "16 Star — No LBLJ + WF 100c (CCM Skip)",
    MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:castle-entry->bob", "→ BoB"),
        star(1, 5, "BoB — Behind Chain Chomp's Gate"),
        segment("seg:bob->wf", "→ WF"),
        # The guide describes this variant in prose only: the WF 100c cycle
        # replaces the CCM visit. Two CCM stars out, two WF stars in — the
        # course-page order with Red Coins + 100 Coins added.
        *WF_ALL,
        segment("seg:wf->bitdw", "→ BitDW"),
        star(16, 0, "BitDW — 8 Red Coins"),
        *BOWSER_1,
        segment("seg:bowser1->ssl", "→ SSL"),
        star(8, 0, "SSL — In the Talons of the Big Bird"),
        star(8, 1, "SSL — Shining Atop the Pyramid"),
        segment("seg:ssl->lll", "→ LLL"),
        star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
        star(0, 0, "HMC Toad"),
        segment("seg:lll->hmc", "→ HMC"),
        star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
        star(6, 5, "HMC — Watch for Rolling Rocks"),
        *_16_TAIL,
    ]))

ROUTES.append(route(
    "route:16-lblj", "16 Star — LBLJ (Standard)", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:lblj", "LBLJ"),
        star(16, 0, "BitDW — 8 Red Coins"),
        *BOWSER_1,
        segment("seg:bowser1->wf", "→ WF"),
        star(2, 5, "WF — Blast Away the Wall (Cannonless)"),
        star(2, 4, "WF — Fall onto the Caged Island (Owlless)"),
        star(2, 2, "WF — Shoot into the Wild Blue"),
        segment("seg:wf->ssl", "→ SSL"),
        star(8, 2, "SSL — Inside the Ancient Pyramid (Pillarless)"),
        star(8, 0, "SSL — In the Talons of the Big Bird"),
        star(8, 1, "SSL — Shining Atop the Pyramid"),
        segment("seg:ssl->lll", "→ LLL"),
        star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
        star(7, 3, "LLL — Red-Hot Log Rolling (Sidehop)"),
        star(7, 0, "LLL — Boil the Big Bully"),
        stars(7, [4, 5],
              "LLL — Hot-Foot-It into the Volcano or Elevator Tour", need=1),
        segment("seg:lll->hmc", "→ HMC"),
        star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
        star(0, 0, "HMC Toad"),
        star(6, 5, "HMC — Watch for Rolling Rocks"),
        star(6, 0, "HMC — Swimming Beast in the Cavern"),
        *_16_TAIL,
    ]))

# --- 70 Star ---------------------------------------------------------------


def _seventy(seed_key, name, *, ccm, ssl, sl, ttc, island_hop=False,
             hmc_early=False):
    """The HMC-Late 70 Star route, parameterised by its four documented
    options. HMC Early re-routes the basement and drops the second MIPS trip.

    Star totals are pinned by tests/test_corpus_routes_main.py — the wiki's
    own CCM17/CCM18 names are simply the running total on leaving CCM."""
    opening = [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:castle-entry->bob", "→ BoB"),
    ]
    if island_hop:
        opening.append(star(1, 2, "BoB — Shoot to the Island in the Sky "
                                  "(Island Hop)"))
    opening.append(star(1, 5, "BoB — Behind Chain Chomp's Gate"))
    lobby = [
        *opening,
        segment("seg:bob->pss", "→ PSS"),
        star(19, 0, "PSS — Slide Star"),
        segment("seg:pss->wf", "→ WF"),
        *WF_ALL,
        segment("seg:wf->pss", "→ PSS"),
        star(19, 1, "PSS — Slide Star (Under 21 Seconds)"),
        segment("seg:pss->totwc", "→ TotWC"),
        star(21, 0, "Tower of the Wing Cap"),
        segment("seg:totwc->bitdw", "→ BitDW"),
        star(16, 0, "BitDW — 8 Red Coins"),
        *BOWSER_1,
    ]
    if island_hop:
        lobby.append(segment("seg:bowser1->ccm", "→ CCM"))
    else:
        lobby += [segment("seg:bowser1->bob", "→ BoB"),
                  star(1, 2, "BoB — Shoot to the Island in the Sky"),
                  segment("seg:bob->ccm", "→ CCM")]
    lobby += [
        *ccm,
        segment("seg:ccm->bbh", "→ BBH"),
        star(5, 4, "BBH — Big Boo's Balcony"),
        star(5, 2, "BBH — Secret of the Haunted Books"),
    ]
    basement = [
        segment("seg:bbh->basement", "→ Basement"),
        star(0, 3, "MIPS (1st star)"),
        segment("seg:mips1->ssl", "→ SSL"),
        *ssl,
        segment("seg:ssl->lll", "→ LLL"),
        *LLL_70,
    ]
    if hmc_early:
        basement += [
            segment("seg:lll->hmc", "→ HMC"),
            *HMC_70,
            star(0, 0, "HMC Toad"),
            segment("seg:hmc->ddd", "→ DDD"),
        ]
    else:
        basement.append(segment("seg:lll->ddd", "→ DDD"))
    basement += [
        *DDD_70,
        segment("seg:ddd->bitfs", "→ BitFS"),
        star(17, 0, "BitFS — 8 Red Coins"),
        *BOWSER_2,
    ]
    upstairs = [
        segment("seg:bowser2->wdw", "→ WDW"),
        *WDW_70,
        segment("seg:wdw->thi", "→ THI"),
        *THI_70,
        star(0, 1, "Upstairs Toad"),
        segment("seg:thi->ttm", "→ TTM"),
        *TTM_70,
        segment("seg:ttm->sl", "→ SL"),
        *sl,
    ]
    if hmc_early:
        tippy_entry = [segment("seg:sl->rr", "→ RR")]
    else:
        tippy_entry = [
            segment("seg:sl->basement", "→ Basement (SL re-entry)"),
            star(0, 4, "MIPS (2nd star)"),
            star(0, 0, "HMC Toad"),
            segment("seg:mips2->hmc", "→ HMC"),
            *HMC_70,
            segment("seg:hmc->rr", "→ RR (HMC re-entry)"),
        ]
    tippy = [
        *tippy_entry,
        *RR_70,
        star(0, 2, "Tippy Toad"),
        segment("seg:rr->ttc", "→ TTC"),
        *ttc,
        segment("seg:ttc->bits", "→ BitS"),
        *BOWSER_3,
    ]
    return route(seed_key, name, MAIN, lobby + basement + upstairs + tippy)


CCM_18 = [
    star(4, 5, "CCM — Wall Kicks Will Work"),
    star(4, 1, "CCM — Li'l Penguin Lost"),
    star(4, 0, "CCM — Slip Slidin' Away"),
    stars(4, [2, 6], "CCM — Big Penguin Race + 100 Coins"),
]
CCM_17 = [
    star(4, 5, "CCM — Wall Kicks Will Work"),
    star(4, 1, "CCM — Li'l Penguin Lost"),
    stars(4, [0, 6], "CCM — Slip Slidin' Away + 100 Coins"),
]
SSL_70_CCM17 = [star(8, 5, "SSL — Pyramid Puzzle"), *SSL_70]

ROUTES.append(_seventy("route:70-hmc-late-beginner",
                       "70 Star — HMC Late (Beginner)",
                       ccm=CCM_18, ssl=SSL_70, sl=SL_70_REDS, ttc=TTC_NO_100))
ROUTES.append(_seventy("route:70-hmc-late-intermediate",
                       "70 Star — HMC Late (Intermediate, TTC100)",
                       ccm=CCM_18, ssl=SSL_70, sl=SL_70_TTC100, ttc=TTC_ALL))
ROUTES.append(_seventy("route:70-hmc-late-advanced",
                       "70 Star — HMC Late (Advanced, CCM17)",
                       ccm=CCM_17, ssl=SSL_70_CCM17, sl=SL_70_TTC100,
                       ttc=TTC_ALL))
ROUTES.append(_seventy("route:70-hmc-late-expert",
                       "70 Star — HMC Late (Expert, Island Hop)",
                       ccm=CCM_17, ssl=SSL_70_CCM17, sl=SL_70_TTC100,
                       ttc=TTC_ALL, island_hop=True))
# HMC Early collects SL Reds INSTEAD of the second MIPS star, so it keeps the
# no-TTC100 tail to stay at 70.
ROUTES.append(_seventy("route:70-hmc-early", "70 Star — HMC Early",
                       ccm=CCM_18, ssl=SSL_70, sl=SL_70_REDS, ttc=TTC_ALL,
                       hmc_early=True))

# --- 120 Star --------------------------------------------------------------

BOB_ALL_TAIL = [
    star(1, 0, "BoB — Big Bob-omb on the Summit"),
    star(1, 1, "BoB — Footrace with Koopa the Quick"),
    stars(1, [3, 6], "BoB — Find the 8 Red Coins + 100 Coins"),
    star(1, 2, "BoB — Shoot to the Island in the Sky"),
    star(1, 4, "BoB — Mario Wings to the Sky"),
]


def _one_twenty_body():
    """Everything after the lobby, identical in both 120 variants."""
    return [
        segment("seg:bob->basement", "→ Basement"),
        star(0, 3, "MIPS (1st star)"),
        segment("seg:mips1->ssl", "→ SSL"),
        *SSL_ALL,
        segment("seg:ssl->hmc", "→ HMC"),
        *HMC_ALL,
        star(0, 0, "HMC Toad"),
        segment("seg:hmc->lll", "→ LLL"),
        *LLL_ALL,
        star(0, 4, "MIPS (2nd star)"),
        segment("seg:mips2->vcutm", "→ VCUtM"),
        star(22, 0, "Vanish Cap Under the Moat"),
        segment("seg:vcutm->ccm", "→ CCM"),
        *CCM_ALL,
        segment("seg:ccm->bbh", "→ BBH"),
        *BBH_ALL,
        segment("seg:bbh->ddd", "→ DDD"),
        stars(9, [2, 6], "DDD — Pole-Jumping for Red Coins + 100 Coins"),
        star(9, 1, "DDD — Chests in the Current"),
        star(9, 0, "DDD — Board Bowser's Sub"),
        segment("seg:ddd->bitfs", "→ BitFS"),
        star(17, 0, "BitFS — 8 Red Coins"),
        *BOWSER_2,
        segment("seg:bowser2->ddd", "→ DDD"),
        star(9, 4, "DDD — The Manta Ray's Reward"),
        star(9, 3, "DDD — Through the Jet Stream"),
        star(9, 5, "DDD — Collect the Caps..."),
        segment("seg:ddd->wdw", "→ WDW (BitFS re-entry)"),
        *WDW_ALL,
        segment("seg:wdw->thi", "→ THI"),
        *THI_ALL,
        star(0, 1, "Upstairs Toad"),
        segment("seg:thi->ttm", "→ TTM"),
        *TTM_ALL,
        segment("seg:ttm->sl", "→ SL"),
        *SL_ALL,
        segment("seg:sl->wmotr", "→ WMotR"),
        star(23, 0, "Wing Mario Over the Rainbow"),
        segment("seg:wmotr->ttc", "→ TTC"),
        *TTC_ALL,
        star(0, 2, "Tippy Toad"),
        segment("seg:ttc->rr", "→ RR"),
        *RR_ALL,
        segment("seg:rr->bits", "→ BitS"),
        star(18, 0, "BitS — 8 Red Coins"),
        *BOWSER_3,
    ]


_120_CASTLE_STARS = [
    segment("seg:wf->sa", "→ Secret Aquarium"),
    star(24, 0, "The Secret Aquarium"),
    segment("seg:sa->jrb", "→ JRB"),
    *JRB_ALL,
    segment("seg:jrb->pss", "→ PSS"),
    star(19, 0, "PSS — Slide Star"),
    segment("seg:pss->totwc", "→ TotWC"),
    star(21, 0, "Tower of the Wing Cap"),
    segment("seg:totwc->pss", "→ PSS"),
    star(19, 1, "PSS — Slide Star (Under 21 Seconds)"),
]

ROUTES.append(route(
    "route:120-non-lblj", "120 Star — Non-LBLJ", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:castle-entry->bob", "→ BoB"),
        star(1, 5, "BoB — Behind Chain Chomp's Gate"),
        segment("seg:bob->wf", "→ WF"),
        *WF_ALL,
        *_120_CASTLE_STARS,
        segment("seg:pss->bitdw", "→ BitDW"),
        star(16, 0, "BitDW — 8 Red Coins"),
        *BOWSER_1,
        segment("seg:bowser1->bob", "→ BoB"),
        *BOB_ALL_TAIL,
        *_one_twenty_body(),
    ]))

ROUTES.append(route(
    "route:120-lblj", "120 Star — LBLJ", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:lblj", "LBLJ"),
        star(16, 0, "BitDW — 8 Red Coins"),
        *BOWSER_1,
        segment("seg:bowser1->wf", "→ WF"),
        *WF_ALL,
        *_120_CASTLE_STARS,
        # LBLJ reaches Dark World immediately, so BoB is visited ONCE, for all
        # six stars plus 100 coins (including Bomb Clip).
        segment("seg:pss->bob", "→ BoB"),
        star(1, 5, "BoB — Behind Chain Chomp's Gate (Bomb Clip)"),
        *BOB_ALL_TAIL,
        *_one_twenty_body(),
    ]))

# --- 0 / 1 Star ------------------------------------------------------------

_LOW_STAR_TAIL = [
    *BOWSER_2,
    segment("seg:bowser2->upstairs", "→ Upstairs"),
    segment("seg:bits-entry", "Endless Staircase BLJ"),
    *BOWSER_3,
]

ROUTES.append(route(
    "route:1-star", "1 Star", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:lblj", "LBLJ"),
        *BOWSER_1,
        segment("seg:bowser1->ddd", "→ DDD (Crackslide)"),
        star(9, 0, "DDD — Board Bowser's Sub"),
        segment("seg:ddd->bitfs", "→ BitFS"),
        *_LOW_STAR_TAIL,
    ]))

ROUTES.append(route(
    "route:0-star", "0 Star", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:lblj", "LBLJ"),
        *BOWSER_1,
        segment("seg:bowser1->bitfs", "→ BitFS (SBLJ / DDD Skip)"),
        *_LOW_STAR_TAIL,
    ]))
