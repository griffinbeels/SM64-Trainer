# Private friends practice (M3)

Friends opens an invite-only room for at most four independent local games.
Each player sends their player code to someone already in the room. That member invites the address;
the guest explicitly accepts the received invitation. There is no room-code
lookup service. Closing the panel keeps the room. Leave room withdraws local
sharing and incoming presentation before requesting provider departure.

Navigation, practice targets and local resets do not end a room. The existing
Ghost Overlay switch hides incoming ghosts without disabling sharing. Manual
Trainer pause withdraws outgoing poses while the room and incoming display stay
available. Video recording is independent. No remote input or RAM writes,
rollback, synchronized emulator clock, casual-race scoring or ranked matchmaking
is part of M3. Display names do not verify account ownership or performance.

GPU recording is preferred. If recording falls back to the Project64 window,
ghosts remain visible and sharing continues. Window footage includes visible
ghosts even with **Ghosts in recording** off; that preference cannot remove
ghosts from a photograph of the window. Recording fallback never changes the
Ghost Overlay switch or room membership. Qualified GPU startup failures retain
their cause and retry after a ROM reopen or a new producer generation/process;
unsupported hardware keeps usable window footage. Cleanup must finish before
another GPU capture starts. See `replay/sourcefactory.py` and `replay/gpuretry.py`.

## Ownership and transport

- `multiplayer/room.py` owns ephemeral rooms, current membership, source identity,
  bounded reconnect state and receiver delivery on one background worker.
- `multiplayer/eos_process.py` owns the quiet, kill-on-close child job, bounded
  private JSONL pipes, command deadlines and latest-pose coalescing. An unknown
  command outcome retires its helper generation; a late join cannot revive it.
- `integrations/eos-transport/` is an original MIT component. It includes no game,
  pose or GPL headers. Its only external runtime is the selected EOS C SDK.
  Private lobbies and two forced-relay P2P sockets carry opaque application
  control and latest poses. No Epic launcher, account UI, overlay, RTC/voice or
  EAC is initialized. Costs stay within the approved $0 service arrangement;
  this integration never configures billing or expands provider policy.
- The room starts identity/authentication only after a deliberate identity or
  room action. Reading the cached local API never starts the SDK or capture.
  Helpers and room state do not automatically resume after application restart.

Only actual authenticated SDK membership can authorize a peer. Fresh transport
challenges bind each connection epoch. Reliable SOURCE_INFO binds that admitted
peer to a separate stable application source nonce and display label. Pose data
cannot create membership, replace that identity, select a local file/model, or
promote the sender to host. Removed memberships remain retired. Reconnect clears
visible poses while preserving source sequence/time fences.

Reliable SOURCE_STATE follows SOURCE_INFO on each admitted transport. It reports
playing, paused or waiting with a monotonic serial and a real source occurrence
floor. Pause/source loss clears presentation. Resume requires a fresh occurrence;
delayed pre-pause frames cannot refill it. One-second heartbeats do not invent
poses or refresh their receipt age.

## Source, receiver and rendering

`ReplayRecorder` combines independent footage, local saved-ghost and room demand.
A registered local ghost consumer retains its pose archive and playback source
through footage off/failure and room leave. Room endpoints hot-mount on that
same source. Without a local archival consumer, the separate silent live mode
opens no encoder, audio player, scratch/archive or media store. All consumers
share one `NativeCapture` and machine lease. The sole pose pump publishes to
`SourceHandoff` before archive callbacks. Room IO never
runs on that pump or on an emulation/render/GPU encoding thread. Codec probing
is deferred until actual media work needs it.
`tests/test_ghost_recording_independence.py` covers local pose-only publication;
`tests/test_multiplayer_demand.py` covers both truly silent rooms and the combined
local archive/room lifecycle, including retained cleanup and ROM/window changes.

SMLV version 0 is a fixed 424-byte pose. Python and native validation both check
bounds, representation, current source identity and monotonic source occurrences.
The sender's actual captured QPC and verified local frequency bound the helper's
send queue. Native SDK dequeue timestamps survive private pipe/parent stalls.
They do not reveal earlier SDK residence or synchronized remote capture age.
The receiver currently expires poses after 250 ms; this prototype bound does not
promise a particular network latency.

