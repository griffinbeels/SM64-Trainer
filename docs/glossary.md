# Glossary

The words this project invented, and the words it uses in a way no other
project would recognise. When we talk about changing something, these are the
names we use for it.

**A term earns a row when this project is what gives it meaning.** Backpressure
and idempotency belong in a textbook; MARELO, the practice log and the caveat
mark exist only here, so only they are written down here.

Two rules make this an artifact rather than a document, and
`uv run python tools/check_glossary.py` enforces both:

1. **Closed.** Every word in this glossary that appears inside another
   definition carries a `[[mark]]`. Closure decides how long this file is —
   start from the words we say, close it, and it lands where it lands.
2. **Active voice.** No definition hides its actor, because the actor is nearly
   always the module you would open.

Each row names the module that owns it. `Not` appears only where two terms
genuinely get confused.

---

## What you practice

### Target

The one thing you are practicing right now — either a [[star]] or a
[[segment]]. Setting a target tells the trainer which [[ladder]] to grade
against and which [[personal best]] to compare with.

- **Lives** — the target rules (`src/sm64_events/tracking/practicable.py`)
  → the [[selector]] and the [[practice log]]

### Target queue

The line of detections waiting behind the held [[target]] — first hooked,
first held. Performing a [[segment]]'s own start deliberately (a course
exit, a door [[moment]]) takes an empty hand and becomes a
[[hooked target]]; a detection that fires while the hand already holds
something waits its turn; when the front completes or dies, the oldest
detection still alive takes over, and an empty queue leaves the hand
neutral. A [[segment]] that
arms by mere presence — LBLJ on a castle entry, a pipe family on a course
entry — never enters the queue, and neither does a [[subsection]] whatever
its clause shape: only your click selects a piece, and its detection
speaks through its parent [[star]] instead.

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
  → the [[selector]] and the [[practice log]]

### Hooked target

A [[target]] a detection set: the front of the [[target queue]]. The trainer
holds it until it completes, until you genuinely abandon it (an [[anchor]]
landing inside a foreign course), or until its own staleness budget expires
— walking back through a course to redo its start keeps the hold. A
[[star]] grab takes the hand from a stale hooked one, never from one the
matcher still holds armed — except a hooked [[subsection]], which yields
to its parent [[star]]'s grab armed or not; a PICKED one holds even
through that grab, because explicit choices take priority.

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
  → the [[selector]]
- **Not** — a [[picked target]]. A click is sovereign; a hook is the
  trainer following your play.

### Picked target

A [[target]] you clicked. Nothing detected may steal it, a [[star]] grab may
not move it, choosing one empties the [[target queue]], and it retires only
by the standing rules (walking into a course it is not practiced in, with
its arm gone).

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
  → the [[selector]]

### Star

One of Super Mario 64's collectable stars, identified by its course and its
slot inside that course. Every star is a [[target]] you can practice.

- **Lives** — the course and star names (`src/sm64_events/memory/addresses.py`)
- **Not** — a [[target]]. A [[segment]] is also a [[target]]; only some
  [[target]]s are stars.

### Segment

A named stretch of play with a start trigger and an end trigger — a movement
like LBLJ, or a Bowser fight. A segment is a [[target]] you practice the same
way you practice a [[star]], and the trainer times it on [[real time]] rather
than [[in-game time]].

- **Lives** — the trigger vocabulary and matcher
  (`src/sm64_events/tracking/segments.py`) → the segment builder

### Subsection

A piece of a [[star]], of a [[segment]], or of a castle area, practiced on
its own — the climb rather than the whole [[star]]. A subsection IS a
[[segment]], and names what it belongs to — several [[star]]s when the same
piece serves them all, the way both of LLL's volcano [[star]]s enter the
volcano identically — so it carries its own [[personal best]], its own [[ladder]] and
its own rows in the [[practice log]]. The [[star]] owns the hand: a piece arms
and records underneath its parent without ever taking the [[target]] slot
by detection — its completion points the slot at the parent [[star]], never
at itself — and only clicking a piece makes it the [[target]], where it
then holds even through its parent's grab. Inside a course a piece always
belongs to something specific; a castle-side
piece belongs to its area, which is as high as the castle goes — his own
split, and the [[recorder]]'s parent picker follows it (an area tile
answers in one click, a course tile opens its [[star]]s, and the + beside a
chosen parent adds another).

