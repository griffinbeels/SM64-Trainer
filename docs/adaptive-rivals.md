# Adaptive rivals

The practice card selects four rivals and your personal-best ghost by default.
Add Rival searches players for the current star or segment. Clicking a rival
opens the same picker for that slot. A reset symbol beside a chosen name
restores automatic selection. Settings holds the challenge range, rival count
and appearance; Cancel leaves the saved settings unchanged.

## Selection and playback

Automatic selection uses the current practice strategy's ladder and canonical
clock, through the same scoring path as Practice Strategy. Its anchor is that
strategy's current visible saved PB. Legacy rows with no ROM stamp still grade
on the running version; imported times can also supply the rank anchor.
The native provider separately checks whether that PB has playable poses.
An expired clip retains its PB time/rank and reports its playback failure.
Undo resolves the surviving saved slot; historical minima cannot restore an
undone PB. Without a saved basis, selection starts at the bottom tier.

The default range is one to five subdivisions above that basis. The floor stays
above the PB until the final subdivision. At the ceiling, only faster runs in
that subdivision qualify. Fixed mode selects an absolute rank range.
Every automatic, searched or manually selected rival belongs to the strategy
selected for practice. Switching strategy uses its own saved PB, ladder, pool
and protected choices. Stored `strategy_only` settings migrate automatically.

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

A PB save or undo cancels obsolete pending preparation and opens a five-second
settle window for that target. Target or strategy changes cannot bypass it.
The session reads the final saved state when the window expires. File validation,
rank selection and preparation run outside the game poll loop. Native playback
retains the current roster during same-strategy updates until an acknowledged
replacement can adopt at a certified full start. Changing strategy immediately
revokes its old roster. Readiness arriving later waits for another start.

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

Search filters the current strategy before paging, uses normalized indexed
player prefixes, at most 20 records per page and a checked keyset cursor.
The UI debounces input, caches bounded results and drops
stale replies. Private-import credit remains user-supplied. The local HTTP server
is loopback-only; a future cloud endpoint must independently authenticate and
authorize every request.

## Test a fresh climb

Close the other trainer, then run `run-ghost-climb.bat` from this checkout.
It builds the native bundle, creates an isolated Wild Blue tester with named
opponents copied from your real Standard recordings, and opens port 8070.
Open that address in the
browser. Install the source build's capture layer through Setup with Project64
closed if Setup requires installation, then open the practice ROM. Source builds
can refresh their own prior layer but do not automatically replace another
installer's layer. A competing recorder makes the launcher refuse to start.

Each ordinary launch creates a new profile with the same definitions and
calibration, its own player identity, and one starting PB at the slowest playable
Wild Blue recording. Every opponent retains its original timing and pose bytes.
The tester can race that starting clip through a marked portable PB reference.
Your first played/saved PB supersedes the reference. The card displays
`Developer simulation · Wild Blue`; these records never establish community
authentication. Your original practice database, recordings and PBs stay intact.
To repeat an existing profile, use:

```powershell
.\run-ghost-climb.bat --profile "C:\absolute\profile"
```

The reported sample set contains eleven distinct playable times, 10"66–11"23.
Starting at 11"23 selects two rivals inside the default range: 11"06 and 11"03.
Four is the capacity; available recordings determine the actual count. Saving
11"03 brings faster Gold ghosts into range. Search `Simulated` to choose a
specific opponent manually. A fresh double-click restarts the climb independently.

The generic empty-profile/retained-population workflow remains available through
`tools/ghost_climb.py --seed --prepare --launch`. For historical clips lacking
an observed ROM stamp, the developer can explicitly
declare the source ROM when seeding. Use this only when that declaration is true:

```powershell
.venv\Scripts\python.exe tools\ghost_climb.py --seed --certify-source-version us --player-name Griff
```

It still rejects invalid/incomplete attempts and does not certify private
imports. New recordings from this build carry the observed opening/closing ROM.

Try one, two and four rivals plus PB; save a PB, undo one within five seconds,
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
