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

**A term that is also an ordinary English word this project says in another
sense gets QUALIFIED, not left bare.** `Entry` and `Piece` were minted for the
Ultimate Sheet's rows on one branch while another branch was writing "a course
entry" and "a piece of a star" in plain English all over its own rows; the
moment both merged, closure reported 12 rows using a defined term unmarked, and
neither reading was wrong. `Sheet entry` and `Sheet piece` say which half of the
app they belong to and leave the everyday words free (2026-08-09). Closure can
only find this AFTER a merge — it compares rows to rows, so two branches each
adding a coherent set is exactly the case it cannot warn about early.

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
something waits its turn. When the front completes it stays in the hand as
a [[leftover target]], so its [[ghost]] and its retries keep a [[target]]; when it
dies (forfeited or expired), the oldest detection still alive takes over,
and an empty queue leaves the hand neutral. A queued detection also takes
a [[leftover target]], which is the one hand nobody chose. A [[segment]] that
arms by mere presence — a pipe family on a course
entry — never enters the queue, and neither does a [[subsection]] a
[[star]] or another [[segment]] owns, whatever its clause shape: only your
click selects such a piece, and its detection speaks through its parent
instead. A piece of a castle area is not one of those — an area cannot hold
the [[target]], so its detection has nobody to speak through and queues on
its own account.

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
  → the [[selector]] and the [[practice log]]

### Hooked target

A [[target]] a detection set: the front of the [[target queue]]. The trainer
holds it until it completes, until you genuinely abandon it (an [[anchor]]
landing inside a foreign course), or until its own staleness budget expires
— walking back through a course to redo its start keeps the hold. A
[[star]] grab, or an automatic pick of a [[segment]] armed where you stand (the
Bowser arena's fight), takes the hand from a stale hooked one, never from
one the matcher still holds armed — except a hooked [[subsection]], which yields
to its parent [[star]]'s grab armed or not; a PICKED one holds even
through that grab, because explicit choices take priority.

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
  → the [[selector]]
- **Not** — a [[picked target]]. A click is sovereign; a hook is the
  trainer following your play.

### Leftover target

A finished [[segment]] still in the hand — one the trainer followed onto
after you finished it somewhere else, or one you just completed while
it was the [[hooked target]] — and no longer armed where you stand: neither a
[[picked target]] nor a [[hooked target]], so nothing bounds it. A detection
in the [[target queue]] displaces it: performing a [[segment]]'s own start
here beats a guess about a place you have walked out of.

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
  → the [[selector]]

### Picked target

A [[target]] you clicked. Nothing detected may steal it, a [[star]] grab may
not move it, choosing one empties the [[target queue]], and it retires only
by the standing rules (walking into a course it is not practiced in, with
its arm gone). A picked [[segment]] that finishes by entering a course (MIPS
Clip into Dire, Dire Docks) leaves the hand empty there, and comes back the
next time you arrive in the castle, unless you clicked something else
meanwhile, so the trainer holds it ready before you start it again.

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
its own rows in the [[practice log]], drawn INSIDE its parent's own card and
indented under it. The [[star]] owns the hand: a piece arms
and records underneath its parent without ever taking the [[target]] slot
by detection — its completion points the slot at the parent [[star]], never
at itself. It points at nothing at all when two or more [[star]]s or
[[segment]]s share it — LLL's volcano entry serves both volcano [[star]]s,
whether one definition names them both or two identical definitions each name
one — because finishing it proves you did one of them and never which. Such a
piece keeps recording, and shows nothing until the [[star]] it belongs to
earns a card of its own. It has no switch on the [[selector]] — a piece always tracks and
always draws its card as soon as its parent has one, even with nothing
recorded yet (empty, closed, never stealing the parent's auto-open slot) —
so its history, its [[ladder]] and the fact that it exists at all are
reachable from a fresh [[session]], not only after it first records something.
The only doors that turn a piece off are the ones that were always
separate: its own editor's enabled checkbox and its library row's hide/show
button. A [[star]] can be a subsection too, of a
[[segment]] rather than of another [[star]]: a Bowser course's reds [[star]]
already rides its Reds→Pipe [[segment]] as that movement's own waypoint, so
its section carries the movement as its parent and its card nests inside
the movement's, the same as any other piece — with one exception a
[[segment]] piece does not get: when its parent [[segment]] draws no card of
its own (disabled, or simply not armed anywhere the player has stood),
the [[star]] promotes to an ordinary top-level card instead of disappearing.
A [[star]] carried its own [[practice log]] entry and its own [[ladder]] long
before it could ever be a piece of anything, so hiding it as a side effect of
its parent's card going away would erase real history a [[segment]] piece —
which has never stood alone — has none of. Inside a course a piece always
belongs to something specific; a castle-side
piece belongs either to a [[segment]] recorded in its area or to the area
itself, which is as high as the castle goes. The [[recorder]]'s parent picker
offers both: a course tile opens its [[star]]s, an area tile opens the
[[segment]]s recorded there and leads that list with the area itself, and the
+ beside a chosen parent adds another.

- **Lives** — the trigger vocabulary and matcher
  (`src/sm64_events/tracking/segments.py`) → the [[selector]]
- **Not** — a [[route step]]. A [[route step]] names [[target]]s to complete in
  order; a subsection is one [[target]].

### Segment reach

Every place the trainer may show a [[segment]] in: where you can start it,
every place it names as one of its own steps, and everywhere on a shortest
walk between them. It answers "does this go through here", which
the course a [[segment]] belongs to cannot — the castle, the two hubs and the
three Bowser arenas all belong to no course, so a room every castle movement
walks through and a room no movement enters at all read as one place. The
trainer hides a card outside its [[segment]]'s reach and drops a
[[picked target]] that leaves one. A [[star]] has none: one grab walks no
[[topology]].

- **Lives** — the derivation (`src/sm64_events/tracking/segments.py`) and the
  world graph it walks (`src/sm64_events/tracking/topology.py`) → the
  [[practice log]]
- **Not** — where you may SET a [[target]], which is the one place a
  [[segment]] starts in and nowhere else along its reach.

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
is, named by its [[behaviour symbol]] so the same door keys the same on
every ROM — and we ship a name for every kind in the game, derived from the
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
  `src/sm64_events/memory/behaviours.py` (every kind, named),
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

How an [[attempt]] ended: you finished it, you [[reset]], you loaded a save
state (a [[segment]]'s [[attempt]] only; a [[star]]'s [[attempt]] continues
through the load), you died, or you abandoned it by leaving the area.

- **Lives** — the outcome registry (`src/sm64_events/tracking/views.py`)
  → the marker shapes on the [[progress graph]]

### Anchor

What opens an [[attempt]] — a practice [[reset]] or a [[save-state load]]. A
[[reset]] always ends the [[attempt]] before it and restarts the clock and the
[[ghost]], but records a row only if you played since the last one: an idle
[[reset]], one taken during a textbox or cutscene the game opened (Bowser's
arena speech), and the zero Usamune takes as you arrive somewhere have no row,
so none counts against your [[failure rate]]. The move the anchor INTERRUPTED
belongs to the [[attempt]] it ended, so the anchor holds that action back until
you leave it. Since 2026-09-23 a [[save-state load]] opens an [[attempt]] only
when none is open; otherwise the open one carries on through it.

- **Lives** — the anchor detector (`src/sm64_events/detectors/anchors.py`)

### Save-state load

Loading a saved state, caught by whichever clock gives it away: the game's own
[[frame]] counter going backward (Project64's savestate), or Usamune's timer
jumping to a value no play can reach (the state Usamune loads itself). The
[[practice log]] counts every load inside the [[attempt]] it happened in rather
than starting a new one, so one row covers one [[reset]] and every load after
it, and an [[attempt]] with a load in it is not a [[valid attempt]].

- **Lives** — the anchor detector (`src/sm64_events/detectors/anchors.py`)
  → the [[projector]] (`src/sm64_events/tracking/projection.py`)
- **Not** — a [[reset]]. A [[reset]] starts the [[attempt]] over; a load puts
  you back part-way through it.

### Valid attempt

An [[attempt]] whose time could stand as a real result. A [[star]] [[attempt]] is
valid when a full [[reset]] at the course's own start opened it and no
[[save-state load]] came before the finish; a [[segment]] [[attempt]] is valid when
no load came between its own start and its finish. The [[practice log]] marks
an [[attempt]] that is not valid with a [[caveat mark]] saying why, keeps its
Save as PB button, and never saves it as an [[automatic PB]]. An [[attempt]]
journaled before 2026-09-23 is not judged.

- **Lives** — the [[projector]] (`src/sm64_events/tracking/projection.py`)
  → the [[caveat mark]] and the Save as PB button in the [[practice log]]

### Subarea reset

A [[reset]] that lands inside a level's subarea — the volcano, the slide, the
pyramid — rather than at the course's start. A [[star]] [[attempt]] it opens
is not a [[valid attempt]]; a [[segment]] that starts in that subarea is,
because the [[reset]] landed where it starts.

- **Lives** — the [[projector]] (`src/sm64_events/tracking/projection.py`)

### Failure rate

How often your [[attempt]]s at a [[target]] end in a [[death]] or an abandon
rather than a finish.

- **Lives** — the practice views (`src/sm64_events/tracking/views.py`)

### Personal best

Your fastest finished [[attempt]] at one [[target]] on one [[strategy]] —
set by playing it here, or brought in as an [[imported time]]. The trainer
keeps personal bests separately from the [[journal]], so clearing history never
erases one, and it saves one only under the [[strategy]] you are practicing —
so every [[strategy]] holds its own, and the [[practice log]] shows whichever
[[strategy]] you have selected.

Choosing a [[strategy]] for unclassified practice history automatically saves
its fastest eligible success as GOLD. Correcting the newest [[attempt]]'s
[[strategy]] also saves it when it improves that [[strategy]]'s PB. [[Star]]s
use the displayed clock; [[segment]]s use RTA. Ties preserve the existing PB.
Ordinary Save also refuses slower times. Undo or hiding an incorrect saved
[[attempt]] permits correction. Keeping a [[ghost]] never requires lowering a PB.

- **Lives** — the store (`src/sm64_events/storage/db.py`)
  → the PB tag on the [[practice log]], and the gold GOLD!! row that marks
  the [[attempt]] itself (`src/sm64_events/ui/components/attemptlog.js`)

### Automatic PB

A [[personal best]] the trainer saves by itself, as soon as a
[[valid attempt]] on the [[active strategy]] beats that [[strategy]]'s best on
the clock its [[ladder]] grades — [[in-game time]] for a [[star]],
[[real time]] for a [[segment]]. It goes through the same door as the Save as PB button, so the
[[PB ghost]] and the replay follow it and Undo works as usual. With no
[[strategy]] selected nothing saves by itself.

- **Lives** — the tracker service (`src/sm64_events/tracking/service.py`)

### Time Save

How much a new [[personal best]] beat the one it replaced, printed beside its
GOLD!! in the [[practice log]] in seconds and in [[frame]]s. It stays until you
log your next [[attempt]] of any kind, then fades out and leaves GOLD!! alone.
The first save on a [[strategy]] replaced nothing, so it has none.

- **Lives** — the practice log's PB news (`src/sm64_events/ui/pbcrown.js`)
  → the PB row (`src/sm64_events/ui/components/attemptlog.js`)

### Imported time

An [[attempt]] the trainer never watched you make, because you set the time
before this tool existed. It is a real row in the [[practice log]] — stamped
with the time you pressed Save, holding the [[personal best]] it brought — so you
can clear it, undo it, or move it to another [[strategy]] exactly as you would
a played one. Its public recording link opens the provider's player; clicking
Download prepares a reusable local copy. Changing or removing
the link affects only this [[attempt]] and survives rebuilding the [[journal]].

It remembers the source that brought it — you typed it, or you named your
column in the [[Ultimate Sheet]] — and the
[[game version]]
that set it, so a JP time grades on the JP [[ladder]] while you play US. The
trainer draws neither fact: nothing marks an imported time on any screen.

An import lands a time only when it beats the [[personal best]] you already
hold for that [[target]] and [[strategy]], so importing twice changes nothing
and grows no second row. Every source names back what it could not read,
rather than importing what it understood and staying quiet about the rest.

- **Lives** — the import rule (`src/sm64_events/tracking/importing.py`)
  → the projector (`src/sm64_events/tracking/projection.py`)
  → the store (`src/sm64_events/storage/db.py`)

### Held time

A time from an [[Ultimate Sheet]] column that an import kept aside because
the trainer had nowhere to put it: a [[sheet piece]] you have not linked to
a [[segment]], a castle-movement [[approach]] with no entity, a whole-stage
RTA row, or a [[star]] row the sheet times on a real-time clock. It is not
an [[attempt]] and grades nothing. It shows on its row in the
[[Library tab]], the [[column export]] prints it back exactly as the sheet
had it, undoing the import erases it, and an [[adoption]] of its row lands
it on that [[segment]] through the ordinary import rule as soon as you make
the link — so the trainer loses nothing a [[runner]] wrote down, and invents
nothing to hold it.

- **Lives** — the hold (`src/sm64_events/storage/db.py`)
  → the lander (`src/sm64_events/server/import_api.py`)
  → the link strip on the [[Library tab]]'s target page

### Platform stamp

Which machine set a time: `emu` (Project64's memory) or `n64` (a real
console's picture). Every [[attempt]] carries one, read off the [[event]] that
closed it, and a [[personal best]] remembers its platform through the
[[attempt]] you saved it from. An absent stamp means `emu`: until the console
front-end existed, nothing but the emulator could close an [[attempt]], and
one rule in the mode registry says so. An [[imported time]] takes its stamp
from the [[sheet legend]] of the column it came from, and the [[column
export]] paints each cell in its [[sheet colours]] by the stamp of the
[[personal best]] it prints.

- **Lives** — the mode registry (`src/sm64_events/core/modes.py`)
  → the [[practice log]] and the sheet export

### Sheet legend

The two cells a [[runner]] may write into rows 2 and 3 of their own
[[Ultimate Sheet]] column — "Emu" in one fill and "N64" in another — so that
every time below, wearing one of the two fills, says which machine set it.
Raisn's convention, not the sheet's: on 2026-09-04 his was the only column
with one. The sheet import reads a [[platform stamp]] off a timed cell only
where the column's own legend names both platforms in two different fills,
matching the cell to the nearest legend fill within a small distance; a
column with no legend stamps nothing.

- **Lives** — the sheet reader (`src/sm64_events/library/sheet.py`)
  → the library payload's entries → the sheet import

### Sheet colours

The colours your pasted [[column export]] wears: the fill an emulator time
wears, the fill a console time wears, the text colour, and the font family
— one stored preference, tuned on the [[Rank tab]] beside the copy button:
two legend cells reading EMU and N64 in their fills, three circle swatches,
and a font dropdown that draws every name in its own face. The pasted column
opens with those two legend cells (the sheet's own convention, in rows 2 and
3), each timed cell wears the fill of its [[platform stamp]], and an empty
cell wears nothing. The defaults are a blue and an orange darker than
Raisn's, white text, and Trebuchet MS, and they are only a starting point.

- **Lives** — the stored preference and its door (`src/sm64_events/server/scorecard_api.py`)
  → the controls on the exports row (`src/sm64_events/ui/components/sheetstyle.js`)
  → the clipboard's HTML half (`src/sm64_events/ui/components/scorecard.js`)

### Strategy

A named way of doing a [[target]] — which path through the level, which tricks.
Two people playing the same [[star]] on different strategies are doing genuinely
different things, so each strategy carries its own [[ladder]]. Every list of
strategies follows one order, the column order of the table of [[standard]]s:
the slowest [[ladder]] first, the way you climb (his rule, 2026-10-17).

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)
  → the [[strategy picker]]; the order: `src/sm64_events/ranks/ladder_order.py`
  and its browser twin `src/sm64_events/ui/ladderorder.js`

### Active strategy

The [[strategy]] you have selected for one [[target]] — the one the
[[practice log]] shows, the one a [[personal best]] saves under, and the one
the quick-select medal grades. One resolver answers it for every surface and
for the PB commands, so the card and the API cannot disagree about it.
Picking one also claims every [[attempt]] of that [[target]] that has no
[[strategy]] yet, except a row you set to no [[strategy]] yourself.

- **Lives** — the resolver (`src/sm64_events/tracking/activestrat.py`)
  → the [[strategy picker]]

### PB gate

Why a [[practice log]] row offers only a disabled Save: the row belongs to a
[[strategy]] other than the [[active strategy]], or you have selected no
[[strategy]] at all. The row keeps a disabled Save whose tooltip says how to
bring it back, so every row draws the same buttons, but shows no time
comparison, since nothing of the [[active strategy]]'s applies to it. The
server refuses the save for the same stated reason.

- **Not** — a [[caveat mark]]. A [[caveat mark]] says the time is not the
  quantity it looks like and draws a mark; a gated row is fine and simply
  not yours to act on right now.
- **Lives** — the resolver (`src/sm64_events/tracking/pbaction.py`)
  → the disabled Save in the [[practice log]]'s action column

### Route

An ordered plan of [[route step]]s covering a stretch of a full game — the
thing you practice toward, rather than the thing you practice.

- **Lives** — the route model (`src/sm64_events/tracking/routes.py`)
  → the Routes tab
- **Not** — a [[segment]]. A [[segment]] is one [[target]] you can practice; a
  route is an ordered plan made of many.

### Route step

One item in a [[route]]: a group of candidate [[target]]s and how many of them
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
yourself. Where the community published none, the trainer fits one from the
[[Ultimate Sheet]]'s own times for every [[strategy]] the sheet has enough
rows for — the [[star]]'s own row as its Standard [[strategy]] — and re-fits
them whenever it pulls the sheet: an import, a [[column export]], a refresh,
or the app starting. A published standard always wins over a fitted one.

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)
  → the [[standards ladder]]

### Ladder

Every [[standard]] for one [[target]] on one [[strategy]], ordered from Mario
down to [[Iron]].

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)

### Best-possible ladder

The legacy comparison taking the fastest [[standard]] at each [[rank]] across
a [[target]]'s [[strategy]] [[ladder]]s. Generated [[entity rank]] uses the
independent [[Overall curve]].

- **Lives** — the legacy scorer (`src/sm64_events/ranks/scoring.py`)

### Overall curve

The score for an absolute time on one [[target]], derived from compatible
[[sheet entry]] performances and families of [[strategy]] choices. Full nodes
determine its [[rank]] and [[division]]; eight displayed [[standard]] values
cannot reconstruct it. Changing a [[strategy]]'s [[standard]] does not change it.

- **Lives** — `src/sm64_events/ranks/overall.py` and
  `src/sm64_events/ranks/curves.py` → the Overall Rank Standards section
- **Contract** — [living rank calibration](ranking-calibration.md)

### Rank calibration

One published set of [[sheet entry]] observations, local [[target]] mappings,
[[strategy]] [[standard]] values and [[Overall curve]] values. Readers retain that set
throughout a response while refresh prepares the next one.

- **Lives** — `src/sm64_events/ranks/calibration.py` and
  `src/sm64_events/library/calibration.py`

### Strategy rank

Your [[rank]] on the [[ladder]] of the [[strategy]] you are actually playing —
"how well do I play this strat".

- **Lives** — the scorer (`src/sm64_events/ranks/scoring.py`)
  → the left banner on the [[practice log]]

### Entity rank

Your [[rank]] on the independent [[Overall curve]] for the actual [[target]],
clock, and ROM of the time. It can differ from your [[strategy rank]].

- **Lives** — `src/sm64_events/ranks/curves.py`
  → the Overall banner on the [[practice log]]

### MARELO

Your one overall rating across everything you practice, rather than one
[[target]] at a time. A [[tier]] and a [[division]] inside it, both of which
your [[rank]]s earn.

- **Lives** — the MARELO bridge (`src/sm64_events/tracking/marelo.py`) and the
  aggregator (`src/sm64_events/ranks/scopes.py`) → the MARELO pill
- **Not** — a [[rank]]. A [[rank]] grades ONE time on ONE [[ladder]]; MARELO
  grades your whole practice history at once.

### Tier

One of the nine named levels a time can reach, climbing from [[Iron]] through
Bronze, Silver, Gold, Platinum, Diamond, Master and Grandmaster to Mario. A
[[rank]] names one tier; [[MARELO]] names one tier plus a [[division]] inside
it.

- **Lives** — the tier names (`src/sm64_events/ranks/classify.py`)

### Division

One step inside a [[tier]], written as a roman numeral — the fine half of a
[[MARELO]] rating, and since round 1 of the [[Library tab]] also how that
page files every community [[sheet entry]] ("Wario 3"), so a reader can tell the
top of a [[tier]] from its bottom at a glance.

- **Lives** — the scorer (`src/sm64_events/ranks/scoring.py`), with a
  pinned twin for the [[Library tab]]
  (`src/sm64_events/ui/components/librarymodel.js`,
  compared implementation-to-implementation by
  `tests/test_cross_language_parity.py`)

### Goal

What the [[Scorecard]] compares with your [[personal best]], separately for
each entry. **[[Rank]] Goal** is always active: either one selected [[division]],
or Automatic, which follows the current [[scope]]'s [[MARELO]] [[rank]] plus
one subdivision, capped at Mario 1. Its time comes from that entry's canonical
[[Library tab]] [[strategy]] [[standard]]s, including the appropriate 100-coin [[route]].
A historical [[strategy]] alias can identify a Library row but cannot replace its
[[standard]]s with another [[strategy]]'s faster [[ladder]].

`Player Goal` independently selects any number of [[runner]]s, none by
default. `Custom Goals` selects named sets of times explicitly added for
[[star]]s or [[segment]]s. Edit goal times, name the set, then select or remove that
whole set together. Saving more entries under the same name extends the set
across [[scope]]s; changing an existing entry replaces that entry's time.

Each tile uses the fastest offer among its [[rank]], selected players and custom
sets. A [[runner]]'s offer is their best across all [[strategy]] choices and selected regions.
Manual choices and saved sets persist across [[scope]]s. Automatic continues to
adapt alongside those choices, and selecting it only changes **[[Rank]] Goal**.
[[Rank]] alone needs no legend. With players or custom sets selected, pills and
colored dots identify the winning source; the [[rank]] pill has no remove button.

Automatic is also floored per entry: an entry's goal is the harder of the
[[scope]]'s Automatic goal and one subdivision above your own [[rank]] on that
entry's [[ladder]] (capped at its top), so it never shows a goal you already hold.

A **pinned** goal is one threshold set on a single [[star]] or [[segment]] from its
Practice card: a [[division]], a [[runner]]'s time, or a typed time. It replaces
that entry's fastest-offer choice everywhere, including its [[Scorecard]] tile,
until cleared. A pin may repeat: `10x` asks for ten finishes under it, `10x
streak` for ten in a row, counted from the current [[session]]'s [[attempt]]s
since you set the pin, so each sitting starts again. A [[reset]] in the first
3 s neither counts nor breaks a streak, and you can forgive an [[attempt]]
that broke one.

Capless 4 through 1 have finite goal times; Capless 5 is the unbounded floor
and is not a selectable goal.

- **Lives** — `src/sm64_events/server/scorecard_standards.py` resolves the entry identity;
  `src/sm64_events/server/scorecard_goals.py` owns saved selection/set semantics;
  `src/sm64_events/server/scorecard_resolve.py` resolves every entry's goal (pins,
  the Automatic floor); `src/sm64_events/server/scorecard_api.py` serves them; `src/sm64_events/ui/components/scorecard.js`
  presents the controls and inline editor.

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