- **Lives** — the trigger vocabulary and matcher
  (`src/sm64_events/tracking/segments.py`) → the [[selector]]
- **Not** — a [[route step]]. A [[route step]] names [[target]]s to complete in
  order; a subsection is one [[target]].

### Clock start

Where a [[segment]]'s timer begins, chosen per definition. "At the start
trigger" counts from the trigger's own [[frame]], fades and all — how every
definition timed before the move clock existed. The seeded castle movements
time from the move too: his call, made against the measured re-timing of
his own history rather than as a silent default. "When Mario can
move" detects the [[attempt]] at the trigger but counts from where Usamune's
own timer restarts in the section the trigger led into — open the door, go
through, and the recorded time matches what Usamune shows. The [[recorder]]
saves every recording with the move clock; the matcher rebases to the
section entry the start caused and never to a later one mid-piece.

- **Lives** — the trigger vocabulary and matcher
  (`src/sm64_events/tracking/segments.py`) → the segment builder's "Clock
  starts" control
- **Not** — a [[strategy]]. A [[strategy]] says how you play the piece; the
  clock start says which [[frame]] the trainer measures the piece from.

### Moment

Something the player does that a [[subsection]] can start or end on — opening
a door, triggering a textbox, grabbing a pole or a tree, picking up a bob-omb,
pressing a switch, defeating an enemy. Most kinds read Mario's own action; a
[[caused moment]] reads the object he acted on instead. Either way a moment
happens inside one course, which is why a [[subsection]] begins and ends where
every other trigger needs him to travel. It records whenever you play, with or
without a [[target]] set: the [[segment recorder]] exists to let you point at
what you just did, and you do the thing before you name it.

- **Lives** — the moment registry (`src/sm64_events/detectors/moment.py`)

### Caused moment

A [[moment]] the player caused without Mario's own action saying so — pressing
a blue coin switch, defeating a goomba or a bob-omb. The switch presses itself
by watching Mario's position, and an enemy records its defeat on itself, so
the [[detector]] reads the object's state out of the game's object list
instead of Mario's action. Every one still names its [[landmark]] — WHICH switch, WHICH
goomba — and speaks through the same sentence a door does. Which objects may
fire one, and the measured rule for each, is one registry row; a new object
kind earns its row with a capture from the pool probe, never from reading the
game's source alone.

- **Lives** — the caused-moment detector
  (`src/sm64_events/detectors/caused.py`), its registry
  (`src/sm64_events/memory/addresses.py`)
- **Not** — a health change. The game arms an object's hitbox when Mario
  comes near, which reads as a health write; a defeat is the object entering
  its own defeat action.

### Landmark

The specific thing in the world a [[moment]] happened to — that door, that
pole, that bob-omb — and that warp, since a warp inside a course leads nowhere
new and so has no destination to name it. The game rebuilds its object list every time an area
loads, so we name a landmark by where it SPAWNED rather than by which slot of
that list holds it: the slot changes and the spawn position does not. A thing the
game creates with no spawn position of its own — a pole, a tree — takes the place
it STANDS in instead, which never moves either, so one specific pole can carry
a name. Every
landmark also belongs to a KIND — the game's own script for what the thing
is — and we ship a name for every kind in the game, derived from the
decompilation's symbol table, so a row says "a bob-omb" without anyone
naming anything. He names one specific landmark in the [[recorder]] and
every row it ever appeared in takes that name; a name he types always beats
a shipped one. A recorded [[segment]] can also PIN one: a definition made by
pointing at "Open the CCM Door" matches that door and no other, instead of
"whichever door came first". Some one things wear several keys — the game
builds a [[star]]-count door from TWO halves, each with its own spawn position — so the
name is the collapse: keys of the same kind in the same course that carry
one name ARE one landmark, a rename or an erase moves all of them, and a
pinned [[segment]] fires on whichever half he pushes.

- **Lives** — `src/sm64_events/core/landmark.py` (the key),
  `tools/corpus_behaviors.py` (every kind, named),
  `src/sm64_events/data/defaults.seed.json` (the names we ship)
- **Not** — the count in a [[moment]]'s name. "The 5th door you opened" counts
  doors; a landmark IS the door, and the count is something it has.
