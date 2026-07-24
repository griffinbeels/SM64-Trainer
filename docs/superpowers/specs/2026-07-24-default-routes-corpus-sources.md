# Default Routes corpus — source transcription (Ukikipedia, fetched 2026-07-24)

Companion to `2026-07-24-default-routes-corpus-design.md`. This file is the
**provenance record**: what the sources actually say, so a future session can
audit a seeded route without re-fetching. Corpus authoring is transcription, not
invention — if this file and the seed disagree, this file is right.

Fetched with `curl` + a browser User-Agent (WebFetch 403s on ukikipedia.net):
`/wiki/RTA_Guide/{16_Star,70_Star,120_Star,0/1_Star}` and
`/wiki/RTA_Guide/<Course>` for all 15 main courses.

## Star-id conventions used throughout

`(course_id, star_id)` with **0-based** star ids, matching `star_grab.py`
(`star_id = last_completed_star - 1`) and `addresses.STAR_NAMES`.
`+100` means the 100-coin star = **star id 6** for courses 1–15.

## Community-alias glossary

The guides use nicknames; these are the bindings this corpus assumes.

| Alias | Star |
|---|---|
| Cannonless / Cless (WF) | Blast Away the Wall — (2, 5) |
| Owlless / Owl Star (WF) | Fall onto the Caged Island — (2, 4) |
| Wild Blue (WF) | Shoot into the Wild Blue — (2, 2) |
| Whomp King (WF) | Chip off Whomp's Block — (2, 0) |
| Tower (WF) | To the Top of the Fortress — (2, 1) |
| Talon Star / Klepto (SSL) | In the Talons of the Big Bird — (8, 0) |
| Pyramid (SSL, 16★ No-LBLJ) | Shining Atop the Pyramid — (8, 1) |
| Pillarless / Pless (SSL) | Inside the Ancient Pyramid — (8, 2) |
| Log Star / Sidehop (LLL) | Red-Hot Log Rolling — (7, 3) |
| Big Bully (LLL) | Boil the Big Bully — (7, 0) |
| Lava Boost (LLL) | Hot-Foot-It into the Volcano — (7, 4) |
| Elevator Star (LLL) | Elevator Tour in the Volcano — (7, 5) |
| Tama Pless (SSL) | a Pillarless variant of (8, 2) — same star |
| Dorrie / Swimming Beast (HMC) | Swimming Beast in the Cavern — (6, 0) |
| Emergency Exit (HMC) | A-Maze-Ing Emergency Exit — (6, 4) |
| Rolling Rocks (HMC) | Watch for Rolling Rocks — (6, 5) |
| Island Hop (BoB) | Shoot to the Island in the Sky — (1, 2), no wing cap |
| Bomb Clip (BoB) | Behind Chain Chomp's Gate — (1, 5), clipped |
| Crackslide / SBLJ / DDD Skip | movement tricks, not stars |
| DW / BitDW Reds | Bowser in the Dark World 8 Red Coins — (16, 0) |
| BitFS Reds | (17, 0) · BitS Reds — (18, 0) |
| HMC Toad | castle-secret star (0, 0) |
| Upstairs Toad (near TTM) | (0, 1) · Tippy Toad (near TTC) — (0, 2) |
| MIPS 1st (15 stars) | (0, 3) · MIPS 2nd (50 stars) — (0, 4) |

## Castle-secret stars (course 0) — decomp evidence

`n64decomp/sm64` master, fetched 2026-07-24:

`src/game/save_file.h`
```c
#define SAVE_FLAG_COLLECTED_TOAD_STAR_1  /* 0x01000000 */ (1 << 24)
#define SAVE_FLAG_COLLECTED_TOAD_STAR_2  /* 0x02000000 */ (1 << 25)
#define SAVE_FLAG_COLLECTED_TOAD_STAR_3  /* 0x04000000 */ (1 << 26)
#define SAVE_FLAG_COLLECTED_MIPS_STAR_1  /* 0x08000000 */ (1 << 27)
#define SAVE_FLAG_COLLECTED_MIPS_STAR_2  /* 0x10000000 */ (1 << 28)
#define SAVE_FLAG_TO_STAR_FLAG(cmd) (((cmd) >> 24) & 0x7F)
```
⇒ star-flag bits 0,1,2 = Toad 1/2/3; bits 3,4 = MIPS 1/2.

