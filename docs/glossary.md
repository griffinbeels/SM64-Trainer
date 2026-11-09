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

### Save this moment

The [[Ghost Lab]]'s **Save states** row: one press of 1 to 9 makes a Usamune save state of the [[frame]] on screen
and writes it to that slot of the running game. Usamune's menu shows the same numbers. The lab plays SM64's own code for
that one [[frame]] from what the recording kept of the [[frame]] before, then lets Usamune's own save routine make the
slot, and checks the rest of the [[frame]] against the recording. A [[frame]] Usamune could not save on makes no state, and
the row says why. So does a game in another level, or a level that loaded in another way. If Mario sits in another
place in the game's memory, the write uses [[mario relocation]] first. A recording made before
this feature lacks a few small values, so the row says it may not be exact. A state the row writes joins the
[[slot library]]. After a write the row shows the flag of the version the save state came from; one written into
the other version's game says so in words ([[save state translation]]).

- **Lives** -- `tools/scene_lab/moment_slot.py`, `src/sm64_events/server/scene_moments.py`,
  `src/sm64_events/ui/components/scenemoment.js`; lab only
- **Not** -- a [[kept state]] saved by the game itself

### Slot Library

Every Usamune save state the Trainer has seen, kept on disk so it can come back.
You can keep more than nine, and only nine sit in the game at a time. Saving
over a slot in the game loses nothing, because the old [[kept state]] stays
here. Nothing in the library deletes a state on its own.

- **Lives** -- `src/sm64_events/slotlibrary/`, in the app's user data folder
- **Not** -- a [[save-state load]], which is the game loading one

### Kept state

One save state in the [[slot library]]: the slot's exact bytes, packed, with a
name, level, area, Mario's object word, the Usamune build it came from, the time
the Trainer first saw it and its source: captured, imported or built. Its id is a
hash of its bytes, so the same bytes are always one kept state.

- **Lives** -- `src/sm64_events/slotlibrary/store.py`

### Course set

For one course, which [[kept state]] goes into which of Usamune's nine slots.
Each course has a course default, and each [[star]] of it can have a set of its
own. That set's slots replace the course default's at the same numbers; the
other slots keep the course default. When you enter a course, the Trainer writes
the course default, or the set of your [[target]] when that [[star]] is in this
course. Picking or grabbing a [[star]] there writes its set. A slot that held
another set's state of this course becomes empty, and anything else stays. When
you save into a slot yourself, that save joins the set you are in. The Trainer
first keeps every occupied slot, then writes. Each state goes through [[mario
relocation]] on the way in. Every set starts empty.

- **Lives** -- `src/sm64_events/slotlibrary/coursesets.py` (the sets),
  `src/sm64_events/slotlibrary/keeper.py` (which set applies, and when it is
  written); lab only

### Mario relocation

Usamune refuses to load a save state unless Mario sits in the same place in the
game's memory as when Usamune saved it, and a practice [[reset]] often moves him. Before
the Trainer writes a state, it moves Mario inside that state's own bytes to his
place in the running game. Every link to Mario, and to the object that sat
there, moves with him. The game's code and Usamune stay unchanged. The
[[kept state]] keeps its old bytes. A state the Trainer cannot read all the way
through is not written.

- **Lives** -- `src/sm64_events/slotlibrary/relocate.py`; every write runs it
- **Not** -- changing Usamune's check, which Griffin ruled out (2026-11-05)

### Save state translation

A save state made by Usamune US, written into a game running Usamune JP, or the other way round. Both versions save
the same bytes in the same order. Only the addresses inside differ, so the Trainer rewrites each address to the place
the other version keeps the same thing. The game must stand in the save state's level and area. The Trainer leaves
out an object the other version does not have, and the page says what is there instead. The Trainer writes nothing
when an address has no place in the other version. Then the Trainer moves Mario with [[mario relocation]], as for
any save state. The [[kept state]] keeps its own version's bytes.

- **Lives** -- `src/sm64_events/slotlibrary/translate.py`; every write path calls it when the versions differ
- **Not** -- a change to the game or Usamune: the data changes, the code does not

### Atlas

For one level and area, which address in the US game holds the same level data as which address in the JP game. The
Trainer makes one the first time a [[save state translation]] needs it, by starting both games from their cartridges
in its simulator, about 10 seconds. It keeps the atlas beside the [[slot library]]. Making one needs both ROMs.

- **Lives** -- `src/sm64_events/slotlibrary/atlas.py`, built by `tools/scene_lab/slot_atlas.py`; lab only

### Slot refit

After every spawn (a practice [[reset]], entering a level or an area), the Trainer gives each Usamune save state
made for that same level and area Mario's new place, using [[mario relocation]]. Then Usamune's own load works
without a visit to the [[Ghost Lab]]. States for other places stay as they are, and a state already in place is
not written again. The pass skips a state he saves over while it works. Before its first refit, his save joins the
[[slot library]]. Lab only.

- **Lives** -- `src/sm64_events/slotlibrary/refit.py`; each pass writes one line to its own log file
- **Not** -- something the game did, so it never enters the [[journal]]

### Share file

One [[kept state]] as a `.sm64state` file you can send to a friend. Opening one
adds it to the [[slot library]] and writes nothing to the game. The Trainer
refuses a damaged or oversized file, and one from another Usamune build. While
the game is running, the Trainer keeps a file from the other version (US or JP),
because [[save state translation]] can place it.

- **Lives** -- `src/sm64_events/slotlibrary/sharefile.py`

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
that broke one. An [[attempt]] touched by a [[save-state load]] never counts
toward any goal, never breaks a streak and never plays a goal
[[Celebration]] (2026-11-09).

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

### Attempt window

The unsaved [[attempt]]s whose replay video the trainer keeps. It keeps the
newest ones and stops before either limit breaks: 5 [[attempt]]s by default, or
10 minutes of footage by default. A [[reset]] counts as one [[attempt]], so a
player who keeps resetting holds very little video. The newest finished
[[attempt]] always stays, and the one in progress never expires. A [[star]] and
a [[segment]] closed by the same play share one place and count their time once.
Saved replays and PBs sit outside it. The trainer deletes older unsaved footage.

- **Lives** -- `src/sm64_events/replay/attemptretention.py`; the recording panel
  edits both limits (`src/sm64_events/ui/components/replay.js`).

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

