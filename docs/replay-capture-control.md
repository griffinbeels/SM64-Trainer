# Native capture control: the control page and its lease

The capture wrapper publishes a 4096-byte control page
(`<stream>_control_v1`, default stream `sm64_trainer_gfx_v1`). The server reads
it to discover a live producer and to turn GPU capture on and off without
switching plugins or restarting Project64, which was his requirement for the
plugin (round 22): with no server, the plugin behaves as plain LINK GLideN64.
Discovery never requests capture or creates capture resources.

## Layout and owners

- `plugin/gfxwrap/control.h` owns the layout. The producer owns bytes [0,128);
  one mutex-serialized client owns [128,256). Each half carries a 32-bit
  sequence that is odd while writing, so readers take a bounded snapshot and a
  dead writer cannot make them spin.
- The producer half carries state (CLOSED, PASSIVE, PREPARING, UNAVAILABLE,
  ACTIVE), reason, capabilities (CAP_PASSIVE, CAP_GPU), acknowledgement, build
  identity, producer PID and process birth time, generation, and two cartridge
  flags: `rom_open` (a practice ROM is open) and `rom_baseline` (another
  cartridge is open, so the plugin runs as plain GLideN64). See the practice ROM
  gate in [Renderer GPU recording](replay-gpu-runtime.md).
- `control_worker.h` owns the worker's lifetime. `replay/capturecontrol.py` is
  the Python reader and the generation-bound lease owner; only the recorder
  owner acquires a lease.

## What the worker guarantees

- One background worker owns the page, its small synchronization objects and a
  reference to its own DLL. Every check it makes runs outside graphics callbacks.
- A lease lasts three seconds and must be renewed. The worker watches the owner
  process; PID plus creation time rejects a reused process ID.
- Client commands serialize on a named mutex. The worker only tries that mutex
  with a zero timeout; ordinary contention keeps the last valid command, and a
  torn command from an abandoned writer is refused.
- Closing an old lease cannot disable its successor or another generation.
- A producer without CAP_GPU answers enabled demand with
  UNAVAILABLE / NO_BACKEND. A non-practice cartridge never sets `rom_open`, so
  no lease can activate capture there.
- Shutdown signals retirement without joining the worker. A new initialization
  requests a new session, and a failed reinitialization retires the old one.
  Readers reject in-progress writes and closed or dead producers.

## Verification

`tests/test_capturecontrol.py` drives the real x86 DLL from a small CPU-only
host and a Python client: discovery (opening exactly the control page),
capability refusal, stale cleanup that cannot disable a new owner, expiry and
renewal, owner and producer death, immediate restarts, failed
reinitialization, contention, cleanup errors, and a vanilla cartridge reported
as `rom_baseline`, never as open. No test needs Project64, a visible window or
a GPU context. `tools/graphics_diagnostics.py` includes a bounded control status
snapshot and never acquires a lease.