- **Not** — the thing an [[entrance touch]] row renames. Mario collides with
  level geometry there, not with an object, so the row's landmark reading
  lingered from whatever he touched last — the entrance carries its own
  identity instead, made from where it stands and where it leads.

### Attempt

One try at a [[target]], from the [[anchor]] that opens it to the [[outcome]]
that closes it. Each attempt keeps its own time, its own [[strategy]] and its
own [[outcome]].

- **Lives** — the [[projector]] (`src/sm64_events/tracking/projection.py`)
  → one row in the [[practice log]]
- **Not** — a [[run]]. A [[run]] walks a whole [[route]]; an attempt is one try
  at one [[target]].

### Outcome

How an [[attempt]] ended: you finished it, you died, or you abandoned it by
leaving the area.

- **Lives** — the outcome registry (`src/sm64_events/tracking/views.py`)
  → the marker shapes on the [[progress graph]]

### Anchor

What opens an [[attempt]] — a practice [[reset]] or a state load. The
[[projector]] discards an anchor you never moved after, so an idle [[reset]]
never counts against your [[failure rate]]. The move the anchor INTERRUPTED
belongs to the [[attempt]] it ended, so the anchor holds that action back until
you leave it.

- **Lives** — the anchor detector (`src/sm64_events/detectors/anchors.py`)

### Failure rate

How often your [[attempt]]s at a [[target]] end in a [[death]] or an abandon
rather than a finish.

- **Lives** — the practice views (`src/sm64_events/tracking/views.py`)

### Personal best

Your fastest finished [[attempt]] at one [[target]] on one [[strategy]]. The
trainer keeps personal bests separately from the [[journal]], so clearing
history never erases one.

- **Lives** — the store (`src/sm64_events/storage/db.py`)
  → the PB tag on the [[practice log]]

### Strategy

A named way of doing a [[target]] — which path through the level, which tricks.
Two people playing the same [[star]] on different strategies are doing genuinely
different things, so each strategy carries its own [[ladder]].

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)
  → the [[strategy picker]]

### Route

An ordered plan of [[route step]]s covering a stretch of a full game — the
thing you practice toward, rather than the thing you practice.

- **Lives** — the route model (`src/sm64_events/tracking/routes.py`)
  → the Routes tab
- **Not** — a [[segment]]. A [[segment]] is one [[target]] you can practice; a
  route is an ordered plan made of many.

### Route step

One entry in a [[route]]: a group of candidate [[target]]s and how many of them
you need. "Any 3 of these 7 [[star]]s" is one step, so a [[route]] tolerates you
taking them in a different order.

- **Lives** — the route model (`src/sm64_events/tracking/routes.py`)

### Run

One timed walk through a [[route]], from the [[reset]] that starts the clock to
the last [[route step]] you complete. A run forgives retries: it counts wall
time per step and rolls repeated tries up into the step they belong to.

- **Lives** — the run tracker (`src/sm64_events/tracking/runs.py`)
  → the Run view
- **Not** — an [[attempt]]. An [[attempt]] is one try at one [[target]].

### Rank

The [[tier]] one time earns on one [[ladder]].

- **Lives** — the classifier (`src/sm64_events/ranks/classify.py`)
  → the [[rank icon]] on the [[practice log]]
- **Not** — a [[standard]]. A [[standard]] sets the threshold; a rank is what
  your time earns against it.

### Iron

The bottom [[tier]], and the only one carrying no threshold: no time can fall
short of it. Its bar therefore fills gradually rather than snapping, so a flat
bar means you have never tried this [[target]], never that you went slowly.

- **Lives** — the classifier (`src/sm64_events/ranks/classify.py`)

### Standard

The time one [[rank]] demands, for one [[target]] on one [[strategy]]. The
community publishes these; the trainer ships them and reconciles your file
against a newer edition without discarding [[strategy]] rows you wrote
yourself.

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)
  → the [[standards ladder]]

### Ladder

Every [[standard]] for one [[target]] on one [[strategy]], ordered from Mario
down to [[Iron]].

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)

### Best-possible ladder

The fastest [[standard]] at each [[rank]] across every [[strategy]] that has
one. Grading against it answers "how close is this to the fastest this
[[target]] can go", which mastering a slow [[strategy]] never moves.

- **Lives** — the scorer (`src/sm64_events/ranks/scoring.py`)

