# Adaptive rivals

The practice card selects four rivals and your personal-best ghost by default.
Add Rival searches players for the current star or segment. Clicking a rival
opens the same picker for that slot. A reset symbol beside a chosen name
restores automatic selection. Settings holds the challenge range, rival count
and appearance; Cancel leaves the saved settings unchanged.

## Selection and playback

Automatic selection uses the complete current Overall curve and the target's
canonical clock. The saved basis is the best current strategy slot with a valid
backing attempt, explicit matching ROM, and a complete native clock span.
Undo resolves the surviving saved slots; historical minima cannot restore an
undone PB. Without a qualifying basis, selection starts at the bottom tier.

The default range is one to five subdivisions above that basis. The floor stays
above the PB until the final subdivision. At the ceiling, only faster runs in
that subdivision qualify. Fixed mode selects an absolute rank range.
Current-strategy-only uses the strategy selected for practice.

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
A historical PB selected explicitly from the library uses a protected rival
slot when its provenance cannot supply the separate certified PB slot.

A PB save or undo cancels obsolete pending preparation and opens a five-second
settle window for that target. Target or strategy changes cannot bypass it.
The session reads the final saved state when the window expires. File validation,
rank selection and preparation run outside the game poll loop. Native playback
retains the current roster until an acknowledged replacement can adopt at a
certified full start. Readiness arriving later waits for another start.

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

Search uses normalized indexed player prefixes, at most 20 records per page and
a checked keyset cursor. The UI debounces input, caches bounded results and drops
stale replies. Private-import credit remains user-supplied. The local HTTP server
is loopback-only; a future cloud endpoint must independently authenticate and
authorize every request.

## Test a fresh climb

Close the other trainer, then run `run-ghost-climb.bat` from this checkout.
It builds the native bundle, seeds compatible saved recordings, creates a new
practice profile and opens the trainer on port 8070. Open that address in the
browser. Install the source build's capture layer through Setup with Project64
closed, then open the practice ROM. Source builds do not silently replace an
installed layer. A competing recorder makes the launcher refuse to start.

Each launch creates a new profile with the same definitions and calibration,
empty attempts/PBs and the retained population. Your original practice database,
recordings and PBs stay intact. To repeat an existing profile, use:

```powershell
.venv\Scripts\python.exe tools\ghost_climb.py --launch --profile C:\absolute\profile
```

For historical clips lacking an observed ROM stamp, the developer can explicitly
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