### Input frame

What the controller was doing on one [[frame]], and what Mario was doing while
you did it — every button held, how far the stick leaned, the action he was in,
which way he faced and how fast he was going. The game rewrites the pad about
two thirds of the way through each [[frame]], so the trainer reads it far
faster than the game itself ticks and keeps the LAST reading of each one; his
own state rides in that same reading, so the two can never describe different
[[frame]]s.

- **Lives** — the input decoder (`src/sm64_events/inputs/frame.py`)

### Input track

Every [[input frame]] of one [[attempt]], from its [[anchor]] to its
[[outcome]] — what you did, at the [[frame]] you did it. The trainer numbers a
track from its own start rather than from the [[session]]'s, so two tracks lie
on one axis whatever hour of whatever day you played each of them; a [[reset]]
in the middle of a track continues that axis rather than restarting it, and a
[[frame]] the trainer did not capture stays a hole on it. Missing first or
last samples preserve the [[attempt]]'s full extent, so they cannot move [[frame]] zero
or shift a saved template. Repeated raw counters alone cannot identify which
capture an [[attempt]] belongs to.

- **Lives** — the input store (`src/sm64_events/inputs/store.py`) resolves
  the frames; the run derivation (`src/sm64_events/inputs/runs.py`) lays them
  on the axis

### Input timeline

The surface that draws an [[input track]] as one lane per button over
[[frame]]s, with the [[template track]] behind it, and every [[moment]] the
[[journal]] holds inside the [[attempt]] as a tick on the same axis, wearing
the same sentence the [[segment recorder]] gives it. It reports what each
track was doing on a [[frame]] and names nothing as the reason — you read
the gap yourself. It opens inside an [[attempt]]'s own row, beside that
[[attempt]]'s clip, and follows the clip's clock rather than keeping one.
It draws the [[lead-in]] before its own [[frame]] 0, so what you did on the
way in is visible without moving where the [[attempt]] starts. On a clip the
[[capture layer]] recorded, the time it prints for a [[frame]] is the timer
the game itself held when it drew the picture on screen; on any other clip
it counts from the [[attempt]]'s first [[frame]].

- **Lives** — the timeline component
  (`src/sm64_events/ui/components/inputtimeline.js`)

### Lead-in

The stretch of play the [[input timeline]] draws BEFORE the [[attempt]]
itself: the lead its clip carries, from the clip's first [[frame]] to the
[[frame]] your [[reset]] landed on. You are in the level for those
[[frame]]s and your camera answers, so a timeline that began at the
[[reset]] hid real input. They number backwards from zero and sit behind a
shaded band, so the [[attempt]]'s own start and its own length — the number
the [[ladder]] grades its [[personal best]] against — do not move. The
timeline spans exactly what its clip shows -- the [[frame map]] names
every [[frame]] the footage holds -- so every part of it points at video
you can watch; with no clip there is no lead-in.

- **Lives** — the track resolver
  (`src/sm64_events/inputs/track.py`), which reaches back to the entry the
  [[journal]] recorded
- **Not** — part of the [[input track]] a [[template track]] or an export
  carries. Those stay exactly the [[attempt]].

### Frame map

The list a clip carries naming which [[frame]] of the game each picture of
that clip shows. The [[capture layer]] stamps every picture with the
[[frame]] the game was drawing when it made that picture, the [[feed log]]
says which stamped picture each encoded picture is, and the map is those
stamps -- so a [[frame]] the emulator held on screen twice maps twice and
one it skipped never maps, which no single offset can describe. Nothing
aligns, joins or infers: the [[oracle reader]] certifies the result picture
by picture, and a clip whose pictures carry no stamp gets NO map and says
so on the [[input timeline]] rather than showing a guess. The
[[input timeline]] and every [[overlay layer]] read the clip through its
map.

- **Lives** -- the clip's own metadata, written at extraction
  (`src/sm64_events/replay/service.py`) from the [[feed log]] join
  (`src/sm64_events/replay/feedmap.py`)
- **Not** -- the [[input track]]'s own axis. The track says what you DID on
  each [[frame]]; the map says which [[frame]] the footage SHOWS.

### Picture feed

The way the [[recorder]] hands pictures to the encoder since 2026-09-02:
one encoded picture per distinct picture the [[picture ledger]] noticed,
written as soon as it arrives and stamped with the picture's own
composition time, with the game's audio in the same stream on the same
clock. A clip cut from it holds every picture once, at its own time, so
the trainer reads which [[frame]] a picture shows off the [[feed log]]
rather than inferring it from how long the picture stayed on a 60 Hz
grid -- the duplicates and skips that no offset could describe are gone
because the feed duplicates nothing. A picture that stays on screen
feeds nothing; after a second of silence the feed writes the last one
again so the recording keeps rolling. His approved trade-off (item 38):
a clip's picture rate follows the game's pictures instead of a fixed 60.

- **Lives** -- `src/sm64_events/replay/ffmpeg_sink.py` (the feed itself:
  the NUT stream, the heartbeat, the audio on the same clock); the
  [[recorder]] decides what feeds (`src/sm64_events/replay/recorder.py`);
  `src/sm64_events/replay/config.py` switches it (picture_feed)
- **Not** -- the earlier feed, which re-sent the latest grab sixty times a
  second onto a wall-clock grid and stamped it at the encoder's read;
  that shape still serves clips cut before it and the in-process fallback.

### Polled fill

What the input lanes draw for a game [[frame]] that has no picture when exact
capture is on: the independently polled pad sample for that [[frame]], hatched
(bars) or dashed (stick and speed curves) and labelled "polled, no picture".
Every [[frame]] that has a picture draws the picture's own [[picture ledger]]
stamp instead, so the lanes and the inspector cannot disagree on one paused
picture. Without exact capture the lanes draw the polled track and mark
nothing. A [[refused picture]] therefore shows as a polled fill, never as the
picture's own state.

- **Lives** -- `src/sm64_events/ui/components/inputtimelinemodel.js` (the
  merge of stamps and polled samples),
  `src/sm64_events/ui/components/inputtimelinelanes.js` (the drawing).
- **Not** -- the template's own bars, which are another player's [[run]].

### Refused picture

A picture the [[capture layer]] could not admit while recording: the [[renderer]]'s
eight source slots or its GPU copy pool were full (Python fell behind), or
the record carried no usable stamp. The [[renderer]] never waits for it; the
worker counts it (`refused` in the native `gpu_summary` log) and keeps
recording. The replay holds the previous picture through that [[frame]], the
lanes show its [[polled fill]], and the [[recorder]] files the next captured
picture inexact. Before round 48 one refusal ended the recording [[run]].

- **Lives** -- `plugin/gfxwrap/gpu_delivery.cpp` (the count),
  `plugin/gfxwrap/stamp_adapter.cpp` (the refusal latch).
- **Not** -- a same-origin or no-swap omission, which is a VI that showed
  no new picture and is not counted.

### Media run

One encoder child's identity and clock origin. The first submitted picture
starts the clock; video timestamps are integer ticks at 90,000 ticks per
second. A restart creates a new identity even when it keeps the same [[frame]] size.
[[Segment]]s retain that identity so extraction cannot join unrelated clocks.
The cut preserves its integer source offset and can recover each encoded
picture's original timestamp without fitting a cadence.