`src/game/behaviors/mips.inc.c`
```c
bhv_spawn_star_no_level_exit(STAR_INDEX_ACT_4 + o->oBhvParams2ndByte);
// MIPS_BP_15_STARS = 0, MIPS_BP_50_STARS = 1  ⇒  indices 3 and 4
```
Two independent paths agree on MIPS = ids 3 and 4. The **Toad↔index binding**
(basement/HMC = 0, upstairs/TTM = 1, tippy/TTC = 2) follows the flag order plus
the 12/25/35-star spawn thresholds — **VERIFY at the live gate**.

The live journal (`%LOCALAPPDATA%\SM64Trainer\data\tracker.db`, 736
`star_collected` rows on 2026-07-24) contains **no** course-0 grabs, so there is
no empirical evidence yet.

## Main-category routes

### 16 Star — `/RTA_Guide/16_Star`

**Standard (No LBLJ)** — the guide interleaves movements with stars verbatim:
```
Lakitu Skip
Castle Entrance to BoB Movement | BoB: Behind Chain Chomp's Gate            (1,5)
BoB to WF Castle Movement | Cannonless | Wild Blue | Whomp King | Tower | Owl Star
                                                        (2,5)(2,2)(2,0)(2,1)(2,4)
WF to CCM Castle Movement | Wall Kicks Will Work (4,5) | Li'l Penguin Lost  (4,1)
CCM to DW Castle Movement | BitDW Red Coins (16,0) | Bowser Battle 1
DW to SSL Castle Movement | Talon Star (8,0) | Pyramid                      (8,1)
SSL to LLL Castle Movement | 8-Coin Puzzle with 15 Pieces                   (7,2)
LLL to HMC Castle Movement | HMC Toad (0,0) | Emergency Exit (6,4) | Rolling Rocks (6,5)
MIPS Clip
DDD: Board Bowser's Sub                                                     (9,0)
BitFS (No Reds) | Bowser Battle 2
Backwards Long Jumps
BitS (No Reds) | Bowser Battle 3
```
Star total = 1+5+2+1+2+1+3+1 = **16** ✓ (HMC Toad counts).

**Beginner (No Dark World Reds)** — standard minus (16,0); HMC becomes
Emergency Exit (6,4), HMC Toad (0,0), Swimming Beast (6,0), Rolling Rocks (6,5).

**WF 100c (CCM Skip)** — standard, plus WF 100 Coins (2,6), minus the CCM visit.

**Standard (LBLJ)**:
```
Lakitu Skip | LBLJ
BitDW Reds (16,0) | Bowser Battle 1
WF: Cannonless (2,5) | Owlless (2,4) | Wild Blue                            (2,2)
SSL: Pillarless (8,2) | Talon Star (8,0) | Pyramid                          (8,1)
LLL: Red Coins (7,2) | Log Star (7,3) | Big Bully (7,0)
     | choose one: Lava Boost (7,4) OR Elevator Star                        (7,5)
HMC: Emergency Exit (6,4) | HMC Toad (0,0) | Rolling Rocks (6,5) | Swimming Beast (6,0)
MIPS Clip | DDD: Board Bowser's Sub (9,0)
BitFS (No Reds) | Bowser Battle 2 | BLJ | BitS (No Reds) | Bowser Battle 3
```
Star total = 1+3+3+4+4+1 = **16** ✓

### 70 Star — `/RTA_Guide/70_Star`

Three independent options on the **HMC Late** route, taken in increasing skill
order — `No TTC100 | TTC100`, `CCM18 | CCM17`, `No Island Hop | Island Hop` —
named Beginner / Intermediate / Advanced / Expert. Plus **HMC Early**.