### Strategy rank

Your [[rank]] on the [[ladder]] of the [[strategy]] you are actually playing —
"how well do I play this strat".

- **Lives** — the scorer (`src/sm64_events/ranks/scoring.py`)
  → the left banner on the [[practice log]]

### Entity rank

Your [[rank]] on the [[best-possible ladder]] — "how close is this to the
fastest this [[target]] can go". When it agrees with your [[strategy rank]] the
[[practice log]] draws one banner carrying both names instead of two.

- **Lives** — the scorer (`src/sm64_events/ranks/scoring.py`)
  → the right banner on the [[practice log]]

### MARELO

Your one overall rating across everything you practice, rather than one
[[target]] at a time. A [[tier]] and a [[division]] inside it, both of which
your [[rank]]s earn.

- **Lives** — the MARELO bridge (`src/sm64_events/tracking/marelo.py`) and the
  scorer (`src/sm64_events/ranks/scoring.py`) → the MARELO pill
- **Not** — a [[rank]]. A [[rank]] grades ONE time on ONE [[ladder]]; MARELO
  grades your whole practice history at once.

### Tier

One of the nine named levels a time can reach, climbing from [[Iron]] through
Bronze, Silver, Gold, Platinum, Diamond, Master and Grandmaster to Mario. A
[[rank]] names one tier; [[MARELO]] names one tier plus a [[division]] inside
it.

- **Lives** — the tier names (`src/sm64_events/ranks/classify.py`)

### Division

One step inside a [[MARELO]] [[tier]], written as a roman numeral — the fine
half of the rating, and the thing that moves often enough to feel like
progress.

- **Lives** — the scorer (`src/sm64_events/ranks/scoring.py`)

### Scope

Which slice of your history a rating covers — everything, one course, or one
kind of [[target]]. Changing scope changes what [[MARELO]] describes without
changing any [[attempt]].

- **Lives** — the scopes (`src/sm64_events/ranks/scopes.py`)
  → the scope control on the [[Rank tab]]

### Caveat

A reason one saved time does not mean what the [[rank]] beside it implies —
you took it on an old clock, or under conditions the [[standard]] assumes
differently. The trainer keeps the time and marks it, rather than discarding it.

- **Lives** — the caveat rules (`src/sm64_events/tracking/caveats.py`)
  → the [[caveat mark]]

### In-game time

The clock Usamune itself keeps, in [[frame]]s, which the community grades
[[star]]s on. Every [[star]] time on screen comes from this clock, never from
our own [[frame]] counting.

- **Lives** — the IGT clock (`src/sm64_events/detectors/igt_clock.py`)
- **Not** — [[real time]]. [[Segment]]s grade on [[real time]]; [[star]]s grade
  on in-game time.

### Real time

Wall-clock elapsed time, which the community grades [[segment]]s on.

- **Lives** — the run tracker (`src/sm64_events/tracking/runs.py`)

### Frame

One tick of the game at 30 per second, and the unit every time in this project
starts as. One frame is three centiseconds on the [[in-game time]] display, so
a three-centisecond disagreement means exactly one frame.

- **Lives** — the time formatter (`src/sm64_events/core/timefmt.py`)

### Epoch

The stretch since Usamune's counter last restarted. Two readings from different
epochs describe different things, so the trainer works out WHY the counter
restarted before trusting any difference between them.

- **Lives** — the epoch reader (`src/sm64_events/detectors/counter_epoch.py`)

### X-cam

The camera cut Super Mario 64 plays when you touch a [[star]]. The community
times a grab there, so the trainer publishes a [[star]] time when the
x-cam starts rather than when the game finishes writing its own result.

- **Lives** — the star-grab detector
  (`src/sm64_events/detectors/star_grab.py`)

### Entrance touch

The [[frame]] Mario commits to the painting, portal, hole, pipe or cage that
leads into a course. The game loads that course 77 [[frame]]s later — 23 at a
pipe, 74 at Big Boo's Haunt's cage, whose entering jump is the commit — so a
[[segment]] measured to the load counts the fade as travelling. The trainer
ends a movement at the touch instead.

An entrance carries a name of its own: the [[recorder]]'s pencil on one of
these rows names THE ENTRANCE — keyed by where it stands and where it leads —
never the [[landmark]] Mario last touched on the way in, which is how naming
the CCM entrance once renamed the CCM door instead.

