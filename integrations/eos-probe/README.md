# Standalone EOS internal probe R2

This directory contains newly authored probe code under its own MIT license.
It is independent of Trainer's game-specific implementation. It must not import,
include or link Trainer source, its native structs, the renderer or GPL modules.
Epic's SDK is acquired separately and retains its own terms and notices. This
component's license does not relicense Epic materials or existing Trainer code.

The probe is for bounded, explicitly configured internal evaluation. It does not
grant public redistribution clearance. Root owns actual build, credentials and
execution. Do not run a network probe while preparing its source.

Intended build inputs are this directory's source, Windows SDK/MSVC libraries,
and the separately acquired EOS C SDK 1.19.2.1 Include directory plus the import
library `EOSSDK-Win64-Shipping.lib`. Use an x64 MSVC developer environment and
`/std:c++17 /EHsc /W4 /MT /O2`; select an ignored output directory explicitly.
Do not add the Trainer source/include directories to the compiler search path.

Only `EOSSDK-Win64-Shipping.dll` is the intended EOS runtime build input for this
Windows proof. Do not copy the SDK tree, headers, samples or private configuration
into a public artifact. Validate actual PE imports, SDK designation, notices and
helper/runtime sizes before deciding any later packaging.

The CLI accepts only file paths and bounded options:

```text
eos-probe --config PRIVATE_CONFIG --ticket-out NEW_PRIVATE_TICKET
  [--mode identity|host|guest|uninvited] [--peer-ticket PRIVATE_PEER_TICKET]
  [--seconds 1..300] [--parent-pid PID]
```

The private UTF-8 configuration has exactly five `key=value` lines:
`product_id`, `sandbox_id`, `deployment_id`, `client_id`, `client_secret`.
No value belongs in this repository, a command argument or a log. The probe
never prints the configuration or identifiers. Ticket output is exclusive and
created with a protected owner-only Windows DACL; choose a fresh ignored private
path for each run. Tickets are private runtime state and can contain identifiers
and the room socket capability. Never publish them as evidence.

Every JSON record identifies `report_version=2` and `probe_version=2`. R2 tickets
require `version=2`; an R1 ticket or zero-filled R1 payload is rejected. Existing
R1 packages and results must remain separate and unchanged.

`identity` retains the SDK's Device ID and exports its PUID without joining a
room. `host` creates an invite-only four-seat room and exports its room ticket;
an optional guest identity ticket directs one invitation. `guest` requires the
host's room ticket and joins only an invitation matching its lobby and owner.
An empty invite list does not prove all unauthorized join attempts are blocked.
Transfer tickets deliberately between real devices; do not reset a local Device
ID or count two processes sharing the OS keychain as two players.

`uninvited` is a deliberate private admission experiment. Run a fresh host
without a peer ticket so it sends no invitation. On another real identity,
`uninvited` uses that host ticket to perform actual SDK accept/send operations
without joining its lobby. The host must explicitly refuse nonmember connection
requests and admit no SESSION/DATA. Guest queue acceptance is not delivery or
host rejection proof; compare contemporaneous host receipts and distinct private
identity tickets. This does not exercise the server's JoinLobby permission.

The SDK is initialized, ticked and shut down on one thread. The probe disables
overlay/RTC/presence and reads back relay-only control. It sends disposable opaque
424-byte messages at a nominal30Hz, with no delayed delivery or auto-accept.
The versioned [experiment format](PROTOCOL.md) adds run correlation, sequence and
sampled echo RTT without importing any game format. Bounded SESSION bootstrap
starts explicitly accepted connections; SESSION binds only after a real relayed
route witness and never changes membership. DATA cannot replace a bound run.
Every packet's original length is checked before interpreting received length.
The source has no game pose parser or renderer dependency.

Membership notifications revoke existing bindings before channel close. The
one-second snapshot remains reconciliation. R2 supports **one membership epoch
per process**: an observed member departure/disconnect/kick/close ends the probe.
It does not accept a same-PUID rejoin or claim persistent-room recovery. This
prevents an old unseen SESSION from binding a rejoined membership. Actual
revocation timing and hostile admission require runtime receipts.

RTT uses an echoed outstanding sequence/timestamp matched on the originating
process's monotonic clock. It includes SDK queuing and both helpers' scheduling;
it is not a synchronized one-way delay. Echoes request only every tenth DATA
sequence and respond at most3Hz per peer, with no echo-of-echo. Missing sequences
are provisional/aged-out holes, not a network-loss percentage; the ending tail
is unknown. Sender overruns send only the newest sample. SDK queue sizes remain
ideal limits, queue packet age is unknown, and the application keeps no payload
backlog. These counters do not establish actual Mario capture-to-render delay,
in-game frame impact or remote ghost feel.

The R1 two-PC proof is separate historical evidence. No successful R2 build,
RTT or negative-admission result is established by this source. Compile/run the
SDK-free `protocol_test.cpp` separately for local bounded-contract tests; it
cannot prove provider membership or Internet behavior. Exact internal build/run
receipts are recorded separately.
