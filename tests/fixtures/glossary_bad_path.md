# Fixture — a Lives line pointing at a module that does not exist

## What runs

### Journal

The append-only file holding every [[event]] the game produced.

- **Lives** — the journal writer (`src/sm64_events/core/no_such_file.py`)

### Event

One thing the game did, recorded once in the [[journal]].

- **Lives** — the event definitions (`src/sm64_events/core/events.py`)