```
Lobby    Lakitu Skip | BoB: [IH] Island Hop (1,2) · Behind Chain Chomp's Gate (1,5)
         PSS 1st (19,0) | WF all six (2,5)(2,4)(2,3)+(2,6)(2,2)(2,0)(2,1)
         PSS 2nd (19,1) | TotWC (21,0)
         BitDW Reds (16,0) | Bowser Battle 1
         [no-IH] BoB: Shoot to the Island in the Sky (1,2)
         CCM: Wall Kicks (4,5) · Li'l Penguin Lost (4,1)
              [CCM18] Slip Slidin' (4,0) · Big Penguin Race+100 (4,2)+(4,6)
              [CCM17] Slip Slidin'+100 (4,0)+(4,6)
         BBH: Big Boo's Balcony (5,4) · Secret of the Haunted Books (5,2)
Basement MIPS 1st (0,3)
         SSL: [CCM17] Pyramid Puzzle (8,5) · Inside the Ancient Pyramid (8,2)
              · Talons (8,0) · Shining Atop (8,1)
         LLL: (7,3)(7,2)(7,0)(7,1)(7,4)(7,5)
         DDD: Chests (9,1) · Board Bowser's Sub (9,0) · Manta Ray (9,4)
         BitFS Reds (17,0) | Bowser Battle 2
Upstairs WDW: Secrets+100 (11,2)+(11,6) · Express Elevator (11,3)
              · Shocking Arrow Lifts (11,0) · Top o' the Town (11,1)
         THI: Five Itty Bitty (13,3) · Pluck the Piranha (13,0) · Tip Top (13,1)
         Upstairs Toad (0,1)
         TTM: Scale (12,0) · Breathtaking View (12,4) · Blast to Lonely Mushroom (12,5)
              · Scary 'Shrooms (12,2) · Mysterious Mountainside (12,3)
         SL: Big Head (10,0) · Deep Freeze (10,2) · Whirl (10,3) · Chill (10,1)
             [No TTC100] Shell Shreddin' (10,4)
         >>> re-enter SL and pause-exit → basement
Basement MIPS 2nd (0,4) | HMC Toad (0,0) — before entering HMC
2        HMC: Swimming Beast (6,0) · Metal-Head (6,2) · Emergency Exit (6,4)
              · Rolling Rocks (6,5)
         >>> re-enter HMC and pause-exit → tippy
Tippy    RR: Cruiser (15,0) · Tricky Triangles (15,4) · Coins Amassed (15,2)
             · Swingin' (15,3)
         Tippy Toad (0,2)
         TTC: [TTC100] Stomp on the Thwomp+100 (14,3)+(14,6) else (14,3) alone
              · Roll into the Cage (14,0) · Pit and the Pendulums (14,1)
              · Get a Hand (14,2) · Timed Jumps (14,4) · Stop Time for Red Coins (14,5)
         BitS (No Reds) | Bowser Battle 3
```
**HMC Early** — after LLL go straight to HMC (all HMC stars + Toad), then DDD;
SL must take Shell Shreddin' (10,4) in place of the 2nd MIPS star; after SL go
straight to Tippy instead of exiting the course.

### 120 Star — `/RTA_Guide/120_Star`

Two variants differing **only inside Lobby**.
```
Lobby (Non-LBLJ) BoB(1): Behind Chain Chomp's Gate (1,5)
                 WF all | SA (24,0) | JRB all
                 PSS 1st (19,0) | TotWC (21,0) | PSS 2nd (19,1)
                 BitDW Reds (16,0) | Bowser Battle 1
                 BoB(2): Big Bob-omb (1,0) · Footrace (1,1) · 100+Red Coins (1,6)+(1,3)
                         · Shoot to the Island (1,2) · Mario Wings (1,4)
Lobby (LBLJ)     LBLJ | BitDW Reds | Bowser Battle 1
                 ...WF / SA / JRB / castle secret stars as above...
                 BoB: all six (incl. Bomb Clip)
Basement  MIPS 1st (0,3) | SSL all | HMC all + CotMC (20,0) | HMC Toad (0,0)
          LLL all | MIPS 2nd (0,4) | VCUtM (22,0)
Middle    CCM all | BBH all
          DDD(1): 100+Pole-Jumping (9,6)+(9,2) · Chests (9,1) · Board Bowser's Sub (9,0)
          BitFS Reds (17,0) | Bowser Battle 2
          DDD(2): Manta Ray (9,4) · Through the Jet Stream (9,3) · Collect the Caps (9,5)
          >>> re-enter BitFS, pause-exit → upstairs
Upstairs  WDW all | THI all | Upstairs Toad (0,1) | TTM all | SL all
Tippy     WMotR (23,0) | TTC all | Tippy Toad (0,2) | RR all
          BitS Reds (18,0) | Bowser Battle 3
```

