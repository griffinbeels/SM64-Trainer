# Adaptive rivals

The practice card selects four rivals and your personal-best ghost by default.
Add Rival searches players for the current star or segment. Clicking a rival
opens the same picker for that slot. A reset symbol beside a chosen name
restores automatic selection. Settings holds the challenge range, rival count
and appearance; Cancel leaves the saved settings unchanged.

## Selection and playback

The Practice card's Strategy/Overall tabs also choose its ghosts. Strategy is
the default: it uses the current strategy's ladder and opponents. Overall uses
the complete Overall curve and admits compatible opponents from other strategies.
Each rival keeps its recorded strategy label. Both views grade the current
strategy's visible saved PB, matching the existing Practice banner's basis.
Legacy rows with no ROM stamp still grade
on the running version; imported times can also supply the rank anchor.
The native provider separately checks whether that PB has playable poses.
An expired clip retains its PB time/rank and reports its playback failure.
Undo resolves the surviving saved slot; historical minima cannot restore an
undone PB. Without a saved basis, four rivals spread across the two lowest
tiers (steps 0 to 9, Capless V to Toad I), one near each evenly spaced step.
A target with no strategy has no rivals, no PB, no chosen slots and no cloud
request; the lineup reports `no_strategy`.

The default range is one to five subdivisions above that basis. The floor stays
above the PB until the final subdivision. Seats keep their player, slot and
colour (his design, 2026-10-16): a new PB replaces only the seats it passed,
and a passed seat first takes that same player's fastest recording still ahead
inside the range. A seat above the range's top is dealt again, so an undone PB
brings nearer rivals back. Only changed rival settings deal every seat afresh.
Chosen slots never move and stay when beaten. When the completed compatible pool
has no automatic opponents in the chosen range, the fastest available recordings
fill automatic slots. They can be slower than the user's PB or outside its rank
range. The separate PB still plays; chosen slots stay protected. This fallback
also applies to an empty fixed range. Target, clock, current ROM and scope
compatibility still apply; incomplete cloud membership cannot prove an empty
range. The pinned author bundle is complete locally and can provide its own
fastest fallback while cloud membership is unavailable. Higher-rank opponents
take priority whenever the chosen range has any.
Automatic opponents exclude your own recordings; the separate PB slot remains.
An explicit chosen rival can still name one of your other recordings.
In Strategy, every automatic, searched or manually selected rival belongs to
the strategy selected for practice. Overall broadens that pool without changing
the practice strategy. The choice is remembered per star or segment; Strategy
keeps its strategy-specific protected choices, and Overall has separate choices.
Switching back restores them. Stored `strategy_only` settings migrate automatically.

Adding fills a vacancy or replaces the slowest automatic rival. Other chosen
rivals and the separate PB remain protected. Decreasing the count retains hidden
choices; restoring the count restores those slots. Each slot keeps its explicit
colour when the selected recording changes. PB uses the appearance colour.
Saving a local run adds it to an available automatic slot without replacing
another chosen rival. If every slot is protected, the run stays in the library.
Removing the saved run clears its local override without clearing other choices.

The local provider supplies certified compatible recordings. Explicit private
imports and saved library choices can occupy protected slots without qualifying
for automatic admission. Unknown historical ROM metadata does not become a
certificate. The source session, attempt and start time distinguish local
recordings across fresh profiles; exported credits never establish that lineage.
Historical local PBs remain eligible for the separate PB slot. Community
admission still requires complete validated run evidence and an explicit ROM.

A PB save or undo immediately reads the authoritative saved state. There is no
confidence debounce. An unchanged set retains its acknowledged preparation;
refreshing its rank also retains its playback availability and refusal reason.
If a concurrent read checks the PB before its poses finish saving, a successful
pose commit validates that same current PB and retries the refused preparation.
Unrelated, superseded or still-unavailable runs cannot trigger that retry.
An actual changed set supersedes the pending generation. The updates message
appears only when the renderer has prepared that set and has not adopted it.
Disabled or unavailable ghosts do not promise a pending update. File validation,
rank selection and preparation run outside the game poll loop. Native playback
retains the current roster during same-strategy updates until an acknowledged
replacement can adopt at a certified full start. Changing strategy immediately
revokes its old roster. Changing Strategy/Overall also revokes the previous
scope, including when both happen to select the same clips. Readiness arriving
later waits for another start. Population lookup failure also revokes the old
scope; a refused scope swap restores the preceding choice without resuming
its old race mid-attempt.

A fresh segment arm selects its roster before a move clock resolves; drawing
waits for the existing authoritative clock policy. Clock rebases and save-state
continuations cannot select another roster. Native active, pending and staging
storage shares a 192 MiB budget; a refusal keeps the current race intact.

## Local population

After a successful attempt commits, a bounded worker validates the complete run
and preserves its native poses. It skips slower/equal recordings when the same
player and strategy already has a faster recording in that subdivision.
Immutable earlier improvements remain available for exact choices and climb
testing. The local pilot caps committed metadata at 16,384 rows and unique clips
at 512 MiB. Capacity refusal preserves existing recordings. This pilot does not
yet garbage-collect obsolete unchosen improvements; a hard crash can leave an
unindexed asset. These local limits do not implement remote account quotas.

