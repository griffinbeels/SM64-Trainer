# Fixture — a small, closed, active-voice glossary

Two rows that satisfy every check. Tests mutate this file to prove each rule
has teeth, so keep it minimal: every word here is load-bearing.

## What runs

### Journal

The append-only file holding every [[event]] the game produced.

- **Lives** — the journal writer (`src/sm64_events/core/uilog.py`)

### Event

One thing the game did, recorded once in the [[journal]].

- **Lives** — the event definitions (`src/sm64_events/core/events.py`)