- **Lives** — the warp detector (`src/sm64_events/detectors/warp.py`);
  the entrance's own name key in `src/sm64_events/tracking/eventlabel.py`
- **Not** — the [[frame]] the course loads. A painting or portal records where
  it leads as Mario touches it, so the [[detector]] publishes on that same
  [[frame]]; only a pipe makes it wait, and a pipe the player pauses out of —
  the [[attempt]] ends at the touch, the fight unwanted — publishes as soon as
  the frozen countdown rules a ride out, not at the eight-second backstop.
- **Not** — a [[moment]]. A [[moment]] reads Mario's own action; an entrance
  touch reads what he collided with.

### Spawn point

WHICH entry placed Mario when he spawned — the warp node the game itself
routed the spawn through, read back off the same warp struct the
[[entrance touch]] watches. The pyramid's top and bottom entries are two
spawn points into one subarea, so a [[subsection]] recorded from one of
them starts only there; a spawn the struct cannot vouch for (a save state,
a stale write) carries none rather than a wrong one. A spawn into a
subarea reads "Spawned into Lethal Lava Land: Volcano" — the place, not
just the level.

- **Lives** — the spawn detector (`src/sm64_events/detectors/spawn.py`);
  the subarea names in `src/sm64_events/memory/addresses.py`
- **Not** — an [[anchor]]. An [[anchor]] says an [[attempt]] boundary
  happened; the spawn point says where the game put Mario when it did.

### Death

Mario dying, which closes the open [[attempt]] with a losing [[outcome]].

- **Lives** — the death detector (`src/sm64_events/detectors/death.py`)

### Reset

Usamune restarting the game or loading a save state. A reset closes whatever
[[attempt]] was open and may open the next one as an [[anchor]].

- **Lives** — the anchor detector (`src/sm64_events/detectors/anchors.py`)

### Session

One sitting of practice. Sessions group [[attempt]]s for the [[practice log]]
and for scoping a [[progress graph]]; you can reopen one and keep appending to
it. A sitting that recorded no [[attempt]] was not a session, so the trainer
forgets it at the next startup — its [[journal]] rows stay, because they can
still govern other sessions' [[attempt]]s.

- **Lives** — the tracker service (`src/sm64_events/tracking/service.py`)

### Exit star

A [[star]] you collect to leave a course rather than as the goal — which makes
its time comparable only with other [[attempt]]s that left the same way.

- **Lives** — the 100-coin rules
  (`src/sm64_events/tracking/hundred_coin.py`)

### 100-coin star

The [[star]] a course gives you for one hundred coins. Because you can finish
that collection anywhere, which [[exit star]] you ended on defines which
[[strategy]] the [[attempt]] belongs to.

- **Lives** — the 100-coin rules
  (`src/sm64_events/tracking/hundred_coin.py`)

### Topology

The map of which course you can reach from which, which the trainer judges a
[[segment]] move against. A move the map does not allow means the [[segment]]
did not happen.

- **Lives** — the world graph (`src/sm64_events/tracking/topology.py`)

---

## What is on screen

### Practice tab

The screen you keep open while you play: the [[selector]] and the
[[practice log]].

- **Lives** — the practice page
  (`src/sm64_events/ui/components/practice.js`)

### Rank tab

The screen for your standing rather than your current try — the [[MARELO
pill]], the [[scope]] control, and your [[rank]]s across everything.

- **Lives** — the rank page
  (`src/sm64_events/ui/components/rankpage.js`)

### Selector

The row of [[practice cell]]s across the top of the [[Practice tab]], showing
what you can practice where you currently stand — and no finer: standing
inside a subarea narrows the row to that subarea's own [[star]]s (inside
the volcano, the volcano's), while a subarea the trainer does not know
keeps every [[star]] rather than hiding one wrongly. Clicking a cell sets your
[[target]]. Picking something that owns [[subsection]]s EXPANDS the row into
that family — the parent and its [[subsection]]s, nothing else — and tapping the
parent again returns the full row; [[subsection]]s never appear before
you choose their parent.

- **Lives** — the quick-select row
  (`src/sm64_events/ui/components/stagebanner.js`), whose disclosure rule is
  its own module (`src/sm64_events/ui/subsections.js`)