Search filters the selected scope before paging, uses normalized indexed
player prefixes, at most 20 records per page and a checked keyset cursor.
Scope is part of the query/cache identity; a stale search, manual choice or
settings draft cannot change a different view.
The UI debounces input, caches bounded results and drops
stale replies. Private-import credit remains user-supplied. The local HTTP server
is loopback-only; a future cloud endpoint must independently authenticate and
authorize every request.

## Test a fresh climb

Close the other trainer, then run `run-ghost-climb.bat` from this checkout.
It builds the native bundle, creates an isolated tester with no practice history
or PB, copies usable saved ghosts as `gtest1`, `gtest2`, etc., and opens port 8070.
Open that address in the
browser. Install the source build's capture layer through Setup with Project64
closed if Setup requires installation, then open the practice ROM. Source builds
can refresh their own prior layer but do not automatically replace another
installer's layer. A competing recorder makes the launcher refuse to start.

Each ordinary launch creates a new profile with the same definitions and
calibration and its own player identity. Sessions, events, attempts and PBs start
empty. Each usable target/strategy/time is represented once, with an independent
fictional opponent identity and unchanged recorded timing and poses. Older
clips receive their own proven input-track pads on a derived playback copy,
using the normal calibrated join. Original clips remain untouched; captured
pads are never replaced, and absent or unproven inputs remain unknown. Choose
Shoot into the Wild Blue → Standard in the Target picker to use the eleven
sample ghosts there when their ranks match your range. Other practiced targets
and strategies also have samples.
Your own PB slot appears after you play and save a PB. The card displays
`Developer simulation · Fresh tester`; these records never establish community
authentication. Your original practice database, recordings and PBs stay intact.
To repeat an existing profile, use:

```powershell
.\run-ghost-climb.bat --profile "C:\absolute\profile"
```

The reported Wild Blue sample set contains eleven distinct playable times,
10"66–11"23. New testers use Follow my progress with up to four rivals plus PB.
Without a PB, the range starts one to five subdivisions above Capless 5. These
advanced samples do not fill that beginner range; an empty roster is correct.
Saving or clearing a PB immediately recomputes the range and roster. For a
four-rival rendering stress test, choose Choose ranks in Rival settings and
expand the fixed range to cover the samples. An explicit fixed range continues
to apply after a reset. Search `gtest` to choose an opponent
manually. A fresh double-click creates another empty identity; resuming retains
the tester's new practice history.
The developer fixture retires automatic opponents you have beaten,
even within the same subdivision, while preserving explicit chosen rivals.
After every opponent is beaten, it uses the same fastest-available fallback.
This fixture rule does not redefine production fixed-rank settings.
Resuming an older fresh tester upgrades its input copies before recording starts.
Its identity, sessions, attempts, PBs, credits and chosen rival slots survive;
the old immutable population remains available beside the replacement pool.
Paths defaults off for new profiles. Explicit appearance preferences still win.
Practice log settings → Ghost settings opens the shared appearance, sound and
path controls directly. Rival settings also retains Appearance and sound.
Both dialogs keep changes local until Done; Cancel leaves preferences unchanged.
If the empty card's rank banner is folded, expand its attempts chevron to reach
the existing Strategy/Overall tabs. The ghost roster remains visible while folded.

The earlier seeded Wild Blue experiment remains available explicitly through
`tools/ghost_climb.py --simulate-wild-blue --launch`. It starts with a portable
PB at the slowest source recording and named `Simulated` opponents. Existing
profiles still resume through `--profile`.

The generic empty-profile/retained-population workflow remains available through
`tools/ghost_climb.py --seed --prepare --launch`. For historical clips lacking
an observed ROM stamp, the developer can explicitly
declare the source ROM when seeding. Use this only when that declaration is true:

```powershell
.venv\Scripts\python.exe tools\ghost_climb.py --seed --certify-source-version us --player-name Griff
```

It still rejects invalid/incomplete attempts and does not certify private
imports. New recordings from this build carry the observed opening/closing ROM.

Try one, two and four rivals plus PB; save a PB, undo one immediately,
restart from the full beginning and repeat a climb. Check frame pacing, first-load
audio, readability and overlap during actual gameplay. Unit tests and compiled
witnesses cannot establish those live observations.

## Cloud continuation

The provider boundary is local today. Community accounts, uploads, remote player
discovery and downloads are not connected. The cloud plan starts on Workers Free
and measures complete authenticated validation CPU and database/request limits
before choosing a larger plan. Object storage has its own costs.

The transport experiment selected lossless byte-column shuffle plus Deflate 6
in bounded 256-record blocks. Its round-trip proof is separate from shipping
transport and deployed CPU measurement. The cloud implementation must version
framing, enforce decoded-size limits, preserve all validation, authorize object
access, apply atomic quotas, rate-limit queries and retain exact chosen identities.
Authentication cannot prove honest client gameplay. No cloud deployment follows
from this local pilot.

Implementation owners: `ghost/adaptive.py`, `ghost/population.py`,
`ghost/rivals.py`, `ghost/rival_recording.py` and `tools/ghost_climb.py`.