`ghost/live_channel.py` writes a separate fixed 4096-byte local receiver mapping.
Its capability comes from the existing captured-source lease: process birth,
token, mapping/ROM generations and configuration. `ghost_live_ipc.*` runs on the
same existing native worker, validates that capability, and chooses the local
verified ROM/model itself. A late old client cannot cancel a replacement owner.
Local model/capture retargets suspend display while retaining remote source
history; actual room departure or member removal retires that authority.

The existing saved ABI retains 16 slots. The compositor has 19 draw slots:
16 saved plus three live. Saved and live contributions share one locally derived
model. Every included contributor must still be current before presentation.
The render path performs no networking, file IO, waits or model allocation.
Independent live presentation demand does not negotiate/start a video recorder.

Room actions have one absolute request deadline, including startup, retries and
backoff. An expired action retires its uncertain helper generation before late
events can mount a source. The room worker survives passive observation faults
and keeps a cleanup-only executor until retained handles actually close. Public
shutdown remains bounded. A failed leave also retires uncertain provider state.

Terminal peer loss is distinct from reconnecting. Friends offers Reconnect using
the current membership and a fresh transport epoch; source replay fences survive.
Pose events coalesce to at most three latest peer samples after the private pipe,
while membership and control events keep their bounded ordered queue. The pipe
reader buffers raw stdout instead of performing a read per byte.

Python capture receipts, room freshness and the developer lab share raw QPC
nanoseconds through `core/qpcclock.py`. Python 3.12's `monotonic_ns` has a different
Windows clock origin and is not used for those comparisons. Request deadlines
and retry timers remain local duration clocks.

The room's passive diagnostic projection exposes fixed phases, failure codes,
counts and native receiver evidence. It excludes provider/player identities,
invitations, source capabilities and packet data. The same projection feeds
automatic reports and the local Debug report; see [error reporting](error-reporting.md).

## Private setup and verification limits

Source/private launchers explicitly provide `SM64_EOS_HELPER`, `SM64_EOS_CONFIG`
and `SM64_EOS_CACHE` paths. No configured runtime produces a clear unavailable
state. Dedicated untrusted player-client configuration stays in ignored local
setup storage. Operator credentials never belong in desktop builds. See
[security policy](security.md) and the helper's [IPC/build contract](../integrations/eos-transport/README.md).

Focused tests cover room and request lifetimes, admission/freshness, source/media
demand, native custody, mapping replacement and the actual Friends UI at narrow
and wide sizes. Hidden GPU witnesses exercise real locally derived Mario meshes
with synthetic local cameras/worlds. Such tests do not establish real gameplay
quality. Real R2 latency/negative-admission results, multi-PC lifecycle and
four-player/geographic quality remain required evidence before public release.
The current implementation proceeds under the explicit assumption that R2 passes.
Actual SDK licensing/distribution review applies to the final package boundary.

The private draft uses EOS-native member invitations and host-only player removal.
An actual SDK test returned AccessDenied even for the host with bAllowInvites=false;
the same-config true-setting control succeeded. The working draft makes that
permission visible in the Invite form. This is an agent-prepared recommendation
for private review; the human invitation-policy choice remains open before public
release. R2's earlier true setting did not establish owner-only enforcement.

## Developer loopback and fault inspection

`python tools/m3_live_lab.py --port 8094` explicitly starts an offline developer
app; close other Trainer instances first. Open its printed `/m3-lab` URL yourself.
Start requests the existing sole native source and draws one to three LAB seats.
There is no EOS session, account restoration, sheet refresh or actual network peer.
The separate source tool is excluded from the shipped UI.

Seeded delay, jitter, loss, extra holds, duplicates and per-seat outages feed the
same NativeInbox/LiveChannel path. The queue is bounded to512 and trace to128.
Source gaps, pause, Stop and restart clear in-flight samples; native replay fences
remain. Fault decisions are repeatable while each lab process has fresh admission
namespaces. Capture QPC age, pump receipt age, simulated queue age and native-page
submission counts are distinct. None claims internet latency or visible pixel age.