### 0/1 Star — `/RTA_Guide/0/1_Star`
```
Lakitu Skip | LBLJ
BitDW (No Reds) | Bowser Battle 1
30 Star Door Skip: [1★] Crackslide → DDD: Board Bowser's Sub (9,0)
                   [0★] SBLJ / DDD Skip → straight to BitFS
BitFS (No Reds) | Bowser Battle 2
Backwards Long Jumps
BitS (No Reds) | Bowser Battle 3
```

## Per-course ordered star lists ("Stage RTA" source)

`+6` = the 100-coin star collected with the star it follows.

| Course | 120 | 70 | 16 |
|---|---|---|---|
| BoB (1) | 5, 0, 1, 3+6, 4, 2 | 5, 2 | 5 |
| WF (2) | 5, 4, 3+6, 2, 0, 1 | same as 120 | 5, 2, 0, 1, 4 |
| JRB (3) | 0, 5, 3+6, 4, 2, 1 | — | — |
| CCM (4) | 5, 3, 1, 0, 2+6, 4 | CCM17: 5, 1, 0+6 · CCM18: 5, 1, 0, 2+6 | 5, 1 |
| BBH (5) | 0, 3+6, 5, 1, 4, 2 | 4, 2 | — |
| HMC (6) | 1+6, [CotMC], 0, 3, 2, 4, 5 | 0, 2, 4, 5 | 0, 4, 5 |
| LLL (7) | 3, 2, 0, 1, 4+6, 5 | 3, 2, 0, 1, 4, 5 | 3, 2, 0, (4 or 5) |
| SSL (8) | 2, 3, 0, 4, 5+6, 1 | 2, 0, 1 | 2, 0, 1 |
| DDD (9) | 2+6, 1, 0, ⟨BitFS⟩, 4, 3, 5 | 1, 0, 4 | 0 |
| SL (10) | 0, 5, 4+6, 2, 3, 1 | HMC-Late+TTC100: 0, 2, 3, 1 · else: 0, 2, 4, 3, 1 | — |
| WDW (11) | 5, 4+6, 3, 1, 2, 0 · Beginner: 5, 2+6, 3, 1, 4, 0 | 2+6, 3, 1, 0 | — |
| TTM (12) | 0, 1, 4, 5, 2+6, 3 | 0, 4, 5, 2, 3 | — |
| THI (13) | 5, 3, 4+6, 0, 1, 2 | 3, 1, 0 · with THI Reds: 3, 1, 4, 0 | — |
| TTC (14) | 3+6, 0, 1, 2, 4, 5 | same as 120 · no-TTC100: 0, 1, 2, 3, 4, 5 | — |
| RR (15) | Beginner: 0, 1+6, 5, 4, 2, 3 · Expert: 0, 5+6, 1, 4, 2, 3 | 0, 4, 2, 3 | — |

Named castle-movement clips on the course pages (evidence that the community
treats these as first-class practiced units): `Entry_to_BOB`, `BOB_to_WF`,
`WF_to_CCM`, `Basement_to_SSL`, `Ssl_to_lll`, `Ssl_to_hmc_door`, `Lll_toad_hmc`,
`Hmc_toad_first_{easy,fast}`, `Hmc_toad_to_{door,entry}`, `Hmc_result_to_toad`,
`Leave_hmc_{standard,fastest}`; BBH has "castle movement (entering)" and
"(re-entry/leaving)".
