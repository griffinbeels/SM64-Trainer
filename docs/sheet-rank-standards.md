# Sheet rank standards

Every current Ultimate Sheet approach and subsection receives all eight tier
cutoffs and the derived Capless floor: 45 reachable subdivisions. No minimum
fill rate applies. This is task 0126's corrected contract, including the user's
2026-09-06 clarification about narrow time distributions.

## Fitting

`library/ladders.py` model 4 fits each row's own population. Annotated US and JP
times remain separate; unannotated times form the combined base. Missing samples
use the published best/ideal or the audited related-row estimate, retaining zero
actual submissions and explicit provenance. New observations replace estimates.

Mario I targets the fastest supported performance peak, which may equal a
widely shared record. The fitter scans three-frame windows (two frames between
their endpoints), requiring at least three players and twice the population of
the next equally wide window. It takes the first qualifying window's observed
lower median, provided that is at or faster than the calibrated 6.7th percentile.
This recognizes nearby-frame clusters, ignores isolated records, and prevents
a slower global mode from softening the elite target. Bounded windows prevent
a few slower observations from joining and erasing a supported fast peak.

Without that evidence, smooth or sparse populations use the elite percentile
as a provisional fallback. There is no compulsory extra frame above the record.
The window, support and density settings are explicit in the model metadata;
they are fitting heuristics, not a claim to measure an individual's consistency.
Mario V and Metal V are fitted jointly around Mario I;
the top nine subdivision steps use a whole number of frames. Slower tiers follow
the empirical percentiles, with Bronze including the slowest observation. Every
adjacent tier cutoff is at least five frames apart. If a range is too narrow,
the remaining standards extend slower, one frame per subdivision, rather than
dropping ranks or requiring identical cutoffs. The Library identifies that
extension as estimated targets beyond the submitted range.

For example, the current JRB door row has 57 submissions at 2.46 seconds and one
at 2.50. Mario I is 2.46; Mario V is 2.60; Metal V is 2.76; Toad V is 3.76.
Capless I is 3.80, and each successive frame reaches the next lower subdivision.
The observed maximum is evidence, not a claim about the slowest possible run.

## Grading and Capless

`ranks/timecurve.py` interpolates in continuous game-frame coordinates, preserving
every exact displayed tier cutoff, including hand-entered values such as 8.85.
Interpolating printed centiseconds directly can skip a subdivision because the
clock advances 3, 3, then 4 centiseconds. `ui/timecurve.js` is its browser twin;
cross-language tests compare the actual implementations at frames and boundaries.

Capless I–IV continue the easiest tier's per-subdivision frame spacing (at least
one frame). Below IV the score decays positively toward zero. Capless V stays an
unbounded floor; zero denotes no attempt. The same curve serves explicit manual
ladders, whose tier cutoff numbers remain unchanged. Legacy manual ties retain
hardest-tier-wins classification and a finite inverse; they cannot promise the
45 distinct positions guaranteed by complete generated ladders.

## Identity, edits, and refresh

Strategy identity uses `library/strategy_signature.py`'s calibrated matching
profile, independently of grading. Changing a rank formula must not reassign
saved attempts to another Sheet row. The profile still follows new observations.
Ordinary-row matches retain priority. Target-named rows recover unclaimed
historical aliases such as TJ Owlless so existing PBs still open the right
section; their canonical storage slot remains Standard.

The current Sheet fit supplies the foundation in `RankStandards.ladders`.
Unchanged materialized bundled defaults yield to it. Detectable legacy edits and
explicit per-cutoff US/JP overlays survive refresh and restart, including an edit
equal to an old seed number. Unedited cutoffs continue following the Sheet. Manual
strategies without a Sheet source retain their stored standards. Reset removes
edits; clearing a JP edit reveals the annotated Sheet JP fit or combined base.

Library and Practice consume the same resolved standards. Both retain all nine
rank bands even when no submission occupies a band. Runner search can filter
empty matches. Mounted views refetch after standards changes or reconnect.

Checks live in `test_complete_sheet_ladders.py`, `test_sheet_seed_precedence.py`,
`test_cross_language_parity.py`, and `test_ui_complete_sheet_ladders.py`, alongside
the existing placement, import, refresh, and synchronization suites.