### Segment recorder

The screen turning what you just played into a [[segment]]. It lists your recent
[[moment]]s newest first, keeps listing them while you keep playing, and asks
which [[target]] the result is a piece of — which is the only way to make a
[[subsection]]. Picking two [[moment]]s defines the ends and the [[journal]]
fills the middle from where you walked; picking more says which stops count.
Each row says when it happened on Usamune's clock, and arriving somewhere draws
ONE row that names the place — the load moves the area byte several times and
this screen shows you the arrival rather than the load. An arrival is the game
handing control back, so entering a course, warping through the Usamune menu
and resetting the level all draw one, and it reads 0'00"00 because that is
where Usamune starts counting. Going deeper — the pyramid, the volcano — draws
a move instead.

- **Lives** — the recording screen
  (`src/sm64_events/ui/components/segmenttimeline.js`) → the Segments tab
- **Not** — the [[recorder]], which captures video. This one captures nothing;
  it reads the [[journal]] you already wrote by playing.

### Re-record

The [[segment recorder]] pointed at a [[segment]] that already exists: play it
again, point at what you did, and Save replaces that [[segment]]'s recording
in place. The [[segment]] keeps its identity — same row, so every [[route]] that
references it, its PBs, each [[attempt]], each [[strategy]]'s [[ladder]] and
its library filing all stay attached; only the triggers move. The new
recording's own defaults win over the old row's stored modes (it times from
the move and saves Strict), and re-recording a shipped movement makes it
yours, exactly as editing one does. The door lives on the [[segment]]'s own
editor, where you notice you recorded it wrong.

- **Lives** — the segment editor's "Recorded wrong?" row
  (`src/sm64_events/ui/components/segments.js`) opening the recording screen
  (`src/sm64_events/ui/components/segmenttimeline.js`) with the row as its
  replace intent
- **Not** — delete-and-recreate, which would orphan every [[route step]] and
  PB referencing the old row.

### Practice cell

One clickable thing in the [[selector]] — its art, its [[rank icon]], its name
and its last [[strategy]]. The [[target picker]] draws this same cell, so what
you pick looks like what you get.

- **Lives** — the cell
  (`src/sm64_events/ui/components/practicecell.js`)

### Standards ladder

The table of every [[standard]] for one [[target]], each row linking to the
fastest video that earns that [[rank]].

- **Lives** — the standards panel
  (`src/sm64_events/ui/components/standards.js`)

### Rank icon

How one [[rank]] draws itself — as a cap or as a medal, whichever you chose.
The icon owns only the drawing; which [[rank]] it shows, and the name that
[[rank]] carries, live elsewhere.

- **Lives** — the icon registry
  (`src/sm64_events/ui/components/rankicon.js`)

### Caveat mark

The badge that says a saved time carries a [[caveat]]. One module owns the
wording for every surface, so the [[practice log]] and the [[practice cell]]
can never disagree about what a time means.

- **Lives** — the caveat vocabulary
  (`src/sm64_events/ui/components/marks.js`)

### MARELO pill

The bar carrying your [[MARELO]] [[tier]] and [[division]] at the top of the
[[Rank tab]].

- **Lives** — the MARELO header
  (`src/sm64_events/ui/components/marelo.js`)

### Practice log

The [[Practice tab]]'s history, newest first, EXCEPT the [[target]] you are
practicing right now, which leads regardless of its own recency: one card per
[[target]] you have earned one for (a recorded [[attempt]], or you still
stand where you set it), each carrying its own [[personal best]], its
[[strategy rank]] and [[entity rank]], its [[standards ladder]], its
[[caveat mark]] and its own [[attempt]]s. It highlights the card for whatever
you are practicing right now, and a Bowser course's Reds/Pipe pair never
shows both halves at once — only the one matching how you are currently
grading it.

- **Lives** — the log
  (`src/sm64_events/ui/components/practicelog.js`)

### Progress graph

The plot of your [[attempt]]s over one [[session]] or over your whole history,
with a marker shape per [[outcome]].

- **Lives** — the timeline
  (`src/sm64_events/ui/components/timeline.js`)

### Target picker

The dialog for choosing a [[target]] the [[selector]] does not offer, which
writes nothing until you also choose a [[strategy]] — so backing out of it
leaves your settings exactly as they were.