- **Lives** -- `src/sm64_events/replay/media.py`, shared by the sink,
  [[segment]] ring, extractor and feed-log lookup.

### Feed log

The [[picture ledger]]'s record of each picture accepted by the encoder:
its [[media run]], assigned integer timestamp and original captured-row time.
A heartbeat within that [[media run]] retains the held picture's row identity. The assigned
media time stays separate from capture time, including when a delayed picture
arrives after a heartbeat. Extraction joins exact [[media run]]/timestamp keys; collisions
and missing keys remain unknown rather than selecting a nearby picture.

- **Lives** -- `src/sm64_events/replay/ledger.py` (the log),
  `src/sm64_events/replay/feedmap.py` (the lookup),
  `src/sm64_events/replay/service.py` (clip metadata and state occurrences).
- **Not** -- the [[picture ledger]]'s rows. A row is a picture capture saw;
  a log entry is an accepted encoder write, including heartbeat copies.

### Picture ledger

The record the [[recorder]] keeps of every distinct picture it grabs from
the emulator's window: when the picture appeared, the [[frame]] the game
was computing, and every extra fact a wiring line registers. His spec:
capture holds all the information, so each picture of the video carries it
for any future analysis. At extraction the trainer matches the clip's
pictures to these rows through exact [[feed log]] keys. The matched row
occurrence survives into the [[frame map]] and per-picture timer (the [[picture feed]]
goes further: it writes one encoded picture per row and files each write
in the ledger's [[feed log]]) -- and the clip's own
metadata keeps its slice of the rows after the recording buffer forgets
the footage. Registering one more per-picture fact takes one line.
A row stamped by the [[capture layer]] holds the pad (`pad`), what Mario was
doing (`mario`: action, facing, speed) and, on pictures recorded from
2026-09-19, where he stood (`mario_pos`: x, y, z in the game's world units,
two decimals). A row leaves out any fact that was not captured or did not
read as a real game state, so an older clip has no `mario_pos` and still
loads unchanged.

- **Lives** -- `src/sm64_events/replay/ledger.py`; the [[recorder]] feeds it
  (`src/sm64_events/replay/recorder.py`) and extraction reads it back
  (`src/sm64_events/replay/service.py`)
- **Not** -- the [[input track]]. The track records what you PRESSED, 250
  reads a second; the ledger records what the screen SHOWED, one row per
  picture.

### Clip start

The time of a clip's first picture, in the clip's own clock. A cut
lands on a picture boundary of the ring, never on a whole 60 Hz slot,
so the first picture of a clip sits up to one slot after zero (his
Log Rolling clip: 0.011 s). Every slot arithmetic in the [[input
timeline]] counts from it: the time it seeks to for a slot, and the
slot it reads back from the picture the browser presents. Assumed zero
until 2026-09-01, which put every seek one picture early on such a clip
while the [[frame map]] and the [[pad reader]] were right. A [[picture
feed]] clip carries every picture's own time instead, and the clip start
is the first of them.

- **Lives** -- `src/sm64_events/replay/extract.py` measures it off the cut
  (video_start_of), `src/sm64_events/replay/service.py` carries it in
  the view as video_start_s (and measures it once for a clip cut before
  it existed), `src/sm64_events/ui/frame.js` counts from it
  (slotAtTime, timeOfSlot); `tools/probe_clip_seek.py` proves the
  browser lands where the timeline asked.

### Recording codec

Which video codec the [[recorder]] writes while it records, decided per
graphics card at the start of each recording. The trainer asks the card for
AV1 first, because AV1 holds the same picture in about a third of the bytes
and encodes faster than the H.264 it used to write, so a replay saved from an
AV1 recording is already small and never needs a [[compressed replay]] pass at
all. Only newer cards have an AV1 encoder; a card without one refuses by
codec -- a typed answer, distinct from anything going wrong -- and the trainer
records H.264 and shrinks the save afterwards exactly as before. The trainer
puts the question to the card through the same encoder that will do the
recording, then remembers the answer, so it costs one extra request per card
per launch.

- **Lives** -- `src/sm64_events/replay/gpusettings.py` (the choice and the
  memory of it), `plugin/gfxwrap/gpu_bridge_encoder.cpp` (the card's own
  answer), `src/sm64_events/replay/packetmux.py` (the archive carries whichever
  codec came back)
- **Not** -- the archive stage in `src/sm64_events/replay/config.py`, which is
  the codec a [[compressed replay]] is re-encoded INTO after the fact.

### Compressed replay

A saved replay whose video the trainer re-encoded smaller in the background
after Save/PB published it, under the same filename. The [[frame map]], the
picture states and the [[input timeline]] index a clip's pictures by
position and by each picture's own time, and none of them reads the video,
so the trainer swaps the file only once it has proven the smaller one holds
the same pictures, in the same order, on the same ticks, for the same
length, under identical decoded audio, and looks like the original. A proof
that fails keeps the original and says why. Saving stays instant: the swap
happens later, when no player has asked for the clip lately, and otherwise
when the app closes, so the folder never keeps both files once the app has
closed. When he closes the app before a job finishes, the next launch
finishes it. Working files live in one hidden folder at the top of the save tree,
never beside the replay. The trainer never rewrites a replay saved before
this existed unless asked, and never encodes a compressed one a second time.
A replay whose [[recording codec]] was already AV1 comes out small: the
trainer reads the codec, answers "nothing to do" without encoding anything,
and never asks about that replay again.

- **Lives** -- `src/sm64_events/replay/compress.py`; the quality rows are
  the archive stage in `src/sm64_events/replay/config.py`
- **Not** -- a second copy. There is one file per saved replay, and the ring
  and the clip the drawer shows straight after an [[attempt]] stay as they were.

### Media fingerprint

What a copy of a saved replay must reproduce to be the same replay: the
file's sha256, the picture count with a digest of every picture's tick, a
digest of the decoded audio, and where the last picture ends. The trainer
writes it into the sidecar's `media` block when it adopts a [[compressed
replay]], beside the original's own fingerprint, and it is what an uploader
or downloader checks to say a shared replay arrived exactly as it left.

- **Lives** -- `src/sm64_events/replay/compress.py` (fingerprint, prove)

### Close warning

The modal the page shows when he closes the app while the trainer is still
making a [[compressed replay]]: which replays are in flight, what is
happening to each in plain words, one moving bar apiece, and Exit anyway /
Wait / Keep using the app. Its copy is a contract: the replays are already
saved and exiting only leaves some of them full size until the next launch,
so Exit never wears a destructive style. The desktop shell asks the page to
show it, and closes instead whenever it cannot be sure a page is there to
answer. The recording panel shows the same list at any time.

- **Lives** -- `src/sm64_events/ui/components/closewarning.js` (the modal),
  `src/sm64_events/ui/components/compressionjobs.js` (the one list),
  `src/sm64_events/desktop/closeguard.py` (when to ask),
  `src/sm64_events/server/replay_api.py` (the list it polls)

### Pad reader

The reader that reads Usamune's input display out of every picture of a
clip. The game paints the pad into each picture as six glyph cells -- a
direction letter and up to two digits on the up-down line, the same on the
left-right line -- and paints each glyph identically every time, so a cell
either matches one known glyph by a clear margin or the reader calls it
unknown, never a guess.

It was part of extraction until 2026-09-05, pinning the [[frame map]] to
what it read. The [[capture layer]] made that unnecessary and the reader's
own accuracy made it unwanted: it read 82% to 94% of the pictures of three
clips the [[oracle reader]] certified exact, every sampled disagreement a
digit confusion off compressed video. What checks a clip now is the
[[pad stamp audit]], which reads no pixels at all. The reader stays as an
offline instrument, and the [[oracle reader]] borrows its glyph machinery.

- **Lives** -- `src/sm64_events/replay/padread.py`;
  `tools/score_pad_read.py` prints its verdict for one clip and
  `tools/score_picture_offset.py` sweeps which picture shows which pad
- **Not** -- the [[pad stamp audit]]: that compares two numbers the trainer
  already holds, where this one recognises glyphs in a picture.

### Pad stamp audit

The check a clip carries saying whether the [[input timeline]] holds the
same pad the game held. The [[capture layer]] copies the pad out of the
game's memory beside every picture it stamps, so the trainer ends up with
two independent records of one [[frame]]'s pad -- the plugin's copy and the
[[input track]]'s -- and the audit compares them picture by picture. It
reads no pixels, cannot misread, and costs nothing. It is SILENT when it
passes: it has never failed, so saying so on screen states a fact about
our own plumbing rather than about the [[attempt]] on screen, and only a
DISAGREEMENT draws -- a chip on the [[input timeline]]'s header opening the
list of contradicted pictures.

- **Lives** -- `src/sm64_events/replay/service.py`; the clip's own metadata
  keeps the verdict whether or not anything draws it
- **Not** -- the [[oracle reader]]: that certifies WHICH [[frame]] a
  picture shows, where this one certifies the PAD on the [[frame]] the map
  already names.

### Capture layer

The graphics plugin pair the trainer installs into Project64 (2026-09-04,
his ask: "redo our recording system so that the recording [[frame]]s ARE game
[[frame]]s"): the wrapper Project64 selects (`SM64 Trainer v1.0`,
`sm64_trainer_gfx.dll`) and the [[renderer]] it wraps. When the [[renderer]]
finishes a picture the wrapper copies the game's own memory -- the [[frame]]
counter, the pad, Mario, the running IGT -- and the [[renderer]] takes an owned
GPU copy of the picture with that immutable stamp, then samples it and hands the
selected ones to the isolated [[encoder helper]], all on its own thread
([[renderer-thread capture]]). The [[recorder]] retains exact source identity
through shared media fragments without transferring full images through
Python. A known GPU producer that cannot capture reports the failure; it does
not silently switch to desktop timing. The one exception is an [[unsupported
adapter]], which can never record this way: the [[recorder]] keeps the desktop
grab for it and says why. See [the current
runtime](replay-gpu-runtime.md) for ownership and limits.

The [[frame map]] reads captured identity rather than guessing it from cadence.
Inexact or missing identity stays unknown; the [[oracle reader]] and [[pad reader]]
independently audit real pixels. The trainer installs under explicit consent on
the [[setup screen]] (both DLLs into Project64's Plugin folder, an ini pointing
the wrapper at the [[renderer]], the `Graphics Dll` setting) and undoes it from the
same screen, selecting the plugin you had before; without it the [[recorder]]
photographs the desktop as before. The [[setup screen]] decides "current" by the
build id each DLL carries (bytes only for a file without one), so a rebuild
of unchanged sources never asks for a reinstall. An app update refreshes the installed pair by
itself once you close Project64, under the consent already given; onboarding
never reopens for it (his rule, 2026-09-16).

- **Lives** -- `plugin/gfxwrap/gfxwrap.c` (the wrapper), built by
  `tools/build_plugin.py` (through `tools/build_natives.py`, never committed)
  into the git-ignored bundle folder (its BUNDLE constant in `tools/build_natives.py`) beside the
  [[renderer]] and the [[encoder helper]]; `src/sm64_events/core/capturelayer.py`
  installs and undoes it; `src/sm64_events/replay/sourcefactory.py` selects
  the [[recorder]]'s camera; `src/sm64_events/replay/gpucapture.py` owns the
  capture path
- **Not** -- the desktop grab (`src/sm64_events/replay/video.py`): that
  photographs the window ~120 times a second and the trainer must then work
  out which [[frame]] each photograph shows.

### Renderer

The trainer's build of LINK's GLideN64 v4.2 (bundled as `GLideN64_SM64Trainer.dll`,
own label `LINK v4.2 [SM64 Trainer]`): the pinned upstream tree plus the
capture overlay that adds the SourceV2 export the [[capture layer]] reads.
Project64 receives it as `sm64_trainer_gfx.renderer`, no `.dll`, so its plugin
picker lists only the wrapper (a tester saw two plugins, 2026-10-24). A
stock GLideN64 has no such export, so a wrapper pointed at one captures
nothing. (His 2026-09-15 "input unavailable" report had a different cause:
Project64 selected the renderer directly, so the wrapper never loaded.)
GLideN64 is GPL-2.0; the About box credits its authors and links the
published source. The renderer keeps GLideN64's own settings identity, so a
user's existing GLideN64 settings carry over.

- **Lives** -- `tools/build_renderer.py` builds it from the pin in
  `renderer/build-inputs.json` and the overlay in `plugin/gfxwrap/`
  (`renderer/README.md` has the provenance and credits) into
  the git-ignored bundle folder (its BUNDLE constant in `tools/build_natives.py`), a
  build output that tool refreshes at launch
- **Not** -- the graphics plugin the user had before; that one stays in the
  folder untouched, and "Remove Practice Replay" selects it again

### Practice ROM

