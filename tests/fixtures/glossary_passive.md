# Fixture — a definition that hides its actor

"is written ... by the server" names the actor only as an afterthought, and a
definition written that way stops telling you which module to open.

## What runs

### Journal

Every record is written into this file by the server.

- **Lives** — the journal writer (`src/sm64_events/core/uilog.py`)