- **Lives** — the picker
  (`src/sm64_events/ui/components/targetpicker.js`)

### Strategy picker

The control for choosing which [[strategy]] your next [[attempt]] uses.

- **Lives** — the picker
  (`src/sm64_events/ui/components/stratpicker.js`)

### Level-up climb

The animation your [[MARELO]] bar plays when a [[division]] or a [[tier]]
changes — it travels from the old value to the new one rather than arriving at
it.

- **Lives** — the climb (`src/sm64_events/ui/rankclimb.js`)

### Celebration

Anything the screen does to mark you crossing a threshold. A celebration fires
only when something you just did caused it, never on a page load catching up on
history.

- **Lives** — the celebration surface
  (`src/sm64_events/ui/components/celebrate.js`)

### Tuning inspector

A live control panel for anything judged by feel — timings, easing, layout
weights. You tune it while it plays and press save, and what you saved becomes
what ships.

- **Lives** — the tuning page (`src/sm64_events/ui/tune.js`)

---

## What runs

### Poller

The loop reading the emulator sixty times a second: take a [[snapshot]], hand
consecutive pairs to every [[detector]], publish whatever they emit.

- **Lives** — the poll loop (`src/sm64_events/server/poller.py`)

### Snapshot

One coherent read of every piece of game state the [[detector]]s need, taken at
one instant so no two fields can straddle a change.

- **Lives** — the snapshot (`src/sm64_events/core/snapshot.py`)

### Detector

A function over consecutive [[snapshot]] pairs that emits an [[event]] when it
recognises something. Detectors may keep a little state of their own and must
heal themselves when the game clock jumps backward.

- **Lives** — the detector contract
  (`src/sm64_events/detectors/base.py`)

### Event

One thing the game did, which a [[detector]] recognised and the trainer
published exactly once.

- **Lives** — the event envelope (`src/sm64_events/core/events.py`)
- **Not** — an [[attempt]]. The [[projector]] derives one [[attempt]] from
  several events.

### Journal

The append-only record of every [[event]], in the order the game produced them.
It is the truth: every table the trainer shows comes from replaying it, so
nothing edits it in place.

- **Lives** — the store (`src/sm64_events/storage/db.py`)
- **Not** — the [[UI log]]. The journal says what the GAME did; the [[UI log]]
  says what the SCREEN showed.

### Projector

The module replaying the [[journal]] front to back to rebuild every
[[attempt]]. Replaying the same [[journal]] twice produces the same
[[attempt]]s, which is why the trainer never writes anything it derived back
into the [[journal]].

- **Lives** — the projector
  (`src/sm64_events/tracking/projection.py`)

### Store

The SQLite database holding the [[journal]] and every table the [[projector]]
rebuilds from it, plus the [[personal best]]s that outlive them.

- **Lives** — the database (`src/sm64_events/storage/db.py`)

### Broadcaster

The fan-out sending one [[event]] stream to every connected browser and window
at once.

- **Lives** — the broadcaster
  (`src/sm64_events/server/broadcaster.py`)

### UI log

The record of what the screen actually drew, written by the page itself and
stored beside the [[journal]]. It reads the rendered page rather than what we
believe we rendered, because that belief is what a live bug report puts in
doubt.

- **Lives** — the recorder (`src/sm64_events/core/uilog.py`) and the page
  reader (`src/sm64_events/ui/uilog.js`)

### Recorder

The capture holding the last stretch of play, so a [[star]] you just took can
become a clip without you having recorded anything deliberately.

- **Lives** — the recorder (`src/sm64_events/replay/recorder.py`)

### Instance lock

The guard letting one server own one [[store]]. A second server keeps
broadcasting but writes nothing, so two of them polling one emulator can never
record every [[event]] twice.

- **Lives** — the lock (`src/sm64_events/storage/instance_lock.py`)

### Debug report

One markdown file capturing what the trainer was doing when something went
wrong: capped tails of the server log, the [[journal]], the [[UI log]] and the
perf samples, plus the server's health readout and install facts. The Debug report
button in the settings drawer writes one and opens Explorer with it selected,
so a user can attach evidence to a bug report instead of a description of it.

- **Lives** — the report builder (`src/sm64_events/core/diagnostics.py`) → the
  settings drawer (`src/sm64_events/ui/components/header.js`)