The cartridge the practice tooling works on: Usamune v1.93u (US) or a JP
Usamune ROM. Any other cartridge -- vanilla SM64, another hack, another game
-- is for a real speedrun: the [[capture layer]] forwards every call so the
[[renderer]] draws as plain GLideN64, the [[poller]] serves nothing and the
[[recorder]] captures nothing (his ruling, 2026-09-16: "when we play on the
vanilla rom, it should function identically to the baseline graphics plugin
we forked from"). The [[capture layer]] reads the cartridge header itself, so
the rule holds with no trainer open.

- **Lives** -- the rule in `src/sm64_events/core/onboarding.py` and its native
  mirror `plugin/gfxwrap/practice_rom.h`, which `tests/test_practice_rom.py`
  compares over the same headers
- **Not** -- the [[game version]] setting (US or JP), which picks addresses and
  grading, not whether the tooling is on

### Encoder helper

The 64-bit NVENC DLL (`SM64GpuEncoderV1.dll`) the server's isolated encoder
process loads to turn the [[renderer]]'s GPU pictures into AV1 or H.264
packets without copying pixels through Python. Built by `tools/build_plugin.py`
beside the wrapper; it carries the same native build id with its own suffix.
The process is a direct child of the server: the installed app starts itself
with `--gpu-helper`, and from source the server starts the base interpreter
on the module, never the venv launcher (which drops the no-busy-pointer flag). The
installed app had no such entry until 2026-09-27, so v1.9.1 never recorded.

- **Lives** -- `plugin/gfxwrap/gpu_encoder_dll.cpp`,
  `plugin/gfxwrap/gpu_bridge_encoder.cpp`; loaded by
  `src/sm64_events/replay/gpuprocess/process_worker.py`, started by
  `src/sm64_events/replay/gpucapture_session.py` through
  `src/sm64_events/core/childproc.py`

### Unsupported adapter

A graphics adapter the [[capture layer]]'s GPU capture can never record on: not
NVIDIA, a software adapter, an NVIDIA driver older than the [[encoder
helper]]'s NVENC API, or an encoder offering neither AV1 nor H.264. Decided
once per adapter and driver version, before any [[encoder helper]] starts
where the facts allow. The [[recorder]] then keeps the desktop grab for that
Project64, its status says why, and the [[setup screen]] accepts the desktop's
written pictures. Until 2026-09-27 such a machine retried GPU capture every
10-60 s forever and recorded nothing. A Project64 still running a [[capture
layer]] from another build takes the same path until it restarts (2026-09-28).

- **Lives** -- `src/sm64_events/replay/gpuadapter.py` (the facts),
  `src/sm64_events/replay/gpusettings.py` (the per-process memo),
  `src/sm64_events/replay/sourcefactory.py` (the desktop grab it keeps)
- **Not** -- a transient GPU failure: an encoder that fails for any other
  reason keeps retrying on the bounded backoff (`src/sm64_events/replay/gpuretry.py`).

### Renderer-thread capture

How the [[capture layer]] keeps every GPU call in Project64 on the
[[renderer]]'s own thread (2026-09-27). NVIDIA's threaded optimization froze
the [[renderer]] for 150-270 ms the first time any other thread in Project64
used the GPU: the "very brief visual stutter" he saw once, as recording
started. Now the [[renderer]] itself copies the picture, samples it and copies
a selected picture into the [[encoder helper]]'s texture, inside the callback
it already makes on every VI, and the helper creates those textures. The
delivery worker only keeps time and books, without the GPU.

- **Lives** -- `plugin/gfxwrap/gpu_render.cpp` (the sample, the copy into the
  helper's texture, the GL state guard); `plugin/gfxwrap/gpu_delivery.cpp`
  (render_tick, and which step runs on which thread); evidence in
  [the runtime doc](replay-gpu-runtime.md#first-capture-freeze)
- **Not** -- the driver warm-up it replaced (2026-09-26 to 09-27), which kept
  the worker's context and paid the same freeze once in the boot intro

### Bindings cause

Why the [[renderer]] could not name a capture surface: the first place it
stopped trusting its GL state observation in this context (a site, plus the
OpenGL error it read there), and how far arming that context got (created,
[[practice ROM]], function table installed, right thread). Added 2026-10-24
for a tester whose every recording ended "no qualified source surface"
(10034) with no way to tell which of those steps failed. It rides the
[[refused picture]] record, the `gpu_failure` log line and the startup
receipt, beside the GPU's name it read at context creation.

- **Lives** -- `plugin/gfxwrap/source_refusal.h` (the numbers),
  `plugin/gfxwrap/link_dispatch.cpp` (where each is recorded),
  `src/sm64_events/replay/gpudiagnostics.py` (the plain-words names)
- **Not** -- a refusal reason: the 10034 reason and its `0x53520001` detail
  say which check refused; the bindings cause says why that check failed

### Witness rebuild

The [[renderer]] asking the driver for every binding its state witness
holds, after a recoverable [[bindings cause]], so one GL error no longer costs
GPU recording until the next context (2026-10-24, his "Why can't we fix the
GPU stuff as best we can?"). It happens when the source is about to refuse a
picture or a [[ghost]] list would start no poses, at most once a second, and
never after a structural cause.

- **Lives** -- `plugin/gfxwrap/link_dispatch.cpp` (Reconstruct),
  `plugin/gfxwrap/source_refusal.h` (which sites are recoverable); contract
  in [the link-state doc](replay-link-state.md#witness-rebuild)
- **Not** -- a new recording: the GPU retry starts one of those

### Activation log

`sm64_trainer_gfx.activation.log`, beside the wrapper: this Project64
process's startup lines only -- ROM open, each capture's request and
preparation steps with their QPCs, and every slow
callback in the 15 s after a capture request. The main log rotates, so these
used to be gone within half an hour. The previous process's copy is `.1`.

- **Lives** -- `plugin/gfxwrap/diagnostics.h` (activation_event picks the lines)
- **Not** -- `sm64_trainer_gfx.log`, which keeps everything, rotating at 4 MiB
  through `.1` and `.2`

### Oracle reader

The developer's check that reads the [[frame]] number the game prints into its own
picture when Usamune's HUD memory display shows `0x8032D5D4`, and scores any
[[frame map]] against it. It trusts a reading only when a neighbouring
picture vouches for it (the counter steps by exactly one), so a misread
contradicts its neighbours instead of passing. His ruling: the display is
the oracle, never the shipped mechanism -- users will not enter a memory
address to play.

- **Lives** -- `src/sm64_events/replay/oracleread.py`; `tools/score_oracle.py`
  prints the verdict for a clip
- **Not** -- the [[pad reader]]: that reads what
  the game always draws; this reads a display only a developer switches on.

### Setup screen

The installation wizard: Select platform, Connect Project64, Set up Practice
Replay, and completion. Each page reveals its current action and relevant help.
The emulator sequence is find Project64 v1.6, close it, explicitly install the
[[capture layer]], reopen Project64, then open Usamune once to verify. A US
setup completes only with fresh game, replay pictures and input data. JP can
finish the available installation with an explicit unsupported-tracking warning.
N64 currently explains that support is in development and offers emulator setup.

The Set up Practice Replay page shows one card per **setup stage**: Close
Project64 (only when an install needs it), Install Practice Replay, Open your
game, and Checking your setup. The server's finer steps change a card's text in
place. Progress shows at once; a step back shows only once it lasts 2 s. Moving
on to the next stage, the card acknowledges what you did ("Usamune found") if
it was on screen at least 1.5 s; a stage that resolved by itself gets none.

Pages advance automatically after a visible acknowledgment. Back reviews prior
pages without advancing again. Not now defers incomplete setup for the current
app visit; Ready to practice saves completion and opens Practice. Setup is always
available in Settings; it also opens automatically for a missing, regressed,
outdated or unfinished emulator installation. Beginner guides appear where needed.

- **Lives** -- `src/sm64_events/ui/components/setupmodal.js`; setup stages in
  `src/sm64_events/ui/setupflow.js`; its API is `src/sm64_events/server/setup_api.py`

### Overlay layer

One transparent video file drawing part of the pad — the stick, the
buttons, or both — for an [[attempt]], so you can lay it over that
[[attempt]]'s footage in an editor. Every layer shares one canvas, one
playback rate and one starting [[frame]], so they stack in register and you
delete the one you do not want instead of cropping it.

- **Lives** — the overlay planner (`src/sm64_events/inputs/overlay.py`)
  → `tools/export_overlay.py`

### Ghost

The recorded [[attempt]] you race beside your current play. Its captured
graphics pose is rebuilt through libsm64 inside GLideN64, using the live
camera and terrain depth. Opacity and tint apply to the displayed ghost.
Stopping gameplay video storage leaves native pose capture, ghost creation and
playback running. A failed video encoder does not revoke the [[ghost source]].
Playback binds the exact recorded and live [[attempt]] origins and applies
the [[nudge]]. Missing poses or clock identity have a readable refusal. A
recording whose controller input is unknown on any [[frame]] is not a ghost at
all: every door a recording enters through (bundled collection, ghost
admission, community upload and download, collection import and export, your
own PB, saved and template ghosts) refuses it with one reason (his rule,
2026-10-12: "If it's not a perfectly recorded ghost, it doesn't belong in the
system"). A ghost records the [[practice ROM]] the trainer captured it on (US or JP)
and plays on either. The practice card's [[ghost toggle]] calls it Ghost Overlay.

- **Lives** — `src/sm64_events/ghost/session.py` joins selection, settings and
  the [[target]]; `src/sm64_events/ghost/native_session.py` publishes native playback
- **Not** — the [[overlay layer]], which writes a video file for an editor.

### Lineup

The active practice card's row of the people you race: your own seat first,
then each [[ghost slot]], slowest to fastest, centred at every width (his
words, 2026-10-16: "our pb on far left, then times get faster as we go to the
right"). A seat is a [[rank]] cap inside a ring in that [[ghost]]'s in-game
colour, the time and the name; a name wraps where the handle divides and is
never cut off. A dashed ring is a [[ghost]] that cannot race yet (yours before
your first time, a [[ghost]] still downloading); a lock on a ring is a pinned
[[ghost]], which stays even once beaten, to your left. The [[lineup track]] threads
the rings. With no [[strategy]] the row offers every one of the [[strategies]]
as a button, all one width, in rows of equal length.
Inside one lineup every change slides; a different [[star]], [[strategy]] or
[[ghost matching scope]] keeps your seat in place and exchanges everyone else.

- **Lives** — `src/sm64_events/ui/lineup.js` (drawing and motion),
  `src/sm64_events/ui/lineupmodel.js` (what it shows),
  `src/sm64_events/ui/components/lineup.js` (the card's wiring)
- **Not** — the [[ghost set]] the [[renderer]] draws; the Lineup shows who is in it.

### Lineup track

The rail through the [[lineup]]'s rings. The one stretch from your seat to the
next faster [[ghost]] lights up and carries how much faster that [[ghost]]'s time is,
in the hundredths both seats show ("−0.13") — the gap your next [[attempt]]
has to close, never a comparison with the last one.

- **Lives** — `src/sm64_events/ui/lineup.js` (gapText, markNext)
- **Not** — the [[input track]], a recording's pad history.

### Ghost toggle

The glyph in the active practice card's head that turns the overlay of
[[ghosts]] on and off — gold while they race. It is a preference, not a
capability: with nobody to race it stays on and the [[lineup]] says why.
A hotkey in the game flips the same setting, in play or in the game's pause
menu, even with other buttons held, but never while Usamune's menu is open
(it navigates with D-pad Up) — his rule, 2026-10-16. The hotkey is D-pad Up,
or all four C buttons together while Usamune's save state uses D-pad Up, so
one press never both loads a state and flips the overlay (his ask,
2026-10-17); the glyph's hover names the current one. Off hides the race
rather than ending it: the [[ghost set]] plays on unseen and unheard, so
switching back on partway through shows every one of them at the [[frame]]
the [[attempt]] has reached, at once (his ask, 2026-10-17).

- **Lives** — `src/sm64_events/ui/components/ghostswitch.js` (GhostToggle);
  `src/sm64_events/ghost/dpad_toggle.py` (the hotkey choice, the press rule
  and the flip)

### Recorded shadow

The round shadow the game actually drew under Mario on a recorded [[frame]],
kept with that [[frame]]'s pose: its matrix, nine corners, fade and texture. A
Mario-only [[ghost]] draws it under itself at its own opacity; the shadow
switch turns every [[ghost]]'s shadow off for comparison. Where the game drew
none, or capture cannot prove the shadow is Mario's, the recording keeps none
and playback draws none.

- **Lives** — `plugin/gfxwrap/ghost_root.cpp` captures it;
  `plugin/gfxwrap/ghost_pose.cpp` places it for drawing

### Pose refusal

The one named check that kept Mario's pose off a recorded [[frame]] (for
example `not_drawn_floor`, `animation_stale`, `body_mismatch`), stored with the
[[frame]] so every hole in a [[ghost]] explains itself in the extraction audit.
`not_drawn` on a [[frame]] that still has a pose means the game culled Mario (a
[[star]]-spawn or dialog camera looked away) and the pose's root came from the
last [[frame]] that drew him or from his own position and angles.

- **Lives** — `plugin/gfxwrap/ghost_capture.h` (the refusal enum); decoded by
  name in `src/sm64_events/ghost/clip.py`

### Dance tail

The recorded [[frame]]s after a [[star]] [[attempt]] closes, through the grab
and dance of the [[star]] that completed it, kept on the end of its [[ghost]]
recording so the [[ghost]] celebrates instead of vanishing at its touch; it
then fades out over half a second. For the [[100-coin star]] that is the
[[exit star]] after it, so the [[ghost]] plays on through its 100-coin grab.
A grab straight into the dance may close its [[attempt]] a [[frame]] before
the dance's first, and still keeps it. The trainer commits the [[PB ghost]]
before the [[recorder]] has the dance and adds the tail afterwards, so the
[[PB ghost]] is ready on the very next [[reset]]. When the recording stops before the
game's dance ends (a pause, [[reset]] or state load right after the grab), the
[[ghost]] plays the rest of the dance by animation from its last recorded
dance pose, to the [[frame]] the game's own dance ends on, then fades.
Display only: export keeps the [[attempt]]'s origin, closing [[frame]] and
time as they were. Recordings saved before it have none and still end at the
touch; [[segment]]s carry one only when a Bowser key or the grand [[star]]
ends them (the grand [[star]]'s is its own cutscene, up to its flight).

- **Lives** — `src/sm64_events/ghost/archive.py` keeps it on export;
  `plugin/gfxwrap/ghost_playback.cpp` draws, finishes and fades it;
  `plugin/gfxwrap/ghost_tuning.h` holds the game's dance timings

### Ghost defeat

What a [[ghost]] still racing does when live Mario's [[x-cam]] dance, the dance of
the grab that completes the [[attempt]], begins before the [[ghost]]'s own: it
plays Mario's own dying animation where it stood, with dead eyes and his
dying cry, through its last [[frame]], where he lies passed out, then fades out.
On land he falls over; a [[ghost]] swimming drowns. A pause freezes it where it stands. A [[ghost]] whose own dance began first, or on the same
[[frame]], keeps its [[dance tail]] instead, even when it grabbed later. Once
per [[attempt]]; a state load back into the [[attempt]] brings the [[ghost]]s
back.

- **Lives** — `plugin/gfxwrap/ghost_playback.cpp` decides and draws it;
  `plugin/gfxwrap/ghost_tuning.h` holds its animation, timing and sound
- **Not** — the [[dance tail]]'s fade, which ends a [[ghost]] that won its
  own race

### Translucent world layer

The game's water surfaces, and any other see-through world geometry, as they
would fall over a [[ghost]] standing behind them. The [[renderer]] repeats each
such game draw into the layer, keeping its color, how much it lets through and
its nearest depth; the compositor then gives every [[ghost]] pixel behind that
depth the same blend the game gave the scene there, so a submerged [[ghost]]
takes the water's color. The game's own picture stays untouched. The [[renderer]]
builds the layer only on a [[frame]] where playback has already chosen a
[[ghost]] to draw, so a finished or hidden [[ghost]] costs nothing.

- **Lives** — `plugin/gfxwrap/ghost_compositor.cpp`; the [[renderer]]'s
  repeat is staged by `tools/stage_ghost_renderer.py`; which [[ghost]]s draw
  on a [[frame]] is decided by the plan step in
  `plugin/gfxwrap/ghost_playback.cpp`

### Recorded particles

The bubbles, splashes, water rings, droplets, dust, sparkles, lava flame,
ground-pound mist, sand, leaves, snow, ground-pound and wall-bonk bursts, punch
dirt and his knocked-off cap the game drew for Mario on a recorded [[frame]]: which of the ROM's particle pictures each
one showed and where. A Mario-only [[ghost]] draws them with the ROM's own
pictures, after its body. Another object's bubble, or a particle whose picture
the ROM reader does not know, is never drawn.

- **Lives** — `plugin/gfxwrap/ghost_particle_capture.cpp` records them;
  `renderer/model/particles.c` reads the pictures; `plugin/gfxwrap/ghost_particle_pose.cpp`
  places them

### Player-first fade

A Mario-only [[ghost]] fades wherever it would block the player. The parts of
it that stand between the camera and live Mario, over his body on screen, keep
only 15% of their opacity. Near the camera the whole body fades out, so it
can never fill the screen. The fade leaves a [[ghost]] behind Mario, or away
from him, untouched. Its [[recorded shadow]] and [[recorded particles]]
follow the same rule. It
combines with the [[overlap fade]] under one 15% floor: a [[ghost]] that overlaps
Mario and stands in front of him still keeps 15%, not less. Only the camera
fade may go below it. Its [[ghost sound events]] can follow it too. The card's **Fade in front of Mario** switch turns it off
for comparison.

- **Lives** — `plugin/gfxwrap/ghost_occlusion.h` (the rule and its tuning);
  playback fills live Mario's region each frame
- **Not** — the [[overlap fade]], which fades a whole [[ghost]] when its body
  touches live Mario's, wherever the camera is.

### Overlap fade

A [[ghost]] whose body touches live Mario's fades as a whole, smoothly down to
15% of its opacity when the two stand on the same spot, so it never vanishes
on a pole, a ledge or a plank both Marios hold. It is back to full opacity
once the bodies stop touching, plus any extra distance set on the practice
card. Its [[ghost sound events]] can follow it too.

- **Lives** — `plugin/gfxwrap/ghost_proximity.h`; the floor is shared with the
  [[player-first fade]] in `plugin/gfxwrap/ghost_occlusion.h`
- **Not** — the [[player-first fade]], which fades only the pixels in front of
  live Mario and depends on the camera.

### Held object (ghost)

The object Mario holds on a recorded [[frame]] (a Bob-omb, a baby penguin,
a cork box), recorded as what it is (its behaviour, named through the
[[practice ROM]] the trainer captured it on), its animation state,
and where his hand drew it in that [[frame]]. Captured only when the game
provably drew it from his hand in that [[frame]]. A [[ghost]] draws the kinds
its model knows (the Bob-omb, the small penguin, King Bob-omb, Chuckya, the
cork and crazy boxes) in his hand and leaves the others out. Once he throws
or drops it, capture follows the same object where the game drew it, for up
to 10 s, until it explodes, its slot holds another object, or he grabs it again.

- **Lives** — `plugin/gfxwrap/ghost_capture.cpp` (capture); decoded in
  `src/sm64_events/ghost/clip.py`; drawn by `renderer/model/held.c`

### Ghost path

Where a [[ghost]] goes, drawn in the game world just ahead of you as you
play: a strip of the game's own chevrons on the floor where it ran, a thin line
where it was in the air with dots on the floor beneath, coloured from grey
(standing) to green (full speed) to white (faster than any normal move), and
hidden behind walls like the [[ghost]]. It follows how far along its line you
are, not where the [[ghost]] is, and only moves forward: falling behind still
shows the next stretch from your feet, and coming back through a place shows
the [[ghost]]'s later pass there. It is one line: the fastest [[ghost]]'s,
or the average of them where they all ran together. New users start with
the average path and button presses drawn where the [[ghost]] pressed them.

- **Lives** — `plugin/gfxwrap/ghost_path.h` (what is drawn) and
  `plugin/gfxwrap/ghost_playback.cpp` (which stretch); its settings rows in
  `src/sm64_events/ghost/settings.py`

### Ghost sound events

The sound requests Mario issued on a recorded [[frame]] (up to eight, in
order), kept with that [[frame]]'s pose so a [[ghost]] plays his jumps,
landings and voice as it passes that [[frame]], through SM64's own sound
engine at its set volume. Each [[ghost]] sounds from where it stands relative
to the live camera (panned and distanced as the game does Mario) and fades by
the inverse square of its distance from live Mario past 400 units, the chime
of a [[star]] or cap grab included. Capture drops other objects' sounds, so a
[[ghost]] plays no music. With the card's **Fade its sound** (beside Volume,
on by default), a [[ghost]]'s sound also follows its [[overlap fade]] and
[[player-first fade]], and while it overlaps him, a sound live Mario made on the same
[[frame]] plays only once.

They play from the [[ghost sound player]], so a recording only has them while
[[ghosts in recording]] is on. Each [[practice ROM]] plays its own sounds:
on JP a [[ghost]] sounds as the JP game does, so a US [[ghost]]'s US-only voices
(its triple-jump yahoo, "doh") are JP's or none.

- **Lives** — `plugin/gfxwrap/ghost_capture.cpp` captures them;
  `plugin/gfxwrap/ghost_audio.cpp` makes their sound; `plugin/gfxwrap/ghost_sound_fade.h`
  owns how they fade with the picture

### Ghost sound player

The small trainer process that plays [[ghost sound events]] on the output
Project64's own sound uses. The [[renderer]] still makes the sound and hands it
over through shared memory; it plays outside Project64 because the [[recorder]]
takes everything Project64 plays, and a [[ghost]]'s sound belongs in a
recording only while [[ghosts in recording]] is on. Each open Project64 gets
one, which ends with it or with the trainer.

- **Lives** — `src/sm64_events/ghost/sound_player.py`; the shared ring is in
  `plugin/gfxwrap/ghost_audio.h`

### Ghost source

The native poses a [[ghost]] plays, with their exact [[attempt]] origin.
They come from a local recorded [[attempt]] or an [[imported ghost]]. Marking a
newly recorded [[attempt]] preserves its poses independently of saved video.
Older video-only recordings and input-file templates cannot supply them.
Unavailable sources include a sentence explaining why.

- **Lives** — `src/sm64_events/ghost/native_session.py` resolves playback;
  `src/sm64_events/ghost/selection.py` qualifies the offered recordings

### PB ghost

The [[ghost]] ticked by default: the [[attempt]] behind your current [[personal best]]
for the practised [[target]] and [[strategy]]. While you keep its box ticked, saving a
PB makes that [[attempt]] the [[ghost]] at once (the trainer keeps its Mario poses before it
cuts the video); undoing or hiding that PB falls back to the previous save.
The trainer remembers an untick for that [[target]], so later PBs stay off. Derived from
the saved PBs on every read, never stored, and never changes the input comparison.

- **Lives** — `src/sm64_events/ghost/pb.py`

### Best recorded run

Your fastest recording of the practised [[target]] and [[strategy]] that has
Mario poses, racing in the [[PB ghost]]'s place and colour while your saved
[[personal best]] has none (an older build's PB, an [[imported time]]). Only a
[[run]] the trainer admitted itself qualifies, so the [[ghost ceiling]] applies to it.
The [[lineup]] gives it its own seat under this name, and its label in the
game says the same; your PB stays the [[rank]] you chase (his decision,
2026-10-25). A PB with poses takes the seat back.

- **Lives** — `src/sm64_events/ghost/rivals.py` (Rivals._pb_seat)
- **Not** — a [[ghost slot]]: it never holds a rival, and unticking the PB
  unseats it.

### Ghost set

Which [[ghost]]s play for a [[target]]: the [[PB ghost]] box plus any [[saved ghost]],
[[imported ghost]] or [[template track]] [[attempt]] you tick, up to 16, each named by the time it achieved.
The [[renderer]] draws them together, each tinted its own colour. Collections
contain native poses. With the PB box ticked, no PB saved and nothing else ticked, your active
[[template track]] plays.

- **Lives** — `src/sm64_events/ghost/selection.py`

### Ghost collection

One `.sm64ghosts` file containing selected native [[ghost]] recordings, their
creator credit, time, [[game version]], [[target]] and [[strategy]]. The **[[Ghost]]
Library** dialog opens with the originating [[target]] and currently raced
recordings. Separate import and export flows preview the chosen recordings before
confirmation. Export can choose each [[strategy]]'s best independently or use a
saved search that resolves current results next time; export skips a [[run]]
whose [[playback copy]] has an unknown input, with that reason. Import selects all new
recordings by default and supports a smaller selection; import keeps a
recording with an unknown input on disk, lists it unavailable with the reason,
and never plays it. Collections can contain
many more recordings than the [[ghost set]] can race at once.

- **Lives** — `src/sm64_events/ghost/portable.py` and `src/sm64_events/ghost/sharing.py`;
  `src/sm64_events/ui/components/ghostlibrary.js`

<a id="rival-slot"></a>

### Ghost slot

One place in the adaptive [[ghost set]] for the current [[target]] and [[ghost matching scope]]. All app controls and messages call these [[ghost]]s (his correction,2026-10-19). The trainer
chooses a compatible recording above the saved [[personal best]], or protects
the player's exact manual choice until they clear it. Every seat, including
manual choices and recordings waiting to download, holds one recording per
player. A second choice from that player must replace their existing slot
(round 52, 2026-10-15: "there's no way for someone to be racing two versions of me
at the same time right?"). Seats the range cannot fill widen upward, nearest
subdivision first, then downward (his rule, round 49: "keep expanding the
range upwards until the rival list is full ... then expand downwards"), in
fixed ranges too. Automatic seats never hold the player's own
recordings: this install's player identity, the signed-in community account, and
the bundled [[ghost collection]] when its reviewed author identity matches the
signed-in account. Legacy collections without that identity retain export-credit
matching (his rule, 2026-10-13: "the player doesn't ever actually fight
their own [[ghost]]s as a rival"). A pinned own recording still plays. The slot keeps its colour when
its recording changes; the separate [[PB ghost]] keeps its own colour.

A seat keeps its player, slot and colour across a new [[personal best]] until
the PB passes it; a passed seat first takes that same player's fastest [[run]]
still ahead and in range (his answer, 2026-10-16: the same rivals keep
improving). With no saved PB yet, the seats spread evenly across
Capless 5 to Toad 1 ([[rank]] steps 0..9). A pinned seat never moves, even once
beaten. With no [[strategy]] there are no slots, no pins and no cloud request.

- **Lives** — `src/sm64_events/ghost/adaptive.py` and `src/sm64_events/ghost/rivals.py`

### Ghost matching scope

The [[Strategy]] or Overall choice on the practice card. [[Strategy]] restricts
[[ghost slot]]s to the current [[strategy]]; `Overall` admits other strategies
and grades them on the [[Overall curve]]. Both use the current [[strategy]]'s saved
[[personal best]] as their starting basis. Each choice remembers its own manual
choices, and every [[ghost]] keeps its recorded [[strategy]] label.

- **Lives** — `src/sm64_events/ghost/rivals.py` and `src/sm64_events/ui/components/practicelog.js`

### Region scope

Whether a [[strategy]] plays the same on US and JP: **shared** or
**regional**. The trainer reads the answer from that [[strategy]]'s
[[standard]]s and never asks. A JP difference in its [[ladder]] makes a [[strategy]]
regional, and so does an [[Ultimate Sheet]] that times it on one release
only; with neither, a [[strategy]] with a [[ladder]] counts as shared. A
[[strategy]] with no [[ladder]] follows the others on its [[target]], and a
[[target]] with none at all is regional. Shared: a [[ghost]] recorded on
either release may race you, and one [[personal best]] covers both releases,
the faster winning. Regional: only your running release's [[ghost]]s race,
and each release keeps its own [[personal best]]. A [[ghost]] whose release
nobody recorded races only on a shared [[strategy]]. A bundled [[ghost]] that
names no release counts as US: before JP support, the trainer recorded only
on US.

- **Not** — the [[region switch]], which picks which releases a surface
  shows; the [[game version]], which picks the [[ladder]] the trainer grades
  you on; or the [[ghost matching scope]].
- **Lives** — `src/sm64_events/ranks/region_scope.py`
  → the rivals (`src/sm64_events/ghost/adaptive.py`) and the personal best
  slot (`src/sm64_events/tracking/views.py`)

### Climb profile

A separate practice database with its own player identity, the original
[[segment]] definitions and calibration, and a local [[ghost]] population.
The default developer simulation starts with zero practice and no
[[personal best]], with unchanged saved clips credited to `gtest` opponents.
It presets four [[ghost slot]]s across all [[rank]]s. The earlier seeded Wild Blue
experiment remains available explicitly. The launcher tests progression without
changing original history.

- **Lives** — `tools/ghost_climb.py` and `src/sm64_events/core/paths.py`

### Sign-in step

A configured build asks for a Discord or Twitch sign-in. The startup update
check stays silent without delaying this step; an actual update offer takes
priority and returns here when dismissed. A new account chooses a player name and acknowledges
the policies once. A returning active account skips that form and account settings
entirely. After either path, unfinished Project64 setup opens; a detected working
installation skips setup. Setup waits until the account check settles. The player
cannot close a required sign-in or profile step until the account is active (his
direction, 2026-10-12: "logging in is the first step for onboarding, and
you're not allowed to continue unless you login in some way"). A stored
refresh token counts as an account while offline, so practice never locks
out on a bad connection; signing out returns to this step. Google and email
are not offered. The same provider identity always returns to its own
account. New-account signup enables automatic [[ghost]] sharing, without a sharing
toggle; Settings is where the player changes it. Returning login preserves a
saved opt-out. The name field checks validity while typing and availability after
a pause or on blur (4 to 20 characters; a check or a cross), and the account
settings screen shows the name, the provider and the [[Rank tab]]'s own chip row.

- **Lives** — `src/sm64_events/ui/community.js` (accountRequired),
  `src/sm64_events/ui/components/communityaccount.js`,
  `src/sm64_events/ui/components/communityprofile.js` (useNameCheck),
  `src/sm64_events/ui/components/communitysettings.js` (AccountSummary);
  `src/sm64_events/community/account.py` (stored, check_name);
  `cloud/community/src/service.js` (the names check route)
- **Not** — the Project64 setup wizard (`setupmodal.js`), which follows it.

### Claimed player page

A page inside the existing [[Rank tab]], addressed by a signed-in account's UUID,
separate from a Sheet [[runner]]'s imported projection. The checkmark confirms
account ownership, not legitimate performances. An owner can add a bio and social
links and explicitly share current saved PBs independently of [[community ghost]]
sharing. Available [[ghost]] times remain distinct from the shared saved PBs.
Comparison uses the viewing player's selected [[scope]] and local [[standard]]s;
private or unavailable entries are missing with coverage, never a loss.

- **Lives** — `src/sm64_events/ui/components/playerpage.js`,
  `src/sm64_events/server/player_api.py`,
  `src/sm64_events/server/player_comparison.py` and `cloud/community/src/players.js`

### Publishing profile

The one installation/database profile explicitly selected to publish an
account's current saved PBs. Switching replaces that publisher after
preview and consent; it does not merge devices. Pausing stops future updates,
while withdrawal removes the shared PB data. Neither changes local
PBs or the independent [[ghost]]-sharing setting.

- **Lives** — `src/sm64_events/community/progress_sync.py`,
  `src/sm64_events/community/progress_state.py` and
  `cloud/community/src/progress-store.js`

### Community ghost

A [[ghost]] shared by an authenticated, invited player. The service keeps that
player's fastest full-start recording for each [[division]], [[strategy]],
[[target]], clock and ROM version. Structural validation checks the recording;
it does not certify that gameplay was legitimate. Sharing begins unchecked.

- **Lives** — `cloud/community/` and `src/sm64_events/community/`

### Has ghost

The Library page's filter for published [[community ghost]] recordings. It shows
[[target]]s and their [[strategy]] choices with shared recordings, including entries
without video. Regions and the Library's current [[target]] narrow the results
before paging. Opening an entry shows the player, time, [[strategy]] and ROM.
**Add rival** saves that recording to the [[target]]'s [[Lineup]] without changing
current Practice. When every visible seat holds a saved recording, the player
chooses which rival to replace.
Publication and structural validation do not verify legitimate gameplay.

- **Lives** — `src/sm64_events/ui/components/community-library.js`,
  `src/sm64_events/ghost/community_library.py`,
  `src/sm64_events/ghost/rival_changes.py`,
  `src/sm64_events/server/community_library_api.py` and
  `cloud/community/src/library.js`

### Rival pool

The bounded metadata list for a [[target]], ROM, clock and [[ghost matching scope]],
covering the whole [[rank]] [[ladder]]. The cloud can reuse it across players. The trainer
selects [[ghost slot]]s locally and downloads only their recordings, each once, so
a new [[personal best]] needs no new list. The trainer asks for each list once, holds
it under the cloud's 10-minute publication lease (so a withdrawn [[ghost]] leaves every
screen), and renews it only for the [[star]] on screen with the overlay on; switching
back within the lease asks nothing. A complete membership scan and downloaded playback bytes
are separate states.

- **Lives** — `src/sm64_events/community/sync.py` and `src/sm64_events/community/cache.py`

### Ghost ceiling

The slowest time a [[run]] may have and still fill a [[ghost slot]] or become a
shared [[ghost]]: its [[strategy]]'s slowest named cutoff (the edge of the
lowest named [[division]], Capless 4) plus 10%. The trainer never makes a
slower [[run]] a rival recording or offers it to the cloud, and tells the
player nothing (his rule, 2026-10-13: "We shouldn't tell the user"). The
trainer and the cloud apply the same integer rule. The player's own
[[PB ghost]] has no ceiling.

- **Lives** — `src/sm64_events/ghost/community_catalog.py` (trainer) and
  `cloud/community/src/catalog.js` (cloud; the upload admission in
  `cloud/community/src/store.js` applies it)

### Retraction

Taking one shared [[run]] back out of the cloud under every [[strategy]]
except the one it now belongs to, after the player changes that [[run]]'s
[[strategy]] (his rule, 2026-10-13, round 48). The trainer drops its pending
offers under the old [[strategy]], keeps the request across a restart, and
asks the cloud to unpublish those copies; the cloud's cleanup collects the
bytes. Not account-wide withdrawal, which removes every shared [[ghost]].

- **Lives** — `src/sm64_events/community/sync.py` (retract_other_strategies),
  `src/sm64_events/community/outbox.py` and `cloud/community/src/store.js`

### Private beta

A test build sent directly to friends, rather than published as a download. A
normal-identity private candidate uses the same installation and practice data as
production, defaults to beta updates, and may carry a private friend invitation.
The older standalone ZIP has a separate profile and no public updates. Project64
and the [[recorder]] remain shared machine resources in both cases.

- **Lives** — `src/sm64_events/core/build_channel.py`, `tools/release_flow.py` and `tools/build_beta.py`;
  `docs/private-beta.md`

### Public beta

A published prerelease anyone can download from the beta page. Its installer
starts new users on that beta in the normal installation, with future beta and
production updates through the same app. It contains no private invitation; open
signup is an explicit community-service setting. A player's saved update choice
and practice data survive later releases.

- **Lives** — `tools/release_flow.py`, `src/sm64_events/bootstrap/installer.py`,
  `src/sm64_events/core/update_policy.py` and `tools/build_website.py`

### Imported ghost

A native [[ghost]] received in a [[ghost collection]], with the original
exporter's credited name and [[strategy]]. Stored separately from local
[[attempt]]s and [[personal best]]s; importing it never awards a PB or gold.
[[Segment]] matching compares the actual definition and clock rather than
another player's numeric ID. Unknown definitions stay unavailable with a
reason. Re-export preserves the original creator's credit.

- **Lives** — `src/sm64_events/ghost/sharing.py` and `src/sm64_events/ghost/sharing_targets.py`

### Saved ghost

An [[attempt]] you chose to race that is neither your [[personal best]] nor a
[[template track]]. Its drawer's save button keeps its Mario poses and ticks it
in the [[ghost set]]. Its native recording can carry exact pad evidence for
the in-game input display; saving it never changes the input comparison.

- **Lives** — `src/sm64_events/ghost/saved.py`

### Playback copy

The copy of a local native recording that the game plays and a
[[ghost collection]] exports, when the recording predates exact pad capture.
Each record without a pad takes the [[input track]]'s pad at its own game
[[frame]], so the [[ghost]]'s input display shows what you pressed. The saved
recording itself never changes; a [[frame]] the track lacks stays unknown, and
a copy with any unknown [[frame]] is not a [[ghost]]: its manifest says
`inputs_complete`, and every playback, export and sharing door asks it.

- **Lives** — `src/sm64_events/ghost/padfill.py` (written in an inputs
  folder beside the saved native recordings); the question itself is
  inputs_complete in `src/sm64_events/ghost/clip.py`
- **Not** — a [[polled fill]], which is the replay timeline's drawing of a
  sample with no picture.


### Game picture

The part of Project64's window its [[renderer]] draws into. On 1.6 the client
area also contains a status bar; the game picture excludes that strip. Native
[[ghost]]s draw directly inside this picture.

- **Lives** — the renderer's presentation surfaces;
  `src/sm64_events/replay/video.py` owns recording-window geometry

### Ghost clock

The [[renderer]]'s clock that chooses a recorded [[ghost]] occurrence from the
live [[attempt]] origin and its timing [[nudge]]. The tracker publishes settled
arm, rebase and disarm markers; native playback advances from the game's own
timer each occurrence. No browser timer socket drives it.

- **Lives** — `plugin/gfxwrap/ghost_clock.h` and `plugin/gfxwrap/ghost_clock.cpp`, with
  origin commands from `src/sm64_events/ghost/native_session.py`


### Nudge

How far ahead or behind the [[ghost]] plays, counted in game [[frame]]s.
Positive shows a later recorded occurrence; negative shows an earlier one.
It belongs to the practice [[target]] and returns to zero when that changes.

- **Lives** — `src/sm64_events/ghost/session.py` holds it;
  `src/sm64_events/ghost/native_session.py` publishes it;
  `plugin/gfxwrap/ghost_playback.cpp` applies it



### Ghost colour

The colour shift applied to a [[ghost]]'s body and supported held objects,
so you can distinguish it from live Mario. It starts enabled at
+250 degrees for new users. You can change the angle or disable the colour;
saved preferences remain in effect. Each selected [[ghost]] gets its own hue.

- **Lives** — `src/sm64_events/ghost/settings.py` owns the preference;
  `plugin/gfxwrap/ghost_compositor.cpp` applies it; the practice card's
  `src/sm64_events/ui/components/ghostswitch.js` provides the controls

### Name box

The box behind a [[ghost]]'s name in the game: SM64's own dialog box, the
black one with white text that characters speak in, or the white one with
black text that signs use, at the opacity you choose (none at zero). The name
itself sits just over the top of the [[ghost]]'s body, so it crouches and dives
with him. The [[renderer]] draws it in the US game's font, which your own US
[[practice ROM]] lends once you have played it: the [[renderer]] keeps a copy on
your machine. On JP before that, it draws JP's own font, in capitals (JP's
has no lowercase).

- **Lives** — its two rows in `src/sm64_events/ghost/settings.py`;
  `plugin/gfxwrap/ghost_annotation_mesh.h` draws it;
  `plugin/gfxwrap/ghost_font_cache.cpp` keeps the lent US font

### Corner inputs

Every [[ghost]]'s inputs in the game's bottom left corner, one compact row
each stacked above your own input display (on by default). A row keeps your
display at half size, in the Stick or Values style you chose for yours in
Usamune's menu: its numbers centred over yours, its buttons and name
packed close beside them, and it hides whenever Usamune hides yours (its menu
open or its display off). At most seven show, in the order you chose them, and a row stays up
while that [[ghost]] is off screen.

- **Lives** — its switch row in `src/sm64_events/ghost/settings.py`;
  `plugin/gfxwrap/ghost_annotation_mesh.h` draws it

### Ghosts in recording

The switch on the [[ghost]] card (off by default) that also records a [[ghost copy]]
of your play while it is on. Your own recording stays clean either way: no
[[ghost]] picture and no [[ghost]] sound, so a PB is always clean. It applies to
what the [[recorder]] takes from then on.

- **Lives** — its switch row in `src/sm64_events/ghost/settings.py`

### Ghost copy

A second recording, made beside your clean one while [[ghosts in recording]] is
on, that keeps what the game window showed: the [[ghost]] bodies hidden behind
walls as you saw them, their names and [[corner inputs]], and their sound mixed
with the game's. It holds the clean recording's pictures, one for one, at the
same times, so the replay's button that shows the [[ghost]]s switches between
the two without losing your place. Saving a replay keeps the copy beside it; nothing else
(sharing or the saved list) ever reads it. It costs a
second video encode and its own disk space only while the switch is on, and if
anything goes wrong with it, only the copy stops.

- **Lives** — `plugin/gfxwrap/ghost_presentation.cpp` (the picture the window
  showed) and `plugin/gfxwrap/gpu_delivery.cpp` (its capture);
  `src/sm64_events/replay/ghostcopy.py` (its encoder);
  `src/sm64_events/replay/service.py` (cut, save and serve);
  `src/sm64_events/ui/components/replay.js` (Show ghosts)

### Input document

An [[input track]] written out as text you can send someone, and edit by hand.
Export writes one, import reads one, and a [[template track]] is always one —
which is what lets a template come from an [[attempt]] you marked, a file
another player sent you, or one you typed out yourself. Each row carries the
pad and what Mario was doing while you held it — his action, which way he
faced, how fast he went — so the document is the whole example and not the
controller alone; a row you type by hand may stop after the pad. Optional
author credit travels with the document and does not establish an account.
The optional template name travels with exports and autofills imports; a name
the player types takes precedence.

- **Lives** — the document format (`src/sm64_events/inputs/document.py`)

### Template track

The [[input track]] the trainer draws behind your own, so you can see where the
two differ — its stick, buttons and actions on the same rows as yours,
its speed on the same scale, and a second indicator in each direction dial. You mark the
[[attempt]] that felt right and it becomes the template for that [[target]] on
that [[strategy]]. The trainer shows both and names neither as the reason — it
reports what each was doing on a [[frame]], and you read the gap.

- **Lives** — the template store (`src/sm64_events/inputs/templates.py`)

### Epoch

The stretch since Usamune's counter last restarted. Two readings from different
epochs describe different things, so the trainer works out WHY the counter
restarted before trusting any difference between them: a restart the level
caused — a door or warp the game itself carried out, in or out of a
subarea, or a teleporter — adds the stretch before it to the [[star]]'s
whole time, and a [[reset]] throws it away.

- **Lives** — the epoch reader (`src/sm64_events/detectors/counter_epoch.py`)

### X-cam

The camera cut Super Mario 64 plays when you touch a [[star]]. The community
times a grab there, so the trainer publishes a [[star]] time when the
x-cam starts rather than when the game finishes writing its own result.
The trainer derives that time from Mario's landing and Usamune's running
counter, so the time holds whatever Usamune's TIMER → STOP menu says; the
menu only decides whether the game's own screen agrees.

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

Mario dying, which closes the open [[attempt]] with a losing [[outcome]]. The
game marks Mario dead three ways: health under one wedge, a dying animation,
or a death warp already on its way. A finish he reaches while any of those
holds — a pipe entered mid lava boost, a key grabbed during a void-out — is a
death too ("finished_dead"), never a success.

- **Lives** — the death detector and its alive check
  (`src/sm64_events/detectors/death.py`)

### Reset

Usamune restarting the level — the L button, or entering a course. A reset
closes whatever [[attempt]] was open and may open the next one as an
[[anchor]]. Since 2026-09-23 loading a save state is a [[save-state load]]
instead, which closes nothing.

- **Lives** — the anchor detector (`src/sm64_events/detectors/anchors.py`)

### Session

One sitting of practice. Sessions group [[attempt]]s for the [[practice log]]
and for scoping a [[progress graph]]; you can reopen one and keep appending to
it. A sitting that recorded no [[attempt]] was not a session, so the trainer
forgets it at the next startup — its [[journal]] rows stay, because they can
still govern other sessions' [[attempt]]s.

- **Lives** — the tracker service (`src/sm64_events/tracking/service.py`)

### Exit star

A [[star]] you collect only to leave a course, not the one you set out for —
which makes its time comparable only with other [[attempt]]s that left the
same way. Its grab finishes the [[100-coin star]]'s [[attempt]] and records
none of its own.

- **Lives** — the 100-coin rules
  (`src/sm64_events/tracking/hundred_coin.py`)

### 100-coin star

The [[star]] a course gives you for one hundred coins. Because you can finish
that collection anywhere, which [[exit star]] you ended on defines which
[[strategy]] the [[attempt]] belongs to. Grabbing it, or holding a hundred
coins at the [[exit star]], proves the visit — even one a savestate cut
short — so the trainer files the [[attempt]] here and never on the
[[exit star]].

- **Lives** — the 100-coin rules
  (`src/sm64_events/tracking/hundred_coin.py`)

### Topology

The map of which course you can reach from which, which the trainer judges a
[[segment]] move against. A move the map does not allow means the [[segment]]
did not happen.

- **Lives** — the world graph (`src/sm64_events/tracking/topology.py`)

### Ultimate Sheet

The community spreadsheet this trainer reads its [[approach]]es from — one row
per documented way of doing a [[target]], one column per [[runner]].

- **Lives** — the sheet reader (`src/sm64_events/library/sheet.py`)

### Approach

One documented way of doing a [[target]], as the [[Ultimate Sheet]] publishes
it. Adopting an approach mints a [[strategy]] you can practice.

- **Lives** — the library builder (`src/sm64_events/library/build.py`)
- **Not** — a [[strategy]]. You practice a [[strategy]] and hold a [[personal
  best]] on it; an approach is only what the community wrote down.

### Sheet entry

One [[runner]]'s recorded time for one [[approach]], and the video they linked
to it. It belongs to the regional release its own sheet row names, and to
BOTH releases where that row names none — his ruling, 2026-09-05: a row
drawing no distinction times the same thing on either machine. The trainer
stamps an [[imported time]] with that release and offers the same [[sheet
entry]] as a [[goal]] under it, so the two readings cannot disagree.

- **Lives** — the library builder (`src/sm64_events/library/build.py`) and
  the release rule (`src/sm64_events/library/sheet.py`)

### Runner

A person holding at least one [[sheet entry]] in the [[Ultimate Sheet]].

- **Lives** — the library builder (`src/sm64_events/library/build.py`)

### Matched strategy

The vetted [[strategy]] an [[approach]] turns out to be — found by comparing
whole [[ladder]]s, since one time alone can misread a JP result as three
seconds slower than its US twin. A match shows your real [[rank]] and
[[personal best]] beside the community's; an [[approach]] with no match stays
sheet-only. It can carry its own variant: a [[100-coin star]]'s [[approach]]
matches only inside the exit its [[runner]]s actually used.

- **Lives** — the matcher (`src/sm64_events/library/adopt.py`)
  → the "= your …" chip on the [[Library tab]]

### Sheet piece

A stretch inside one way of doing a [[target]] that the [[Ultimate Sheet]] times separately — the
sheet's own [[subsection]] rows, a third kind of row beside whole
[[approach]]es. On the [[Library tab]]'s [[target]] page a piece draws the
same full card an [[approach]] does — [[rank]] bands, [[division]]s, example
videos — under its own Pieces heading; its one difference is the link/record
strip. Each piece automatically receives a local [[segment]] under its
parent [[star]] or movement, reusing an existing matching piece where possible.
A newly created piece supports manual and Sheet-[[imported time]]s; its start
and end detection remain unconfigured until you define them in [[Segment]]s.
Its Library and Practice cards use the same effective [[ladder]].

- **Lives** — the library builder (`src/sm64_events/library/build.py`)
  → the Pieces cards on the [[Library tab]]'s target page

### Estimated ladder

A provisional [[ladder]] for a Sheet row with no submitted times,
derived from its published best or ideal, or an explicitly named
related row. Its label explains the evidence; its sample count stays
zero until [[runner]]s submit times. Even one submitted time receives
a [[ladder]], with coincident [[tier]]s merged.

- **Lives** — `src/sm64_events/library/ladder_estimates.py`

### Adoption

Your own assignment of a sheet row — a castle-movement [[approach]] or a
[[sheet piece]] — to a [[segment]] you built, minting that [[segment]] a
[[strategy]] with the community's [[ladder]] so every [[attempt]] grades there.
Yours, not the community's: it lives beside your data and survives a
refresh. The door sits beside the [[target]]'s own name on the
[[Library tab]] and links the WHOLE [[target]] — one click adopts every
laddered [[approach]] as its own [[strategy]] on the [[segment]]; a
[[sheet piece]] keeps its own row-level door; unlink removes the explicit batch and returns to automatic placement.
The [[segment]] builder carries the same door from the other side — pick a
sheet [[target]] while making or editing a [[segment]], and the link lands
when you save. A row with no [[segment]] to link also offers recording one on
the spot: the strip's record button opens the [[recorder]] right there,
pre-named after the row and pre-parented to its [[target]]'s [[star]] or
[[segment]]; one save then names, links and — for a [[sheet piece]] — mints
the [[subsection]] in one step. The connection works backwards too: a
[[practice log]] card's book button on a linked or name-matched [[segment]]
lands on the [[target]] page the link points at — the linked [[sheet piece]]
already open, centered on your own standing — never on an empty page. A [[segment]] whose name equals an entity-less
[[target]]'s label
associates by itself — no click — and an associated row shows your standing
at once: your [[segment]]'s best [[personal best]] graded on the row's own
displayed [[ladder]], or Capless ([[Iron]]) when you hold no time yet. Where
the [[segment]] already carries a community-vetted [[strategy]] of the same
name, the assignment still lands and the vetted [[ladder]] keeps grading.

- **Lives** — the assignments (`src/sm64_events/library/adoptions.py`)
  → the link strip on the [[Library tab]]'s target page

### Column export

The reverse of an [[imported time]] — your [[personal best]]s written out in
the [[Ultimate Sheet]]'s own notation, one line per live worksheet row, ready
to paste back into the sheet as your own [[runner]] column. Its first line is
worksheet row 2, so pasting into your column's second cell puts every time
back on the row it came from. It prints a time exactly where an [[imported
time]] would land one, under the same [[strategy]] name: a [[sheet piece]] or
a castle-movement [[approach]] needs an [[adoption]] (or the unasked
name/seed-key match that grants one) to say which [[segment]] it lands on,
and an [[approach]] on a [[star]] otherwise files under its [[matched
strategy]], or under the sheet's own row name where no match names one —
qualified by its 100-coin heading or by the row it sits under wherever the
sheet repeats a name, so every worksheet row of one entity is a slot of its own.
A row that names the thing itself carries your best among the times no
other row of its block claims. A name this database holds no
[[personal best]] under prints the row's [[held time]] if an import left
one, else stays blank, so the column can miss a time but never print a
wrong one. Once built, the column stays on the [[Scorecard]]'s page until
one of your times changes or you rebuild it: the button reads "Ready to
copy", a click copies it, Open shows every line, and the clipboard fills by
itself only while you are looking at the page — so tabbing away during the
build costs nothing.

- **Lives** — the column builder
  (`src/sm64_events/library/export_column.py`)
  → the scorecard's column endpoint (`src/sm64_events/server/scorecard_api.py`)
  → the held column and its controls (`src/sm64_events/ui/components/scorecard.js`)

### Runner rating

The [[MARELO]] this project derives for a [[runner]] from their [[sheet
entry]] times, on the exact same 0-100 curve your own [[MARELO]] grades on —
what turns the [[Ultimate Sheet]] into a board you can climb against a
[[runner]]'s best time on each [[target]], not just your own. A [[runner]]
with no time on a rankable [[target]] contributes zero in that [[scope]]
slot. A [[target]] without a resolved [[Overall curve]] is absent from the
denominator, the same rule your own [[MARELO]] follows.

- **Lives** — the ratings bridge (`src/sm64_events/library/ratings.py`)
- **Not** — your own [[MARELO]], which only ever grades your practice
  history, never a [[runner]]'s.

### Rank board

Every [[runner]] who has practiced something in a [[scope]], plus you,
ordered by [[MARELO]] and numbered the same way [[Leaderboard mode]]
numbers its own list: a tie shares a position and the next skips ahead by
the tie's size. The board covers the entities your own rating covers — exclude an
entity from your own rating and the board drops it too. It drops a [[runner]] with nothing
practiced in this [[scope]] and always keeps your own row, even at zero —
but it COUNTS the drop rather than staying silent about it: it states how many it left off, so
the board can never read as "this is everyone" when it is not. Opening one
[[runner]]'s row lands on their [[Runner page]], which widens every entity
with your own score and time on it, so the two read side by side. On the
[[Rank tab]] it is its own card titled Leaderboard, sitting between the
[[scope]] chips and your [[MARELO]] card, closed until you open it and folding
open and shut the same way a [[Library tab]] section does.

- **Lives** — the board (`src/sm64_events/library/board.py`) → served at
  /api/leaderboard → drawn on the [[Rank tab]]
  (`src/sm64_events/ui/components/leaderboard.js`)
- **Not** — [[Leaderboard mode]], which re-orders one [[approach]]'s
  [[sheet entry]] list by raw time inside a single [[Library tab]] section;
  this orders every [[runner]]'s [[runner rating]] across a whole [[scope]]
  instead.

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

### Scorecard

The [[Rank tab]]'s grid of course cards — the reference sheet's own shape,
compact enough that one landscape screenshot shows your whole progress:
four aligned columns you read DOWN, so the cards follow the game's own
course order, closing with the castle secrets' card, and the Bowser fights'
card centred beneath the columns. The grid drops to two columns, then one,
before any [[star]]'s name would need a second line. Each card belongs to one
course (plus those two, the whole holding all 120 [[star]]s and the Bowser
fights),
wears its course's own colour and art, and prints one line per [[star]] or
[[segment]]: its icon, its full name, your [[personal best]], the current
[[Goal]]'s time, and the gap between the two — the icon and name together
open that [[star]]'s [[Library tab]] page, landing on the example closest
to the [[Goal]]'s time. The castle's Toad and MIPS [[star]]s sit out by
default (no community tracker counts them and no [[Standard]] grades
them); including one puts it back in the Secret card and the [[rank]]
alike. A [[route]]-shaped [[Scope]] buckets
that [[route]]'s entities into the same cards; a course-shaped one draws
that course's card alone. A course's [[100-coin star]] is not its own
line — its line combines it with a companion [[star]] (usually the course's
red-coin [[star]]; five courses pair differently), because the 100-coin
[[run]] collects that companion's objective on the way. A card's foot is
its Stage Sum: your lines added up, the [[Goal]]'s lines added up, and the
gap between the two counted only over the lines both sides have a time
for, with a chip saying how many of the card's lines that gap compares —
so wiping your data leaves the [[Goal]]'s sum standing while your column
and the gap go blank.

The [[Ultimate Sheet]] and the community's [[Standard]]s both file the
Under-21 slide [[star]] under the box [[star]]'s heading; the trainer splits
both sources so that [[star]] carries its own times and its own [[Ladder]].

- **Lives** — the card builder (`src/sm64_events/ranks/scorecard.py`)
  → the card (`src/sm64_events/ui/components/scorecard.js`)

### Daily Star

One [[Scorecard]] entry suggested for practice each calendar day, the same on
every install on the same local date: the date alone picks it, so no server
decides it. The rotation deals the Overall card's 103 default entries once
per 103-day cycle in a shuffled order where two days in a row never share a
course; Day 1 is 2026-09-23, Whomp's Fortress "Shoot into the Wild Blue". The
app announces it once per day in a modal showing your [[personal best]], the
time that reaches the next [[division]] above it, and the time left in the
day, which turns to the next day's [[star]] at midnight; its [[selector]] cell wears a
holographic foil. The rotation never ends: each cycle reshuffles. Its **today's best** is the fastest finished, uncleared
[[attempt]] on it that ended inside today, on the entry's [[Scorecard]] clock,
whatever the [[strategy]]; unlike a [[personal best]] it needs no save.

- **Lives** — the rotation (`src/sm64_events/ranks/dailystar.py`)
  → today's best and the seen date (`src/sm64_events/server/dailystar_api.py`)
  → the local date and midnight (`src/sm64_events/ui/dailystar.js`)
  → the announcement (`src/sm64_events/ui/components/dailystarmodal.js`)
- **Not** — a [[Goal]]: no [[rank]] or gap reads it.

### Runner page

One community [[runner]]'s own standing, read-only, reached by clicking
their name on a [[Rank board]] row or beside one of their times on the
[[Library tab]] — one screen either way, never two that draw it differently.
Your own [[Rank tab]]'s breakdown has the same door: an entity's name opens the
[[Library tab]] on that entity and lands on the subdivision your own
[[personal best]] sits in. The same [[scope]] chips and the same coverage strip your own [[Rank tab]]
draws, pointed at their [[runner rating]] instead of yours, and the same
breakdown table with its last three columns widened for comparison: their
time, your time, and the gap between the two — it keeps [[rank]] on each
entity the same as your own tab's. No control on it can change anything —
no excluding an entity, no repointing an icon. Its one door out: a lit
coverage tile, or an entity's name (icon included) in the breakdown, opens
the [[Library tab]] on that entity landed on this [[runner]]'s own [[sheet
entry]] for it, and the [[Rank tab]] afterwards returns to the same page.
A row whose entry has a video carries a play control that opens that video
right beneath the row. Exclude an entity from your own rating and this page drops it too — it
covers the same entities your [[Rank tab]] does.

- **Lives** — the page
  (`src/sm64_events/ui/components/runnerpage.js`), reached from
  `src/sm64_events/ui/components/leaderboard.js` and
  `src/sm64_events/ui/components/librarytarget.js`
- **Not** — the [[Rank tab]], the same shell around YOUR OWN [[MARELO]] —
  it grades nobody's [[rank]] but yours, and its breakdown table carries the
  Ignore/Include control this page never shows.

### Library tab

The screen for browsing every [[approach]] the [[Ultimate Sheet]] documents.
Opens straight on whichever [[star]] or [[segment]] you last practiced,
otherwise a course grid to browse into; open one and its [[approach]]es lay
out beginner to expert, each one's community times banded by the [[rank]]
they earn, and each [[rank]] band split into its five [[division]]s —
collapsed until you open the exact one you want to study. A search box above
the grid finds a [[star]] or [[segment]] by name as you type, matching its
[[approach]]es' names and the [[runner]]s who hold a time on it as well, and
its results stand in place of the grid until you clear it. A second box, on
the [[target]]'s own page, searches [[runner]]s instead: it keeps only the
[[approach]]es where that [[runner]] holds a time, opens every one it keeps,
and restores the whole page when you clear it. A [[sheet entry]] with a
video draws a playable card; one without draws a compact [[runner]]-and-time
row. The [[tray]] collects [[sheet entry]] rows to compare side by side. A
[[target]]'s page wears the [[wiki mark]] beside its name when Ukikipedia has
a page for it.

- **Lives** — the tab (`src/sm64_events/ui/components/library.js`,
  `src/sm64_events/ui/components/librarynav.js`,
  `src/sm64_events/ui/components/librarytarget.js`)
- **Not** — the [[Practice tab]]. The Library never sets your [[target]]; it
  only shows what the community has recorded.

### Resources tab

The screen pointing outward, to the community: its Discord servers, learning
videos, reference sites and sheets, the rules and settings a submitted time
must meet, and this project's own links. Each card is one row of a registry,
so reordering or adding a link is a data edit.

- **Lives** — `src/sm64_events/ui/resources.js` (the registry) drawn by
  `src/sm64_events/ui/components/resourcespage.js`
- **Not** — the official rules. Each rules block summarizes and links the
  page that is.

### About tab

The "?" screen pointing inward. It opens on Credits, naming the people,
projects and testers the trainer stands on; How it works, a step-through
lesson from playing to the time appearing on screen, is the other view.

- **Lives** — `src/sm64_events/ui/components/aboutpage.js`; the lesson's
  words in `src/sm64_events/ui/howitworks.js`; the credits and testers in
  `src/sm64_events/ui/credits.js`

### Tray

The Library's comparison basket. Adding an [[sheet entry]] docks it here, keyed on
its [[approach]], its [[runner]] and its time together — never on its clip
alone, since one video regularly stands as evidence for more than one
[[star]]. "Play all" plays every docked clip loosely together; Study in
Compare hands the whole tray to Compare's own transport instead, playing them
back in lockstep.

- **Lives** — the tray (`src/sm64_events/ui/components/librarytray.js`)

### Selector

The row of [[practice cell]]s across the top of the [[Practice tab]], showing
what you can practice where you currently stand. A course shows every one of
its [[star]]s the whole time you are inside it — walking into a subarea such
as the volcano or the pyramid changes nothing here. Clicking a cell sets your
[[target]]. The row draws [[star]]s and castle movements at all times, never
a [[subsection]] beside its parent — a piece has nothing of its own here at
all now; it shows up only in the [[practice log]], inside its parent's card.

- **Lives** — the quick-select row
  (`src/sm64_events/ui/components/stagebanner.js`)

### Segment recorder

The screen turning what you just played into a [[segment]]. It lists your recent
[[moment]]s newest first, keeps listing them while you keep playing, and asks
which [[target]] the result is a piece of — which is the only way to make a
[[subsection]]. Picking two [[moment]]s defines the ends and the [[journal]]
fills the middle from where you walked; picking more says which stops count.
**Add another start** marks other recorded [[moment]]s as OR alternatives. Any one
can begin the same ordered steps and finish, even when its sample came from
another [[attempt]]. A marked alternative is no longer a required intermediate step.
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

The table of every [[standard]] for one [[target]], read slowest-first like
the Library, each row wearing the [[rank icon]] of its top [[division]]. A
row expands into the five [[division]] thresholds inside its [[rank]], and
every time links to the fastest watchable [[sheet entry]] inside its band —
the click lands on that exact [[sheet entry]]'s card in the Library, which
blinks on arrival.

- **Lives** — the standards panel
  (`src/sm64_events/ui/components/standards.js`)

### Sheet best

The fastest [[sheet entry]] anybody holds for one [[strategy]] — the last row
of the [[standards ladder]], under every [[rank]] rather than in it. The
trainer names it for what it is: the top of a [[ladder]] is not the top of the
sport, and the [[Ultimate Sheet]] proves only that nobody there has gone
faster.

- **Not** — a [[standard]]. A [[standard]] is a time the trainer grades you
  against; a sheet best grades nothing and wears no [[rank icon]].

- **Lives** — the library examples
  (`src/sm64_events/library/examples.py`)
  → the last row of the [[standards ladder]]

### Game version

Which regional release of Super Mario 64 you tell the trainer you are playing
— JP or US — or Auto-detect, which lets the trainer decide. Every
[[standard]] with a JP time annotated differs by version, so the game version
picks which [[ladder]] the trainer grades you on. Changing it applies at
once, no restart.

- **Lives** — the mode config (`src/sm64_events/core/modes.py`); its API
  door (`src/sm64_events/server/mode_api.py`); the Settings drawer's Game
  section (`src/sm64_events/ui/components/header.js`)

### Region switch

The two-flag control that says which regional releases a surface shows at
once — JP, US, or both, and never neither. It draws each release as its
country's flag rather than the two letters, and it means something different
on each surface that wears it: on the [[library tab]] it decides which
[[sheet entry]] rows appear (both by default, so no community time hides),
and on the [[scorecard]] it decides which regions BOTH sides of a tile may
draw a time from — which regions a [[runner]] [[goal]] may offer, and which
of your own [[personal best]]s count as yours (your [[effective version]] by
default, and the faster of the two when both are on). Both sides read the
same choice so that a [[runner]] whose times you imported grades at exactly
zero however you set it; a [[personal best]] naming no release counts under
either choice, since nothing recorded which release produced it.

- **Not** — the [[game version]]. The [[game version]] decides what the
  trainer grades YOUR [[attempt]]s on; a region switch only widens or narrows
  which releases a surface draws times from.

- **Lives** — the control itself
  (`src/sm64_events/ui/components/versionswitch.js`) and the flags it draws
  (`src/sm64_events/ui/components/regionflag.js`)
  → the [[library tab]] and the [[scorecard]]

### Effective version

What the [[game version]] resolves to — always JP or US, never Auto-detect.
An explicit choice wins; Auto-detect follows the cartridge the emulator is
running, keeps the last one seen when it detaches, and is US before any.

- **Lives** — the mode config's resolver (`src/sm64_events/core/modes.py`)
  → the session view (`src/sm64_events/tracking/views.py`)

### Grading version

The [[effective version]] as the [[standard]]s file holds it: every
[[ladder]] read that names no version resolves on it, which is how every
[[rank]] on every surface follows the [[game version]] without any surface
knowing about it. A [[version switch]] names a version explicitly and so
never moves it.

- **Lives** — the standards file (`src/sm64_events/ranks/standards.py`)

### Version switch

The JP / US toggle — JP on the left, US on the right — that shows the other
version's [[ladder]]s without changing which one the trainer grades you on.
It picks exactly ONE version, because a [[ladder]] table draws one version's
cutoffs or none: the [[standards ladder]]'s toolbar wears it and re-fetches
that [[target]]'s [[ladder]]s under the version you press. It defaults to
the [[effective version]] and names the graded version whenever it shows the
other.

- **Not** — the [[region switch]], which picks one version or BOTH and
  changes what a surface LISTS rather than which cutoffs it draws.

- **Lives** — the shared control
  (`src/sm64_events/ui/components/versionswitch.js`); mounted by the
  standards panel (`src/sm64_events/ui/components/standards.js`)

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

### Timer hint

The warning triangle left of a [[practice log]] row's entry number; hover
it for why the game's timer showed a different time.
The log records the [[x-cam]] time whatever STOP setting Usamune uses, but
Usamune's own timer shows that number only under GrabX or Xcam. The trainer
hints a [[star]] row grabbed under another setting while the newest
grab that reported a setting still did, so every hint leaves once you grab
one [[star]] under GrabX.

- **Not** — a [[caveat mark]]. A [[caveat mark]] says the row's time is not
  the quantity it looks like; a hinted row's time is right, and the hint is
  about the number the game shows. It is a muted triangle, never the
  orange round "!" of a [[caveat mark]].
- **Lives** — the rule (`src/sm64_events/tracking/views.py`)
  → the symbol (`src/sm64_events/ui/components/attemptlog.js`)

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
[[caveat mark]] and its own [[attempt]]s. A [[subsection]] draws inside its
parent's card rather than beside it, indented and folding with it, so the
piece you just ran names the [[star]] it belongs to. It highlights the card
for whatever you are practicing right now, and keeps one card open: the
[[target]] you picked, or else the newest thing you played, so when the game
moves the [[target]] for you the card you were reading stays open until the
new [[target]] records its first [[attempt]]. A Bowser course's Reds/Pipe pair is
the worked example: the reds [[star]] is a [[subsection]] of its paired
pipe-entry [[segment]], so grabbing it draws its own card nested inside the
movement's rather than beside it — never a toggle hiding either half.

- **Lives** — the log
  (`src/sm64_events/ui/components/practicelog.js`)

### Book mark

The icon on a [[practice log]] card that opens the [[Library tab]] at
whatever [[target]] and last-played [[strategy]] the card's own [[standards
ladder]] actually grades against — its own [[target]], except a paired
Bowser reds card, which opens its paired [[star]] instead. A caller with
nowhere to send it omits the icon outright, never a disabled one with no
doorway behind it.

- **Lives** — the doorway (`src/sm64_events/ui/components/practicelog.js`)
  → the [[Library tab]]

### Wiki mark

The small Ukiki face beside a [[target]]'s name on the [[Library tab]] — Ukikipedia's own icon — that opens that
[[target]]'s RTA Guide page in the browser. It draws only where the wiki has
the page: the link registry resolves a [[star]]'s or [[segment]]'s identity,
else the sheet label's own name (LBLJ, Lakitu Skip, MIPS, a Toad detour),
against a copy of the wiki's page list, and a [[target]] the wiki never
wrote up gets no mark rather than a dead one. The detail drawer's "RTA Guide"
link reads the same registry.

- **Lives** — the link registry (`src/sm64_events/links.py`) → the mark on
  the [[Library tab]] (`src/sm64_events/ui/components/librarytarget.js`)
- **Not** — the [[book mark]]. The [[book mark]] leads INTO the Library from a
  practice card; the wiki mark leads OUT of it, to Ukikipedia.

### Leaderboard mode

A second reading of an [[approach]]'s [[sheet entry]] list on its [[Library
tab]] section — every entry ranked fastest first and numbered, instead of the
default reading's bands split by [[tier]] and [[division]]. Each entry is one
condensed line — position, [[rank]] cap, [[runner]], time — with a play
control where the entry has a video; the video stays folded beneath the line
until you open it. Two entries tied
on time share a number and the next distinct time skips ahead by the tie's
size. Your own [[personal best]] inserts itself at the position it actually
earns among the community's, read off the [[rank]] the section already
grades you at rather than fetched again. A per-section switch swaps between
the two readings; the default reading never changes, and the pick lives in
the section's own component state, so closing and reopening the section
keeps it — only navigating away from this library page returns it to the
default reading.

- **Lives** — the mode switch and the flat list
  (`src/sm64_events/ui/components/librarytarget.js`)

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

The control for choosing which [[strategy]] your next [[attempt]] uses. It
keeps your pick on screen from the instant you make it until the trainer's own
answer carries it, so nothing that repaints in between can show the old value.

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

### Goal plate

The box in a [[practice log]] card's head holding its [[personal best]] over
its [[Goal]], and under them one dot per success when that [[Goal]] asks for
a streak or a count. Clicking anywhere on it but the PB time (which jumps to
that [[personal best]]'s row in the log) picks a [[rank]] or a [[runner]] or a typed
time, and that pick pins the [[target]] on the [[Scorecard]] too. The plate
keeps one size whatever it says, so a stream crop of the top card never moves,
and its [[Celebration]] plays inside it. A finished [[attempt]] celebrates only
against the [[Goal]] the plate showed when it ended; a [[Goal]] it has replaced
never fires again.

- **Lives** — the plate and its picker (`src/sm64_events/ui/components/goalplate.js`)
  → the pb track of the [[practice log]] card
- **Not** — the [[Stream overlay]], which draws the same answer as its own page.

### Stream overlay

The page a streamer adds to OBS as a Browser Source: the current [[target]]'s
[[personal best]] over its [[Goal]], a tally of this sitting's successes when
that [[Goal]] asks for a streak or a count, and a [[Celebration]] drawn inside
the same box when it falls. Every setting lives in its URL, which the designer
in Settings → Streaming writes.

- **Lives** — the page (`src/sm64_events/ui/goal-overlay.html`,
  `src/sm64_events/ui/goaloverlaypage.js`), its settings and progress rules
  (`src/sm64_events/ui/goaloverlay.js`), the designer
  (`src/sm64_events/ui/components/streamoverlay.js`)

### Tuning inspector

A live control panel for anything judged by feel — timings, easing, layout
weights. You tune it while it plays and press save, and what you saved becomes
what ships.

- **Lives** — the tuning page (`src/sm64_events/ui/tune.js`)

---

## What runs

### Poller

The loop reading the emulator: it looks at the pad 250 times a second, on its
own high-resolution timer, and once per game [[frame]] takes a [[snapshot]],
hands consecutive pairs to every [[detector]] and publishes whatever they emit. It
serves only a [[practice ROM]]: it reads the cartridge header when it attaches,
whenever the game timer goes back and at least once a second, so swapping ROMs
in Project64 needs no restart.

- **Lives** — the poll loop (`src/sm64_events/server/poller.py`)

### Snapshot

One coherent read of all the game state the [[detector]]s need, taken at
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
become a clip without you having recorded anything deliberately. Its camera
is the [[capture layer]] once you have installed it and Project64 presents
through it, and the desktop grab otherwise.

- **Lives** — the recorder (`src/sm64_events/replay/recorder.py`)

### Instance lock

The guard letting one server own one [[store]]. A second server keeps
broadcasting but writes nothing, so two of them polling one emulator can never
record every [[event]] twice.

- **Lives** — the lock (`src/sm64_events/storage/instance_lock.py`)

### Debug report

One markdown file capturing what the trainer was doing when something went
wrong: capped tails of the server log, the [[journal]], the [[UI log]] and the
perf samples, plus the server's health readout, install facts, recognized native
log records and [[ghost]] playback state. The builder omits private paths and
custom display labels. The Debug report
button in the settings drawer writes one and opens Explorer with it selected,
so a user can attach evidence to a bug report instead of a description of it.
The first automatic error of each issue per launch carries the same report
as an attachment, which the upload worker builds after the SDK captures the
error.

- **Lives** — the report builder (`src/sm64_events/core/diagnostics.py`) → the
  settings drawer (`src/sm64_events/ui/components/header.js`) and the error
  upload worker (`src/sm64_events/core/error_reporting_transport.py`)

### Memory layout

WHERE one build of the ROM keeps each global the trainer reads — one row per
address, US and JP side by side. The US column is what every [[detector]] has
read since the first live check; the JP column starts empty and fills only
after its [[gate]] passes and the [[sync report]] says so. A reader takes the
memory layout for the running version and adds the struct offsets that are the
same on every ROM. Nothing outside the memory layout may spell a RAM address.

- **Lives** — the memory layout (`src/sm64_events/memory/layout.py`) → the
  [[snapshot]] reader and every memory-reading tool under `tools/`

### Behaviour symbol

The decompilation's own name for what a thing IS — `bhvDoor`, `bhvBobomb` —
used as the identity behind every [[landmark]] and every caused [[moment]],
because the pointer the game keeps for that script differs between the US
and JP ROMs and the symbol does not. Both versions' symbol tables ship with
the app; the trainer resolves a pointer to its symbol on the very read that finds it.

- **Lives** — the symbol tables (`src/sm64_events/memory/behaviours.py`) →
  the [[landmark]] key (`src/sm64_events/core/landmark.py`)

### Gate

One check that a version-dependent thing works on the ROM the emulator holds:
the instruction to the player, the read over live memory, and what a pass
proves. Four kinds — an address's contract, the behaviour table's base, a
calibration measured beside the constant it backs, and a whole feature driven
through the real [[detector]] chain. Every [[memory layout]] row, every kind
of [[event]] a [[detector]] emits and every measured constant must have one,
or the test suite fails; that is what keeps a feature built on US from
landing without its JP step.

- **Lives** — the registry (`src/sm64_events/sync/gates.py`) and the gate
  modules beside it → the [[sync runner]] and the [[sync dashboard]]

### Sync runner

The one script that walks every [[gate]] in dependency order against the
loaded ROM, tells the player what to do, and writes each verdict into the
[[sync report]] as it lands — while a live server relays it to the
[[sync dashboard]]. On US it proves nothing regressed; on JP it is how the
player finds and confirms JP support.

- **Lives** — the sync runner (`src/sm64_events/sync/runner.py`) and its
  command (`tools/sync_version.py`)

### Sync report

Every [[gate]]'s latest verdict for one ROM version, on disk and committed
as evidence: verified, failed, candidate, missing or skipped, with the value
or the measurement and one line of proof. A verified address in the report
that the [[memory layout]] does not yet carry fails the test suite, and so
does a shipped address the report refutes.

- **Lives** — the sync report (`src/sm64_events/sync/report.py`) → one
  JSON per version under the data folder's version_sync directory

### Sync dashboard

The page that shows US beside JP for every [[gate]], grouped by feature with
a coverage bar per feature, moving live as the [[sync runner]] posts each
verdict through the server. On the first day every JP cell reads missing;
it fills as the player plays through the [[sync runner]]'s checklist.

- **Lives** — the page (`src/sm64_events/ui/sync.html`,
  `src/sm64_events/ui/sync.js`) → the sync API
  (`src/sm64_events/server/sync_api.py`)

### Version probe

The read that names which ROM the emulator has loaded — the cartridge
header's country byte — so the [[sync runner]] refuses to check JP against a
US ROM and the game-version setting's automatic mode has something to detect
from.

- **Lives** — the version probe (`src/sm64_events/memory/version_probe.py`)

### Chain file

One value's whole path through the trainer, hop by hop: where the value holds
true at each hop, which module owns it there, the probe that reads it, the way
to force a known-good value in, and what that probe prints when the hop breaks.
A bug report names the last hop — what he saw — so the cause sits at the
earliest hop where the value stops holding true, and a fix below that hop
cannot hold.

- **Lives** — the chain files (`.claude/rules/chain-star-grab-time.md`) → the
  test running the machine-wide chain checker over them
  (`tests/test_chains.py`)

### Blast radius

The tests one change can affect, chosen from the diff since the newest main
commit whose [[full run]] passed: the tests that executed a Python code block
the change altered, the tests naming a changed UI module, its importers, a
class it renders or its page's tab, and for a stylesheet only the rules that
changed. It prints the reason for every group it picks. Past 20 browser files
it keeps the startup canary and the pages that name the change and leaves the
rest to the [[full run]].

- **Lives** — the radius rules (`tools/blast_radius.py`) → the test door, which
  lists them on request (`tools/run_tests.py`)
- **Not** — the whole suite, which only the [[full run]] executes.

### Merge check

The local check before a merge: the [[blast radius]] of the change, and
nothing else. The test door takes one of two slots for it, so a third merge
check waits while a focused check, which executes exactly the tests it names,
never does.

- **Lives** — the test door (`tools/run_tests.py`) → the wrapper the harness
  calls before a merge (`tools/verify_full.py`)
- **Not** — the [[full run]], which covers everything the merge check leaves
  out.

### Full run

The whole test suite, browser tests included, executed by GitHub Actions on
every push to main and split into parallel jobs. It blocks only a release: the
release script refuses any commit it has not passed. Its newest green result on
main is where the next [[blast radius]] starts, and its nightly scheduled
execution records the coverage map the radius reads. A test that fails there
gets one more try, alone, after the suite; passing it makes the test FLAKY,
reported rather than red.

- **Lives** — the workflow (`.github/workflows/full.yml`) → the reader
  (`tools/full_run.py`) → the release script (`tools/release.py`)
- **Not** — the [[merge check]], which executes locally and must pass before a
  merge.