The trainer's recording hooks inside its graphics plugin, `sm64_trainer_gfx.dll`
(`SM64 Trainer v1.0` in Project64's list), which also holds the [[renderer]]
(2026-09-04, his ask: "redo our recording system so that the recording
[[frame]]s ARE game [[frame]]s"). When the [[renderer]] finishes a picture the
capture layer copies the game's own memory -- the [[frame]]
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
the [[setup screen]] (one file, `sm64_trainer_gfx.dll`, into Project64's Plugin
folder, and the `Graphics Dll` setting that selects it) and undoes it from the
same screen, selecting the plugin you had before; without it the [[recorder]]
photographs the desktop as before. The [[setup screen]] decides "current" by the
build id the plugin carries (bytes only for a file without one), so a rebuild
of unchanged sources never asks for a reinstall. An app update refreshes the installed plugin by
itself once you close Project64, under the consent already given; onboarding
never reopens for it (his rule, 2026-09-16). An older install put a wrapper
under the same name that loaded the [[renderer]] from a second file named by
`sm64_trainer_gfx.ini`. It reads as stale, not broken: the same refresh
replaces the DLL, then deletes those files of ours, so Project64's choice holds
and nobody picks a plugin again (2026-11-07).

- **Lives** -- the capture code in `plugin/gfxwrap/`, compiled with the
  [[renderer]] into one DLL through `tools/build_natives.py` (never committed)
  into the git-ignored bundle folder (its BUNDLE constant in `tools/build_natives.py`) beside
  the [[encoder helper]]; `src/sm64_events/core/capturelayer.py`
  installs and undoes it, and `src/sm64_events/core/plugin_installation.py` names
  the installed file and an older install's leftovers;
  `src/sm64_events/replay/sourcefactory.py` selects
  the [[recorder]]'s camera; `src/sm64_events/replay/gpucapture.py` owns the
  capture path
- **Not** -- the desktop grab (`src/sm64_events/replay/video.py`): that
  photographs the window ~120 times a second and the trainer must then work
  out which [[frame]] each photograph shows.

### Renderer

The trainer's build of LINK's GLideN64 v4.2: the pinned upstream tree plus the
capture overlay that adds the SourceV2 export the [[capture layer]] reads. It
ships inside the graphics plugin `sm64_trainer_gfx.dll`, together with the
[[capture layer]]: one file that Project64 loads itself, so the trainer loads
nothing by name and Project64's picker lists one plugin. Until 2026-11-07 it was a
second file that a capture wrapper loaded by name (`GLideN64_SM64Trainer.dll`,
then `sm64_trainer_gfx.renderer`); Defender flagged that file as a trojan on
2026-11-06, and Griffin chose one file: the easiest setup for players and the
least likely to look like malware. GLideN64 is
GPL-2.0; the About box credits its authors and links the published source. The
renderer keeps GLideN64's own settings identity, so a user's existing GLideN64
settings carry over.

- **Lives** -- `tools/build_renderer.py` builds it from the pin in
  `renderer/build-inputs.json` and the overlay in `plugin/gfxwrap/`
  (`renderer/README.md` has the provenance and credits) into the graphics
  plugin in the git-ignored bundle folder (its BUNDLE constant in
  `tools/build_natives.py`), a build output that tool refreshes at launch. The
  [[Ghost Lab]]'s helpers load that bundle file where it is, never a copy,
  and point it at the [[renderer settings folder]]
- **Not** -- the graphics plugin the user had before; that one stays in the
  folder untouched, and "Remove Practice Replay" selects it again

### Renderer-only mode

The graphics plugin running as its [[renderer]] alone, for the [[Ghost
Lab]]'s helpers. They call `SM64RendererOnlyV1` before anything else; the
[[capture layer]] then publishes no control page, captures nothing and writes
no log, so a helper never answers as a running Project64's plugin.

- **Lives** -- `plugin/gfxwrap/gfxwrap.c` (the contract), loaded by
  `tools/scene_lab/renderer_load.h`
- **Not** -- a setting a player can turn on; Project64 never calls it

### Test plugin

A `sm64_trainer_gfx.dll` built for the host tests: the real [[capture layer]]
linked with a stand-in for the [[renderer]].

- **Lives** -- `tools/build_plugin.py`, which builds it for the host tests
- **Not** -- shipped; it never enters the bundle

### Renderer settings folder

The folder where the [[renderer]] reads its settings when the scene lab's
helpers draw with it outside Project64: a copy of Project64's `GLideN64.ini`
and `GLideN64.custom.ini` with the shader cache turned off, so the
[[renderer]] keeps nothing there from one start to the next but its log. There
is one folder per window size in the scene lab's own folder (`project64` keeps
Project64's own size, `1600x1200` names another), and a helper reuses the same
folder every time. A helper names it to the [[renderer]] before its first call,
and only the Trainer's [[renderer]] accepts one. Inside Project64 nothing names
one, so the [[renderer]] reads Project64's Plugin folder as GLideN64 always has.
Before 2026-11-06 each helper copied the [[renderer]] into a new random folder
under `%TEMP%` and loaded the copy. Defender flagged that copy as a trojan: a
program loading a DLL it has just written into a temporary folder looks like
malware.

- **Lives** -- on disk under %LOCALAPPDATA%\SM64Trainer\scene-lab\renderer-settings;
  made by `tools/scene_lab/replay.py` (its settings_folder function); the
  renderer's setter `plugin/gfxwrap/renderer_settings_folder.cpp` (staged into
  GLideN64's folder lookup by `tools/build_renderer.py`); the helpers' loader
  `tools/scene_lab/renderer_load.h`
- **Not** -- Project64's Plugin folder, which the scene lab only reads

### Source date

The time of the newest commit that touched a shipped native's inputs: its
sources, its build recipe and `tools/native_identity.py`. The build writes it
into the PE header, where a linker would put the time of linking, and names
the FileVersion after it (year.month.day.hhmm, UTC). Builds are repeatable
since 2026-11-06, so two builds of one commit carry the same date and the same
bytes, and a later native commit carries a larger date. The [[setup screen]]'s
automatic refresh orders the installed graphics plugin by it and
never installs an older one; a build that names SM64 Trainer in its version
resource counts as newer than any build from before. Uncommitted edits keep the last
commit's date while their content still changes the build id.

- **Lives** -- `tools/native_identity.py` finds and writes it;
  `src/sm64_events/core/plugin_ownership.py` orders installs by it
- **Not** -- the build id: that digest says which inputs made a file, never
  which of two files is newer

### Defender gate

The Microsoft Defender scan our own natives pass before they reach the bundle
or a release. On 2026-11-06 Defender quarantined a [[renderer]] built here as
Trojan:Win32/Bearfoos.A!ml, a cloud machine-learning verdict on one build.
`tools/build_natives.py` now scans every native it installs, stops on a
threat, names the file and removes it from the bundle and the build cache. The
release and beta builders scan the built app again and also refuse a scan
that could not finish. Each scan reports without quarantining or deleting
anything, and nothing here changes Defender's settings. Off Windows or without
Defender's scanner it says so and the build continues.

- **Lives** -- `tools/defender_scan.py`, called by `tools/build_natives.py`,
  `tools/build_exe.py` and `tools/build_beta.py`
- **Not** -- code signing: the natives ship unsigned, his decision on
  2026-11-06

### Third-party source archive

One of the two tar files every release attaches beside its installer:
`SM64Trainer-third-party-source-plugin.tar` (the sources our build tools make
the [[renderer]] plugin from: the pinned GLideN64 tree, Qt, FreeType, libpng
and zlib) and `SM64Trainer-third-party-source-ffmpeg.tar` (the sources of the
bundled ffmpeg.exe and of PyAV's FFmpeg). Each carries a `.sha256`. The
compiler's own runtime (GCC's, winpthreads, the MinGW-w64 C runtime) stays
out: the GPL v3 counts it as a System Library, and its notices ship in the
app instead. The GPL asks that the complete source come
from the same place as the program; these are that source, byte for byte the
pinned archives. Since 2026-11-08 the trainer builds its own ffmpeg.exe so
that one archive covers it.

- **Lives** -- `licenses/third-party-sources.json` pins every archive;
  `tools/corresponding_source.py` writes them; `tools/release.py` attaches
  them; `tools/build_ffmpeg.py` and `tools/build_renderer_libs.py` build
  from the same pins
- **Not** -- the trainer's own source: that is the repository at the
  release's tag

### Task closure

Every byte of game memory the [[renderer]] reads while it draws one SM64
graphics task: the display lists, vertices, matrices, lights, viewport,
texture and palette loads, and the microcode it identifies. The closure walker
follows the task the way the [[renderer]] does and marks those ranges; it
refuses a task it cannot follow rather than guessing. The [[renderer]] redraws
a task from its closure alone, with every other byte overwritten, and
reproduces the original picture pixel for pixel (M4 experiment E0,
2026-10-24): a faithful recording can carry the closure instead of the whole
memory.

- **Lives** -- the walker (`plugin/gfxwrap/scene_closure.h`); the proof tools
  (`tools/scene_lab/closure.py`, `tools/scene_lab/replay.py`)
- **Not** -- the stamp the [[capture layer]] copies for each picture: a few
  fields that identify the picture, not what it draws

### Scene tap

An M4 experiment inside the [[capture layer]] that writes
each graphics task's [[task closure]] and the picture Project64 then shows.
It records only while the Trainer's scene lab holds a lease, continuously
into the [[live spool]]; a file appearing never starts it. (The one-shot
arming file that records a few seconds works only in the offline test host
since 2026-11-06.) Without a lease it costs one memory read per callback. It copies at the instant the game submits the task into a fixed
ring and never makes the game wait; a full ring drops and counts the record.
`tools/scene_lab/tap.py` arms it, reads what it wrote and turns each task into
input the [[renderer]] can redraw offline.

- **Lives** -- the tap (`plugin/gfxwrap/scene_tap.cpp`), called from the
  [[capture layer]]'s plugin entry points; its tool (`tools/scene_lab/tap.py`)
- **Not** -- the [[recorder]], which encodes video of play; the scene tap is
  an M4 probe that writes nothing unless armed or leased

### Live spool

The [[scene tap]]'s continuous recording while the Trainer's scene lab is on:
every graphics task with its [[scene state]], on disk, so you can open the
scene of an [[attempt]] right after it ends. The [[Ghost Lab]] shows whether
it records and has the **recording switch** that stops and starts it (kept for
the next start). While the switch is on the lab holds a lease by renewing a
file every 2 seconds; the [[capture layer]] checks it once a second, starts recording
without reopening the game, and stops within a second of the switch going off
or 8 seconds after the last renewal. The writer thread starts a new stretch of
the recording about every 15 seconds with the level state whole, so a reader
decodes any recent stretch without the ones before it. The stretches go into a
fixed set of 64 **spool files**, each reused in turn and never deleted, so the
spool keeps about the last 16 minutes in at most 2 GiB. It leaves out 256-byte
blocks a stretch already holds and compresses the rest with the Windows
Compression API: about 37 MB a minute of play (2026-11-06). An index names
each task's [[frame]], level, area and time a few dozen milliseconds after the
game drew it, and names the tasks a full ring left incomplete.

- **Lives** -- the lease and the reader (`tools/scene_lab/live_spool.py`); the
  capture and the lease check (`plugin/gfxwrap/scene_tap.cpp`); the spool
  files (`plugin/gfxwrap/scene_spool.cpp`); the switch
  (`src/sm64_events/ui/components/scenerecording.js` and the recording route
  in `src/sm64_events/server/scene_lab_api.py`)
- **Not** -- the [[recorder]]'s video, which keeps pictures only; the live
  spool keeps what the [[Ghost Lab]] needs to redraw from a free camera

### Scene state

The game memory SM64's own scene-graph code reads to build one task's display
list: the game globals and the object pool, and the main pool where a level's
scene graph, surfaces and models live. The menu that picks a course's act also
reads the save buffer (the course's coin score) and Usamune's settings and
counters (its hook prints a counter there). The [[scene tap]] copies it with
each task: the globals, the object pool and those two spans whole every time,
the tail of the task's own graphics pool (where the update leaves the
floor-aligned matrices the drawing places walkers with), the main pool whole
at each new level and then only its blocks that changed. A few callbacks draw
from a value and then step it
(painting ripples, the camera's field of view and its shake, title and menu
counters); the copy holds the stepped value, so the value the drawing used is
the previous [[frame]]'s copy. Running the game's own code
offline over the ROM, a task's [[task closure]] and its scene state, with
every other byte overwritten, rebuilds that task's display list byte for byte,
and a moved camera reads nothing else (M4 experiment E2, 2026-10-24, on two
savestates). That is what lets a recording be re-viewed from a free camera.

- **Lives** -- the spans (the scene, main-pool, save-buffer and Usamune-state
  rows in `src/sm64_events/memory/layout.py`); the copy (`plugin/gfxwrap/scene_tap.cpp`);
  the rebuild and the proof (`tools/scene_lab/tap.py`,
  `tools/scene_lab/e2_georender.py`, its from-tap and from-package modes);
  its portable form (`tools/scene_lab/package.py`, the scene-state layer)
- **Not** -- the [[task closure]], which is what the [[renderer]] reads to
  draw a list; the scene state is what the game reads to make one

### Sound stems

An M4 experiment's music track and effects track, split from the sound a
recording played without guessing. Every VI the game hands the RSP one audio
task: a list of mixing commands over its notes. The [[scene tap]] copies the
audio state at each VI while that task still waits, so a recording holds
every task's whole input and, at the next VI, the samples Project64's RSP
wrote. Offline, the ROM's own audio microcode executes each task again in an
RSP interpreter. A stem is the same task with the other side's notes still
decoding but mixing into a scratch buffer. A note belongs to the sequence
player that owns it: the level music and jingle players make the music,
the sound-effect player the effects, Mario's voice included. Each stem is
what the game played for those notes, voice stealing included, and the two
sum to the mix within a few units of the 16-bit scale. On savestates the
copy is complete: executing it again reproduces every task bit for bit.
Project64 itself executes the microcode on its RSP plugin's recompiler, which
rounds the vector math its own way, so a stem matches what Project64 played
to within that rounding (24 to 44 dB below the sound, 2026-10-27), not bit
for bit; its interpreter setting matches exactly.
While the lab is on, the [[stem renderer]] makes them for every VI of live
play, and an [[attempt scene]] carries them on its own sound's samples (its
source-stems layer), so the [[Ghost Lab]] can play either one alone.

- **Lives** -- the audio spans (the audio and SP-task rows in
  `src/sm64_events/memory/layout.py`); the per-VI copy
  (`plugin/gfxwrap/scene_tap.cpp`); the renders and the
  four checks (`tools/scene_lab/stems_probe.py`, `tools/scene_lab/stems_check.py`);
  the RSP interpreter (`tools/scene_lab/cxd4/`, vendored); a scene's layer
  (`tools/scene_lab/stems_layer.py`)
- **Not** -- separating a recorded mix after the fact (source separation,
  volume ducking): those guess at sound the game never split

### Scene package

An M4 experiment's portable file of the graphics tasks a [[scene tap]]
recorded, carrying no ROM bytes. Each [[task closure]] byte the viewer's own
ROM reproduces travels as a reference to the load the game made it from: a
level-script load, a MIO0 block, the boot copy of the main program, or a ROM
window the writer finds by content. The reader rebuilds each load from its
own ROM and checks its hash. Only bytes no load explains travel raw, mostly
the display lists the game builds for each task, and tasks that repeat them
share one copy. Any task decodes without visiting the others; the reader
refuses a package made from a different ROM, a truncated one or a corrupt
one. On savestate tasks about 95% of a closure is a reference, and a 175 KB
closure packs to about 2 KB (2026-10-24). Version 1 adds each task's [[scene
state]], so the game's own scene-graph code can redraw a task from a moved
camera with only the package and the viewer's ROM. A checkpoint every 64 tasks,
and at each level load, holds the whole state; the tasks between hold what
changed since the task before, so any task's state rebuilds from one
checkpoint and at most 63 tasks after it. The [[practice ROM]]'s own code
that the drawing executes travels as a reference to the ROM bytes it came from,
never as a memory image. On a 90-second recording the state adds about
5.4 MiB a minute to the closures' 2.4, and every checked task regenerates
exactly as it does from the tap's own files (2026-10-25). A scene-resolution
layer, added after writing, records what the [[scene evaluator]] found each
task's drawing used, as addresses only (3.5 KB for that recording). A
source-audio layer, also added after writing, carries the game sound
Project64's own audio output played, lossless, with the sample sounding as each
picture appeared; attaching refuses sound recorded beside a different recording.
A source-stems layer, added with it, carries that sound's [[sound stems]] on the
same samples and blocks, or the sentence why the scene has none.
The reader refuses a version or layer it does not know.

