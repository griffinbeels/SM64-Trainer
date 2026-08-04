# Fixture — two ways a glossary fails to close

Journal's definition says "projector" as ordinary English, though Projector has
its own row. Event's definition marks a term nothing defines.

## What runs

### Journal

The append-only file holding every [[event]] the game produced, replayed by the
projector.

- **Lives** — the journal writer (`src/sm64_events/core/uilog.py`)

### Event

One thing the game did, recorded once in the [[journal]] and read by
[[nothing-defines-me]].

- **Lives** — the event definitions (`src/sm64_events/core/events.py`)

### Projector

Rebuilds every attempt from the [[journal]].

- **Lives** — the projector (`src/sm64_events/tracking/projection.py`)