- **Lives** -- `tools/scene_lab/package.py` (write, read, verify); the
  [[scene evaluator]] reads it and writes the scene-resolution layer
  (`tools/scene_lab/evaluator.py`, its resolve command); the sound's format
  (`tools/scene_lab/audio_layer.py`) and its attach command
  (`tools/scene_lab/audio_session.py`)
- **Not** -- what the [[scene tap]] itself writes, which holds raw memory
  images and pictures and stays private on the machine that recorded it

### Re-simulated scene

A [[scene package]] the lab makes from a [[ghost]] alone: the [[ghost]]'s
recorded pads play SM64's own code from a start, in the simulation, and the
real [[scene tap]] producer records each simulated [[frame]]. The start, best
first: the [[ghost's start]]; his own [[attempt scene]] that begins at the
[[ghost]]'s first record; another take's practice [[reset]] in that level; or a
practice [[reset]] the simulation makes from the cartridge alone for any course
and act, even one he never played (a stage). That last [[reset]] needs no
recording, and every machine with the same ROM makes the same bytes, so the
lab also measures a [[ghost's start]] against it. The lab compares every simulated
[[frame]]'s Mario with the [[ghost]]'s recorded pose, and the scene names the
first record where they part. From his own [[attempt scene]] or a start file
the scene matches to the byte; from a [[reset]] Mario stays exact, but moving
platforms and enemies may stand at another point in their cycle, so such a
scene makes a save state only before the parting record, and only after the
slot passes a load check in the simulation (one switch: `SAVE_STATES` in
`tools/scene_lab/ghost_resim.py`). Another player's [[ghost]] works the same
way: the act is its [[star]]'s, and the scene names the cartridge its player
recorded it on beside the one it ran on. The lab plays these scenes on the US
cartridge only for now. It makes a JP [[reset]] and checks JP starts against it,
but it cannot play a JP scene or make a JP save state yet, and it says so in
one sentence when asked. Three of his Whomp's Fortress [[ghost]]s and two
other players' (2026-11-08): no parting record from any start. The lab writes
the scene while it simulates, so the viewer opens it as a growing scene: the
first picture shows in about a second once the lab has made its stage ahead
(looking at a [[star]] makes its stage in the background), and the rest
follows at about 55 [[frame]]s a second; a scene the lab made before opens in
under half a second.

- **Lives** -- `tools/scene_lab/ghost_resim.py` (from a ghost),
  `tools/scene_lab/stage_sim.py` (a stage), `tools/scene_lab/sim_scene.py`
  (the producer feed); `src/sm64_events/server/scene_resim.py` runs them in a
  child process; lab only
- **Not** -- an [[attempt scene]], which the cutter cuts from the live recording

### Scene evaluator

An M4 experiment that redraws a recorded task from a free camera by executing
SM64's own scene-graph code over a [[scene package]] and the viewer's ROM.
For each task it rebuilds the [[scene state]] and works out what the original
drawing used: values the drawing stepped and matrices it let go of go back
until its regenerated list matches the recorded one byte for byte. When the
package records that answer, it takes it after one regeneration confirms the
match instead of searching again. Then it redraws the task from any camera
position. It executes code only from the viewer's ROM and checks every block
against that ROM first. On this desktop a new camera on the same task costs
about 4 ms, the next task about 22 ms with the recorded answers (52 ms
searching, while walkers need their matrices put back), and a jump to any task
about 60 ms (2026-10-25). A viewer has its two [[state resolver]]s work out
the next tasks while it draws this one (a second thread until they are ready).

- **Lives** -- `tools/scene_lab/evaluator.py` (what a viewer calls); the
  game-code sandbox and its proofs (`tools/scene_lab/e2_georender.py`, with
  native hooks in `tools/scene_lab/cpu_hooks.c`)
- **Not** -- the [[renderer]], which draws a list; the evaluator makes the
  list the [[renderer]] then draws

### Ghost Lab

The Trainer's lab page (the Scene viewer until Griffin renamed it on 2026-11-08) that plays one
[[scene package]] in its reconstructed stage: the
game's own pictures from the **Recorded camera**, stepping forward and back, seeking, and playback at
the recording's own pace (each picture stays up as long as the recording showed it, so a level
load's held picture holds and its sound plays through), and a **Free camera** the [[scene evaluator]]
redraws. Top to bottom it is one bar, the picture, the play bar, the save-state strip and a panel with
two tabs, one for [[ghost]]s (open first) and one for the camera; no control moves another when its words or state
change (Play and Pause, Record and Stop, the [[frame]] numbers): each changing label or readout keeps
the size of its widest form, and notices and the [[cinematic camera]] on screen's settings show on
the picture's foot.

- **The bar**: the **stage picker** (Practice's own course grid and [[strategy]] dropdown), the one
  you watch (a ★, its US or JP flag, whose it is and its time; the scene's whole title on hover),
  one line for what went wrong or why there is no stage, the [[scene recording]]'s sign with Stop or
  Start, **More** (the picture's quality, the numbers, **Tune controls**, every key, and the lab
  folder's recordings) and **Close**. The stage picker chooses the **stage**: your fastest
  [[attempt]] of that course, [[star]] and [[strategy]] with a saved scene, which opens, or the lab's
  reason why there is none. It starts on the scene you opened, else on Practice's [[target]].
- **The play bar**: **Restart**, back to the last change of move, one [[frame]] back, Play or Pause,
  one [[frame]] on, on to the next change of move; the slider and which [[frame]] is on screen, with what Mario (or
  the [[stage ghost]] Follow names) is doing ("Mario: Dive"); the [[Play speed]] (1x, ½x, ¼x),
  **Loop** (kept per viewer) and **Sound** (Mute, the volume, **Music** and **Sound effects**). Moving
  the camera never pauses Play; only Pause, Space, a step or the slider do.
- **The save-state strip**: Usamune's nine slots as `slotlibrary/keeper.py` names them for the course and
  [[star]] the game is in: each
  shows its number, a picture of the [[frame]] a save from this page came from (the lab draws it with
  the recorded camera; a state saved in the game itself shows its place's name), the flag of the
  version that made it, ⇄ when [[Save state translation]] changed it for this game, and a gold edge
  with ▶ on the slot Usamune loaded last. A click on slot N, or the key N, saves the
  [[frame]] on screen there, over what was there, as Usamune's own save does; the line under the
  slots says what happened or why the page cannot save that [[frame]]. Each [[star]] may keep its own
  slots over the course's: a ★ marks a slot from that [[star]]'s own set, and a plain slot comes from
  the course's set; hover names the course save under it, and the line's right end names the course
  and the [[star]]. A saved slot's small menu gives it to that [[star]] alone or to the whole course,
  or takes it out of its set (the game keeps the slot). A save from the page joins the set of the
  [[star]] you watch.
- **The [[ghost]]s tab**: "In the scene" (the one you watch, then each [[stage ghost]] shown beside it with its
  [[ghost's view]], its Mute and its eye) beside one list: **Top** (each player's best, with its
  place: first across **All strategies**, each row naming its [[strategy]], or one [[strategy]]
  picked in its dropdown), **Yours** (fastest or newest first) or what **Find a player** found (each
  player's best; "more" opens their slower ones), a page at a time, each page asked once in a
  scene's life. A row is the layers pattern: the row itself **watches** that recording (it becomes the
  scene, its world, its camera and its save states: his [[attempt]]'s scene, or a [[Re-simulated
  scene]] made from the [[ghost]]), and its eye **shows** it beside the one you watch. A
  [[Re-simulated scene]] opens once its first [[frame]]s exist (the old scene plays on until then) and
  grows while the lab makes the rest: its row and the picture's foot show how far, with a Stop that keeps
  the [[frame]]s made so far, and the slider shows the part not made yet as an empty striped stretch
  that a drag cannot reach. Watching another row while the lab still makes one ends the older one. Every row wears the US or JP
  flag of the ROM its recording names, and no flag where its record names none.
- **Camera**: one labeled row each: Camera (Recorded camera, each [[ghost's view]], Free camera,
  **Look at Mario**, **Set focus**, back to where Free camera began), Cinematic (the [[cinematic camera]]s Front, Side,
  Orbit, Chase, Overhead, Tripod and Follow), Look (**HUD**, **Mario**, the [[tilt and lens]]),
  Views (the [[camera preset]]s 1-9, which Shift with 1-9 cut to), Path (the [[camera path]] of the
  course and area on screen), Fly (the flight speed) and Film (**Record** keeps a [[scene recording]]
  until **Stop**, which makes it into a [[scene clip]]; "Show in folder" opens Explorer at it).

Free camera starts from the picture on screen (the recorded camera's place, screen tilt and field of
view) and owns all four after that, so steps and seeks never move or zoom it. Keys: W A S D fly
whenever the page has the keyboard, Q and E down and up, Shift faster, Alt slower; hold the right
button to look around; drag to turn around the focus point, Shift-drag or a middle-button drag to
slide, the wheel to move closer; **double-click** a surface to fly to just in front of it and look at
Mario; F looks at Mario; R returns to the recorded camera; Z and C tilt, X levels, - and = change the
lens; , and . step like the arrows; [ and ] step to a change of move; 1-9 save the [[frame]] on screen
into that slot (his words: "prioritize 1-9 for save states, and then Shift 1-9 for camera views,
since save stating is more likely as an action"). A recorded tilt (a shake) levels as you turn or
fly, never on entry; a tilt you set stays. **HUD** (kept per viewer) leaves the game's on-screen
counters and timer out of every picture. Every picture the lab draws again shows Mario's most
detailed model however far the camera is. **Mario** (for this visit only) leaves the stage's Mario
out of every picture the lab draws again, so only the [[stage ghost]]s show; a level load or a menu,
which it cannot draw again, keeps him and says so. The page takes the keyboard when you arrive (the
sidebar or a [[ghost button]]) and after a mouse click on its controls; a field that types keeps its
keys, and Esc, leaving the window or the page lets go of every key.

It is lab-only and off by default: it exists only when `SM64_SCENE_LAB` names a lab config (the ROM,
the package folder, Project64's Plugin folder, the symbol map). Nothing starts until someone opens a
scene; the hidden [[renderer]] helper and the evaluator start then and stop on Close, after a while
unused, when no page shows the scene, and when the server stops. The page keeps two pictures in
flight, asks for the next as soon as one arrives, and a newer step or camera move replaces the one
waiting behind them; the game's code works out the next camera's picture while the [[renderer]]
draws and the [[picture encoder]] compresses the one before. Play asks for each picture ahead of its
recorded time and shows it at the screen refresh it is due. In the desktop app's window the pictures
skip the JPEG: the server hands the page the [[renderer]]'s exact pixels through WebView2 shared
memory. While the camera moves, the page sums up its own numbers every 2 s (pictures a second, input
to picture, decode, the server's stages): More shows them and the UI observation log records them.
A package with a source-audio layer plays Project64's own sound during Play, following the pictures.
Where the [[sound stems]] measured Project64's audio delay, each sound plays on the picture of its
[[frame]]; elsewhere it plays as heard, that delay after its picture. Stepping, seeking and Pause are
silent, and Play after Pause on the same picture goes on from where it paused; Mute (or M) and the
volume persist per viewer; a stretch the capture lost plays silent and says so. **Music** and
**Sound effects** switch the scene's [[sound stems]] on and off apart, remembered like Mute; a scene
without the split shows them disabled and says why in one plain sentence. Each [[stage ghost]] adds
its own sound effects, under the same volume and Mute.

- **Lives** -- the routes and lifetime (`src/sm64_events/server/scene_lab_api.py`,
  mounted by `src/sm64_events/main.py`); one open scene
  (`tools/scene_lab/scene_session.py`) and its sound (`tools/scene_lab/scene_audio.py`);
  the page (`src/sm64_events/ui/components/sceneviewer.js`), its play bar
  (`src/sm64_events/ui/components/sceneplaybar.js`), save-state strip
  (`src/sm64_events/ui/components/scenestrip.js`, read through the star sets'
  `src/sm64_events/slotlibrary/keeper.py`), build progress
  (`src/sm64_events/ui/components/scenebuild.js`), stage picker
  (`src/sm64_events/ui/components/scenestage.js`) and [[ghost]]s tab
  (`src/sm64_events/ui/components/sceneghosts.js`), the recording's
  own clock its Play keeps (`src/sm64_events/ui/components/sceneclock.js`) and its sound
  (`src/sm64_events/ui/components/sceneaudio.js`, rendered by
  `src/sm64_events/ui/components/sceneaudioworklet.js`);
  the free camera's math
  (`src/sm64_events/ui/components/scenecamera.js`) and feel numbers
  (`src/sm64_events/ui/scenecameratuning.js`); where it goes in the game's RAM
  (`tools/scene_lab/camera_state.py`); Set focus's point
  (`tools/scene_lab/pick.py`, which walks the picture's display list because
  the renderer's own thread holds its depth); its delivery numbers
  (`tools/scene_lab/e3_measure.py`)
- **Not** -- the replay player, which plays recorded video; this page
  redraws the game's graphics from a [[scene package]]

### Scene recording

What the [[Ghost Lab]] showed while **Record** was on, kept by the page as
one entry per screen refresh: the [[frame]] Play's clock was at (or the one on
screen while paused), the camera asked for and the camera the server drew
(recorded, free, a [[ghost's view]], a rig), the HUD and Mario switches, the [[stage ghost]]s
drawn, and the sound's switches, volume, mutes and the recorded time it played.
It holds no pictures: making the [[scene clip]] draws each entry again, so a
stall while recording never reaches the video. Recordings stop at 30 minutes.
The format carries a version, and the lab refuses a recording of a version it
does not know rather than guess what its entries mean.

- **Lives** -- the page's half (`src/sm64_events/ui/components/scenerecord.js`);
  its checks and the export job (`src/sm64_events/server/scene_exports.py`)
- **Not** -- the [[recorder]], which encodes Project64's own pictures as you
  play; nor a [[live spool]]

### Scene clip

The video a [[scene recording]] becomes: every entry drawn again at the
[[renderer]]'s full size in a background process of its own, 60 pictures a
second (the free camera moves every screen refresh; each of the game's own
pictures fills two), with the scene's sound mixed as the viewer played it: only
during Play, from the track Music and Sound effects chose, each [[stage ghost]]'s
own sound unless muted, at the volume, silent while paused, stepping or seeking.
That process encodes it as a saved replay's clip (the encoder the [[recorder]]'s
probe chose for this computer, its quality settings, H.264 and AAC in MP4) and
puts it in the saved replays' folder under "scene clips", named for the course,
[[star]] and local time.

- **Lives** -- the job and the file's name and folder
  (`src/sm64_events/server/scene_exports.py`, `src/sm64_events/core/paths.py`);
  the drawing and encoding child (`tools/scene_lab/scene_export.py`); the sound
  mix (`tools/scene_lab/export_sound.py`)
- **Not** -- a saved replay's clip, which is Project64's own video of an
  [[attempt]]

### Stage ghost

A [[saved ghost]] the [[Ghost Lab]] draws into the scene it plays (its
stage), from that [[ghost]]'s own recording. One you add appears in the next
picture, from the recorded or the free camera, while playing, stepping or
seeking either way. It looks as it does in the game: its own [[ghost
colour]], shadow and [[name box]], hidden behind the stage's walls. Where two
would show the same name (two of your own [[run]]s), each adds its time, so
you can tell them apart. Its first
[[frame]] stands on the scene's first [[frame]] (an [[attempt]]'s scene starts
where that [[attempt]] did), and it follows the game's clock from there, so
lag [[frame]]s keep it in step. At its end one with its [[star]] dance holds
its last pose and fades; any other one is gone. The race's own fades,
[[corner inputs]] and path stay off. It plays its own [[ghost sound events]]
(never music) beside the stage's sound, on the [[frame]]s that recorded them,
placed and faded from the stage's recorded camera and Mario as the game does,
at your [[ghost]] volume; each has a Mute of its own in the panel below the player.
A child process makes each one's sound once, apart from the server, so adding
one during Play never slows the pictures.
Each one in the scene has a [[ghost's view]].

- **Lives** -- the set, its alignment and its look
  (`tools/scene_lab/stage_ghosts.py`); its sound (`tools/scene_lab/ghost_sound.py`,
  SM64's engine in `tools/scene_lab/ghost_sound_host.cpp`); the scene's set
  (`tools/scene_lab/scene_session.py`); which row each one shows, in the
  [[renderer]] (`plugin/gfxwrap/ghost_viewer.cpp`), drawn by the same code as
  a race's (`plugin/gfxwrap/ghost_figure.h`)
- **Not** -- the [[ghost set]], which picks who races you in the game

### Ghost's view

The [[Ghost Lab]]'s stage seen from a [[stage ghost]]'s own camera: the
camera that [[ghost]]'s game drew with where its figure stands in that
picture, from its recording, so it follows that [[ghost]] through Play, steps
and seeks. One button per [[stage ghost]] ("Sully's view", beside Recorded
camera; Their view on its row), the picture's corner names whose it is, and
Free camera from it starts at that camera. The world stays the stage's: its
objects, its Mario, its Lakitu and its counters. Where the recording has no
camera there in the stage's area (a gap, before its start, after its end,
another area), the view holds its nearest one and says why.

- **Lives** -- which camera (`tools/scene_lab/stage_ghosts.py`, from each
  record's camera in `src/sm64_events/ghost/clip.py`); drawn through the free
  camera (`tools/scene_lab/scene_session.py`); the buttons
  (`src/sm64_events/ui/components/sceneviewer.js`,
  `src/sm64_events/ui/components/sceneghosts.js`)
- **Not** -- the recorded camera, the stage's own view

### Cinematic camera

A [[Ghost Lab]] camera that follows Mario (the stage's, or a [[stage ghost]]
chosen in Follow) along his path: **Front** rides in front of him, aimed at
him, in a Close, Medium or Wide shot; **Side** tracks his profile on the side
the stage's walls leave clear, cutting across only for a wall that would block
it a while, and staying a while after; **Orbit** circles him at the speed and radius you set, on the
scene's own clock, and with **Orbit while paused** (on unless you turn it off) keeps circling a paused
picture, so Record can take an orbit of a frozen picture; Play goes on from where it is; **Chase**
rides behind him; **Overhead** looks down at him from a height, from a little behind so he heads up
the picture; **Tripod** stays where the camera on screen stood when you chose it and turns to follow
him. Each but Tripod stands at his eye level (Overhead at its own height) until you raise or lower
it, rides a smoothed copy of his path so his wobble from picture to picture never shakes it, and
rises over a wall (then moves in) instead of sitting behind it; a Tripod never moves, walls or not.
Each carries its own [[tilt and lens]], and a longer lens moves it back so he stays the same size.
The lab works out every picture's camera from the whole path, so stepping back, seeking and Play
draw the same shot; Free camera from it starts at that camera. Its feel numbers are the Tune
controls' Cinematic rows.

- **Lives** -- the camera's math (`tools/scene_lab/camera_rig.py`); Mario's
  path and the stage's walls (`tools/scene_lab/scene_session.py`, a ghost's
  pose in `tools/scene_lab/stage_ghosts.py`); the controls
  (`src/sm64_events/ui/components/scenerigs.js`)
- **Not** -- the [[ghost's view]], which replays the camera that [[ghost]]'s own game drew

### Tilt and lens

How a [[Ghost Lab]] camera turns and zooms its picture. **Tilt** turns the
picture about its middle, in degrees, positive clockwise: the game's own screen
roll with its sign changed, so the world and the [[stage ghost]]s turn while the
HUD stays upright. **Lens** is the focal length a 35 mm camera would need to see
as much, matched by the picture's diagonal; the game's usual view is a 31 mm
lens (45 degrees tall), 14 mm is very wide and 200 mm very tight. The game's own
drawing code draws both, and decides from the lens which objects are in view as
it always does. The row acts on the camera on screen: a [[cinematic camera]]'s
own (they travel in its request, and a longer lens moves it back so Mario stays
the same size), else the free camera's, which the recorded camera or a
[[ghost's view]] starts from the picture first. Z and C tilt while held, X
levels, - and = change the lens while held, and so does Ctrl with the wheel. A
[[camera preset]] and a [[scene recording]] keep both.

- **Lives** -- the conversions (`src/sm64_events/ui/components/scenecamera.js`);
  the row (`src/sm64_events/ui/components/scenelens.js`); a cinematic
  camera's distance for its lens (`tools/scene_lab/camera_rig.py`); the
  game's RAM the free camera writes (`tools/scene_lab/camera_state.py`)
- **Not** -- the game's own field-of-view changes (a cannon, a painting), which
  only the recorded camera shows; the wheel without Ctrl, which moves the free
  camera closer instead of zooming

### Camera preset

A camera you saved in the [[Ghost Lab]]'s **Cameras** strip: a name and
the camera's place, aim, screen roll and field of view, in one of nine slots.
Each course and area keeps its own nine (a level's areas are separate worlds,
so their coordinates differ), and the strip shows the set of the area the
picture on screen is in, switching as the scene moves between areas. An empty
slot saves the picture's camera, whichever it is, and asks for a name at once.
Keys 1-9 or a click cut to one as the free camera, with no flight, and Play
keeps playing; the slot looks pressed until the camera moves, and putting the
view back returns to it. Rename and delete sit on the slot (Undo brings a
deleted one back); dragging one onto another slot moves it, swapping with a
filled one. The lab keeps them in one file in its folder, so they survive
restarts; a file it cannot read shows an empty strip and says so.

- **Lives** -- the file and its checks (`tools/scene_lab/camera_presets.py`,
  served by `src/sm64_events/server/scene_lab_api.py`); the strip
  (`src/sm64_events/ui/components/scenepresets.js`); the cut
  (`src/sm64_events/ui/components/sceneviewer.js`)
- **Not** -- a [[ghost's view]], which follows a [[stage ghost]]'s own camera
  as it moves; a camera preset stands still where you saved it

### Camera path

A [[Ghost Lab]] camera that moves through **keys** you set: each key is a
camera on a [[frame]] of the scene (its place, aim, [[tilt and lens]]), or the
**recorded camera** itself. **+ Key** keeps the [[frame]] on screen and the
camera on screen; from the Recorded camera it keeps the recorded camera, which
the path then eases out of and back into, blending with the game's own moving
camera. Between two keys the camera follows a smooth curve and meets each key
exactly on its [[frame]], gliding through a middle key without slowing; it
slows to a stop only at the first and last keys, at either end of a hold, and
at a key whose **Ease** is on. A key's **Aim** is its own point, or Mario or a
[[stage ghost]]: then the camera keeps turning to his eyes as he moves (the
point a [[cinematic camera]] aims at), easing between one key's aim and the
next one's. Before the first key and after the last it stays on that key. A **hold** is a second key
on the [[frame]] of the key before it (+ Key again on the same [[frame]]): Play
stops the game on that [[frame]], silent, for the hold's length (2 s unless its
row says otherwise) while the camera travels from the key before to it, then
the game and its sound go on ("bullet time"). It is Orbit while paused for any
camera move: a held [[frame]] that the camera circles or flies past, kept by
Record. A key's speed (1x, ½x, ¼x) plays the stretch from it to the next key
that much slower, the camera slowing with the game, as [[Play speed]] does for
the whole play. **Path** puts it on
screen, and Play, a step, a seek and Record all draw the same camera at the
same time in the scene, because the viewer works the path out from its own
clock alone. Each key's row jumps to it (the scene pauses on its [[frame]]),
replaces it with the camera on screen, or deletes it. Each course and area keeps one
path, as it keeps its [[camera preset]]s; a key's [[frame]] counts from the
first [[frame]] of the scene that plays it. The idea came from Xander's
SM64Scripts camera tools (credited in the app).

- **Lives** -- the math (`src/sm64_events/ui/components/scenepath.js`); the
  block and the viewer's side of it (`src/sm64_events/ui/components/scenepathbar.js`);
  the file (`tools/scene_lab/camera_paths.py`, served by
  `src/sm64_events/server/scene_lab_api.py`); the blends with the recorded
  camera and with a runner's eyes (`tools/scene_lab/scene_session.py`)
- **Not** -- a [[cinematic camera]], which follows Mario or a [[stage ghost]]
  wherever he goes; a camera path stands where its keys say, and at most turns
  to look at him

### Play speed

How fast the [[Ghost Lab]]'s Play goes: 1x (the recording's own pace), ½x
or ¼x. Slower, each recorded picture stays on screen two or four times as
long; Mario still moves in the game's own steps, but the free camera and a
[[camera path]] move every screen refresh, so Record makes slow motion of
both. Slower than 1x plays silent, music and sound effects alike, and the
sound comes back at 1x (one switch in the code, so this can change later).
A [[cinematic camera]] still moves once a recorded picture.

- **Lives** -- the speed's clock and the sound's rule
  (`src/sm64_events/ui/components/scenepath.js`); the row
  (`src/sm64_events/ui/components/scenepathbar.js`)
- **Not** -- the flight speed, which sets how fast W A S D move the free
  camera; nor a key's speed, which slows one stretch of a [[camera path]]

### Ghost button

The button left of ▷ on a [[practice log]] row, shown only while the scene lab
is on: one click switches to the [[Ghost Lab]], opens that [[attempt]]'s
scene and plays it from its first [[frame]] as soon as the viewer draws that
picture. A row the lab knows nothing about has none; a scene the lab is still
cutting shows it disabled ("Scene finishing…") until it is ready, and a scene
that cannot exist shows the lab's reason. [[star]] and [[segment]] rows both
have it. The same button sits on each community [[ghost]] in the Library whose
[[star]] or [[segment]] this Trainer knows: one click turns the
[[Ghost Lab]] to that [[star]] or [[segment]] and watches that [[ghost]], which becomes
the scene (a [[Re-simulated scene]] when he has none of it).
A finished scene's button stays after a restart, and goes when
[[ghost files]] deletes that scene.

- **Lives** -- `src/sm64_events/ui/components/attemptscene.js` (the button and
  each table's ask of the lab), drawn by
  `src/sm64_events/ui/components/attemptlog.js` and
  `src/sm64_events/ui/components/community-library.js`; `src/sm64_events/ui/app.js`
  hands the [[attempt]] or the [[ghost]] to the viewer
- **Not** -- the [[ghost toggle]], which races a recorded [[ghost]] in the game;
  this button only opens the [[attempt]]'s scene, and wears the same mark as
  the [[Ghost Lab]]'s sidebar entry

### Stem renderer

The lab's hidden child process, below normal priority, that makes the [[sound
stems]] while he plays: it follows the live recording's VIs, splits each audio
task with the game's own microcode (music, sound effects), and reads at the next
VI what Project64 played for it. It keeps a row per VI for about the last 16
minutes, in 66 fixed files it reuses in turn and never deletes. When the cutter adds an [[attempt scene]]'s sound, the child lines
the game's output up with that recording and hands the cutter the scene's stems on
the recording's own samples, or the reason there are none. About 3 ms of one
core per VI on savestates (2026-10-27).

- **Lives** -- `tools/scene_lab/stems_live.py` (the child, its store and the
  cutter's handle); the alignment and the layer (`tools/scene_lab/stems_layer.py`);
  started by the cutter (`tools/scene_lab/attempt_cutter.py`) when the lab config
  names its symbol map
- **Not** -- the cutter, which writes the scene's pictures; this child only
  splits its sound

### Picture encoder

The [[Ghost Lab]]'s child process that turns each picture the hidden
[[renderer]] helper drew into the JPEG the page shows. The helper's pictures
come back through shared memory, and the encoder reads them there, so the
server never copies the pixels and the game's code for the next camera never
waits on the encode for Python's lock (measured 2026-10-26: the game's code
took 4.0 ms alone, 7 to 9 ms beside an encode in the same process, 4.9 ms
beside one in another process). It uses the same encoder and settings as
before, so every JPEG is the same bytes; when it is not ready or has died,
the server encodes the picture itself.

- **Lives** -- `tools/scene_lab/picture_encoder.py`; started and closed with
  the scene (`tools/scene_lab/scene_session.py`); the desktop window's way
  around it (`tools/scene_lab/webview_pictures.py`)
- **Not** -- the [[recorder]]'s video encoder, which compresses recorded play

### State resolver

One of the [[Ghost Lab]]'s two child processes that work out the
[[scene evaluator]]'s state of the [[frame]]s Play needs next, so the free
camera keeps 30 fps. One [[frame]]'s state costs about 20 ms, and 40-55 ms where
the evaluator must search. Worked out on a thread of the server, it shared
Python's lock with the free camera's pictures: while the camera moved during
Play, some pictures came 150-300 ms late and Play skipped about one [[frame]] in
seven, with or without [[ghost]]s (measured 2026-10-27 on his Hazy Maze Cave
takes). Each child reads the open scene's own package from shared memory and
hands each state back the same way. The server keeps it as if it had worked it
out itself, so every picture is the same bytes. One child goes on [[frame]] after
[[frame]] while Play stays ahead; the other takes [[frame]]s further on when the
first falls behind. Windows gives them below-normal priority; they start the
first time the free camera moves in a whole scene and stop with it. A [[frame]]
they cannot do, the server works out as before.

- **Lives** -- `tools/scene_lab/state_resolver.py`; used by the evaluator
  (`tools/scene_lab/evaluator.py`); started and closed with the scene
  (`tools/scene_lab/scene_session.py`)
- **Not** -- the [[picture encoder]], which compresses pictures; a resolver
  draws nothing

### Attempt scene

The [[scene package]] of one [[attempt]] he just played, which the [[Ghost Lab]]
opens by the [[attempt]]'s id. It starts on the [[frame]] the [[attempt]]'s clock
started. An [[attempt]] that ends on a [[star]] grab ends on the [[star]] dance's last
[[frame]]: the last one before the game stops moving Mario (the exit fade's last
iris picture, his pause, a save dialog) or he leaves the dance, the same [[frame]]
his own [[dance tail]] ends on; the scene keeps nothing frozen after the dance.
Any other [[attempt]] ends on the last picture of the fade-out after its end: the
[[frame]] before the next level or area appears, the [[frame]] before his [[reset]],
or 10 s after the end when nothing comes. While the lab is on, the [[scene tap]] records
all the time, and a hidden child process writes each [[attempt]]'s package as he
plays it, publishing what it has every quarter second. So the scene opens as
soon as the [[attempt]] completes, before the tap has even recorded its fade-out:
it grows on screen as the recording reaches it (about 0.3 s behind the game), and a
Play that catches up waits on the newest picture. Its sound is the game sound the
[[recorder]] heard, from the first picture on; its [[sound stems]] join it a few
seconds after the [[attempt]] ends. Two [[attempt]]s that start together (a [[star]] and
the [[segment]] that goes on to the pipe, both from one [[reset]]) share that writing, and
each gets its own package, so the one that ends later is just as quick. Only an [[attempt]] no package was
following when it began (one started before the lab) is "cutting" for a few
seconds; one the recording does not fully hold has no scene, and says why in one
sentence. A finished scene stays, across restarts, until [[ghost files]] deletes
it. Each [[journal]] has its own folder, and a restarted server shows a scene again
only when the [[journal]] still holds that [[attempt]] with the same start and end.

- **Lives** -- the span (`tools/scene_lab/attempt_span.py`); the child that
  writes them (`tools/scene_lab/attempt_cutter.py`); which attempts have one,
  the journal's folder and its index, deleting them, and the finished scene's sound
  (`tools/scene_lab/attempt_scenes.py`); a scene read while it grows
  (`tools/scene_lab/scene_session.py` Growth, `tools/scene_lab/package.py`
  grown_package) and its sound while it grows (`tools/scene_lab/scene_audio.py`
  LiveSound, from `tools/scene_lab/live_audio.py`); the routes and the wiring
  (`src/sm64_events/server/scene_lab_api.py`, `src/sm64_events/main.py`); the
  page's growth (`src/sm64_events/ui/components/sceneviewer.js`,
  `src/sm64_events/ui/components/sceneaudio.js`)
- **Not** -- the replay's video of an [[attempt]], which the [[recorder]] keeps;
  this scene redraws the game's graphics, and its camera can move

### Viewer stage

The [[attempt scene]] the [[Ghost Lab]] shows as the world: its level, its
sound and its recorded camera. The viewer draws each added [[ghost]] into it from
that [[ghost]]'s own recording, starting on the stage's first [[frame]]. It is the
scene he opened; for a [[star]] or [[segment]] and [[strategy]] he picks, it is his
fastest finished [[attempt]] that still has a scene (a [[reset]] only when nothing
finished has one), else one sentence saying there is none yet. The [[strategy]] starts as the one he
last chose for that [[target]]. Watching another recording from the [[Ghost Lab]]'s lists (or a
Library [[ghost]]) makes that recording the stage: his [[attempt]]'s own scene, else a
[[Re-simulated scene]] made from the [[ghost]].

- **Lives** -- the stage choice (`src/sm64_events/server/scene_ghosts.py`), served
  to the page by the lab's routes (`src/sm64_events/server/scene_lab_api.py`)
- **Not** -- the stage banner's place (where Mario stands now); and not a merged
  world: other [[ghost]]s never bring their own level or camera

### Scene ghosts

The [[ghost]]s the [[Ghost Lab]] can add to its [[viewer stage]], in four lists: yours
(fastest or newest first), top (each player's best, fastest first: across every
[[strategy]] at once, or one), a search by player name (each match's best) and one
player's (fastest first), ten to a page. Each shows its time, its place among every
player's best (ties share a place), its [[rank]] on its [[strategy]]'s [[ladder]],
and the ROM its own recording names (US or JP; none where the record names none,
never a guess). The lists hold a recording only
when the in-game race would play it, judged by the race's own checks; they leave
anything else out and count it, never showing it with a warning.

- **Lives** -- the lists, and the loader that hands the renderer a listed
  [[ghost]]'s recording and the name its in-game name box shows
  (`src/sm64_events/server/scene_ghosts.py`); other players' through the race's
  community client, cache first (`src/sm64_events/server/scene_community.py`);
  served to the page by the lab's routes (`src/sm64_events/server/scene_lab_api.py`)
- **Not** -- the [[lineup]], which picks who you race in the game; nothing here
  changes the race or the practice [[target]]

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
beside the graphics plugin; it carries the same native build id with its own suffix.
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

### Bridge transport

How a selected picture crosses from the [[renderer]]'s OpenGL context into the
[[encoder helper]]'s texture, and how that texture comes back (2026-10-31). The
[[renderer]] picks one per request from what its graphics driver offers: the
keyed mutex (NVIDIA, no copy), the semaphore (no copy; the driver lacks the
keyed mutex, as the AMD tester's did) or the readback (one CPU copy; neither
lock). A transport the driver refuses is never picked again in that Project64;
when the driver has refused them all, recording falls back to the desktop grab.
The startup receipt in the [[Debug report]] names what the driver offers and
which one ran.

- **Lives** -- `plugin/gfxwrap/gpu_transport.h` (the values),
  `plugin/gfxwrap/gpu_render.cpp` (selection and the renderer's half),
  `plugin/gfxwrap/gpu_transport_helper.h` (the helper's half); contract in
  [the bridge doc](replay-gpu-bridge.md#transports)
- **Not** -- the encoder backend (NVENC or Windows Media Foundation), which
  turns the texture into video after the transport delivered it

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

`sm64_trainer_gfx.activation.log`, beside the graphics plugin: this Project64
process's startup lines only -- ROM open, each capture's request and
preparation steps with their QPCs, and every slow
callback in the 15 s after a capture request. The main log rotates, so these
used to be gone within half an hour. The previous process's copy is `.1`.

- **Lives** -- `plugin/gfxwrap/diagnostics.h` (activation_event picks the lines)
- **Not** -- `sm64_trainer_gfx.log`, which keeps everything, rotating at 4 MiB
  through `.1` and `.2`

### Draw facts

What the [[renderer]] did with each [[ghost]] the trainer asked it to draw, as
plain facts beside its status: the first step that refused the latest display
list (`drew` when none did), the first step that kept the latest picture from
carrying that [[ghost]] (`shown` when none did), and the GLideN64 settings
those steps read. A value the [[renderer]] did not report reads `unknown`,
never fine. The [[debug report]] prints them, with one sentence the trainer
builds from the step names.

- **Lives** -- `plugin/gfxwrap/ghost_draw_facts.h` (codes and the render
  thread's record); decoded by `src/sm64_events/ghost/draw_facts.py`
- **Not** -- the playback decision (`playback_reason`), which says whether
  playback wants a [[ghost]] at all; the draw facts say why a wanted one never
  showed

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

### Tested setup

The Project64 setup we test the trainer on: Griffin's own machine, recorded
with its source and date (2026-10-30). Wermi's build v7 of Project64 1.6
outside Program Files, the trainer's graphics plugin with GLideN64's
framebuffer emulation off, Azimer's Audio, the PJ64Fixups RSP, a raphnetraw
controller plugin, and for the Usamune ROM 8 MB memory, Counter Factor 1 and the
Interpreter CPU core. Each item carries an **importance**: *required* (known to
stop the trainer working), *recommended* (differs from what we test with) or
*noted* (kept only as a record; the controller plugin is never a problem).

- **Lives** -- `src/sm64_events/data/setup_baseline.json`, which is also the one
  table of importances, fix sentences and guide links
- **Not** -- the [[setup screen]]'s readiness: that proves the trainer's
  plugin sees the game; the tested setup describes the emulator
  settings around it.

### Setup check

Your Project64 setup compared item by item with the [[tested setup]]: each item
reads *same*, *different* or *can't tell* (no readable evidence; never a
guess). It is read-only and cheap: registry values, file hashes and the
`Project64.rdb` entry for the loaded ROM (the VirtualStore copy when one
exists). Every file comes from one Project64 folder, the running one (else
the last one seen), named by its own folder name; the check marks registry
values as shared by every Project64 copy on the Windows account. Each difference
links its Wermi guide page. Settings → Check my setup lists every item, differences first. When
the [[ghost]] the player asked for has gone unseen for 20 s and the check finds a
required or recommended difference, the practice card shows a notice that
opens it with those items highlighted; with a matching setup it shows nothing
new, because the failure is the trainer's. The wizard's completion page lists
differences as notes. The [[debug report]] and automatic error reports carry
it; `GET /api/setup/check` serves it alone.

- **Lives** -- `src/sm64_events/core/setup_profile.py`;
  `src/sm64_events/ui/components/setupcheck.js`; the stall is
  `src/sm64_events/ghost/display_stall.py`

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

### Live ghost

A friend's current Mario pose in a private [[practice room]]. Each person's game
and clock stay independent. The local [[renderer]] rebuilds the pose with its camera
and world; it never controls local Mario or changes the local game's pace. Old or unavailable
poses disappear. Saved [[ghost]] recordings remain independent.

- **Lives** — `src/sm64_events/multiplayer/` owns source, room and receiver state;
  `integrations/eos-transport/` transports opaque bytes through EOS relays;
  `plugin/gfxwrap/ghost_live_session.cpp` validates and draws three remote seats.

### Practice room

An invite-only group of at most four players, opened from Friends. A player sends
their player code to a room member, receives an invitation and explicitly accepts it.
The room survives navigation, practice [[target]] changes and local [[reset]]s. Closing
the Friends panel keeps the room; Leave room ends participation. Display names
are labels, not verified account or performance claims.

- **Lives** — `src/sm64_events/multiplayer/room.py` owns membership and lifetime;
  `src/sm64_events/ui/components/friends.js` owns the panel.

### Lineup

The active practice card's row of the people you race: your own seat first,
then each [[ghost slot]], slowest to fastest, centred at every width (his
words, 2026-10-16: "our pb on far left, then times get faster as we go to the
right"). A seat is a [[rank]] cap inside a ring in that [[ghost]]'s in-game
colour, the time and the name, with that seat's [[strategy]] small and italic
under the name (his words, 2026-10-28); a name wraps where the handle divides and is
never cut off, and a long [[strategy]] ends in an ellipsis. A dashed ring is a [[ghost]] that cannot race yet (yours before
your first time, a [[ghost]] still downloading); a lock on a ring is a pinned
[[ghost]], which stays even once beaten, to your left. Every rival stays once
beaten until you move to another [[star]] or press the button named after new [[ghost]]s (his rule, 2026-11-03), so
beating all of a set means something. The [[lineup track]] threads
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

The [[ghost]]s a [[target]]'s [[ghost slot]]s may hold. Every [[lineup]] is
Overall: it races every [[strategy]] of the [[target]] and grades them on the
[[Overall curve]], from the current [[strategy]]'s saved [[personal best]] (his
decision, 2026-10-28: "I think that actually is the better design in general").
A player near the limit of a slower [[strategy]] meets the slowest [[ghost]]s of a
faster one. The code still serves a caller that asks for the earlier
[[strategy]]-only matching, which holds the slots to the current [[strategy]]; no
card offers it, and the card's [[Strategy]]/Overall pair now picks only which
[[rank]] shows. A [[ghost]] someone pinned in one [[strategy]]'s [[lineup]] before
then took a free slot in the Overall [[lineup]]. Every [[ghost]] keeps its
recorded [[strategy]] label.

- **Lives** — `src/sm64_events/ghost/rivals.py` (DEFAULT_SCOPE, _widened_pins) and
  `src/sm64_events/ui/components/ghostswitch.js` (GHOST_SCOPE)

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

### Earlier ghosts

The [[ghost]]s this install recorded that its account may never have shared.
The first account that shares from this install claims them, and they upload under
that account with no question. Terms `beta-2` disclose this, so an account that last
accepted older terms adopts nothing. No other account can share them, and removing
shared [[ghost]]s closes the claim for good.

- **Lives** — `src/sm64_events/community/earlier_ghosts.py`

### Community ghost

A [[ghost]] shared by an authenticated, invited player. The service keeps that
player's fastest full-start recording for each [[division]], [[strategy]],
[[target]], clock and ROM version. Structural validation checks the recording;
it does not certify that gameplay was legitimate. Sharing begins unchecked.

- **Lives** — `cloud/community/` and `src/sm64_events/community/`

### Ghost's start

The game state a [[ghost]] begins from, saved with its recording. With it, the
[[ghost]]'s recorded pads re-simulate the whole recording exactly in a
[[re-simulated scene]], even after the [[attempt scene]] it came from is gone:
the [[scene state]] regions of that first record, and his own copy also keeps
the main pool's difference from the simulated practice [[reset]] of that level
and act. About 25 KB on his machine; about 21 KB travels, without the main-pool
part, which no other machine applies. The trainer saves one when an [[attempt
scene]] becomes ready while its recording's clip exists, and when the lab
re-simulates a [[ghost]] from his own [[attempt scene]]. It uploads a [[community
ghost]]'s start after the [[ghost]]'s own bytes when the cloud accepts one, at
most 64 KiB. The cloud keeps the start in the same object as its [[ghost]],
counts its bytes in every storage budget and deletes it with the [[ghost]]; on
his machine a purge deletes a start when the last clip of its bytes goes. The
cloud and the community client never read inside a start. A downloaded start
came from another player's trainer, so its owner checks its size, shape and
binding to the [[ghost]]'s first record, keeps it apart from his own, and the
lab checks every pointer in it against his ROM before it simulates anything. A
missing or refused start never stops a [[ghost]] from sharing or playing.

- **Lives** -- `src/sm64_events/ghost/start_frame.py` (the versioned file and its
  checks, in the ghost population folder's starts folder),
  `tools/scene_lab/start_check.py` (the pointer checks),
  `cloud/community/src/service.js` (storage and routes),
  `src/sm64_events/community/ghost_start.py` (the client's seam to its owner) and
  `src/sm64_events/community/sync.py` (send and download)
- **Not** -- the clip, whose format the race and the community exchange

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

### Ghost files

The Settings section that shows how much space [[ghost]] files use on this
computer and frees it. One button deletes every [[ghost]] except [[PB ghost]]s
(each [[personal best]]'s latest save, per [[strategy]] and ROM), [[saved
ghost]]s, [[template track]] [[run]]s and [[imported ghost]]s; the other deletes
those too. A [[ghost]] counts once however many files it has: an [[attempt scene]],
the committed recording and its [[playback copy]], the local population's row,
a downloaded [[community ghost]], an imported one. Neither button deletes a
file a pending upload names, a [[run]] still owed its admission, or the recording
that keeps a [[run]] with no [[strategy]] through the startup prune; the purge
skips and counts a [[ghost]] in use right now (racing, open in the [[Ghost Lab]]).
Neither touches saved replays and their videos, [[scene clip]]s, [[Slot Library]]
save states, the [[journal]] or the community server. After a purge the server
broadcasts `ghost_files_changed`, and the [[ghost button]]s and the [[lineup]] re-read.

- **Lives** — the purges and what each keeps (`src/sm64_events/ghost/storage.py`);
  each kind's holdings and delete in its owner (`tools/scene_lab/attempt_scenes.py`,
  `src/sm64_events/replay/service.py`, `src/sm64_events/ghost/population.py`,
  `src/sm64_events/community/cache.py`, `src/sm64_events/ghost/sharing.py`); the
  routes (`src/sm64_events/server/ghost_api.py`); the section
  (`src/sm64_events/ui/components/ghoststorage.js`)
- **Not** — the [[recorder]]'s rolling pose archive, which a PB save reads and
  which empties itself; nor a saved replay's own poses beside its video

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
