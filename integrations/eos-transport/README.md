# Standalone EOS transport v1

New MIT component. It links only the approved EOS C SDK and Windows/MSVC runtime
inputs. It includes no Trainer, game, pose, native renderer or GPL source. SDK
materials keep their own terms/notices; this license does not relicense them.
Actual SDK/package/licensing proof remains a separate release gate.

Private draft: current room members may invite, while only the owner may kick.
Actual SDK tests returned AccessDenied even for the owner with bAllowInvites=false;
the same-config true-setting control succeeded. This working recommendation is
prepared for private review. Its invitation policy and actual multi-PC quality
still require review before public release. No portal client policy or billing
configuration was changed.

```text
eos-transport --config PRIVATE_FILE --parent-pid PID [--cache-dir PRIVATE_DIRECTORY]
```

The parent starts this helper only after an identity/room gesture. Startup then
uses persistent EOS Device ID, requiring no Epic account UI, launcher, overlay,
voice or EAC. The private config contains exactly product_id, sandbox_id,
deployment_id, client_id and client_secret key=value lines; values never appear
in command arguments, source, diagnostics or events. This component performs no
service registration, billing or provider-policy mutation.

The inherited stdin/stdout must be private pipes. Do not print/capture raw IPC
in public diagnostics: it necessarily contains identity codes, transport nonces
and application payloads. SDK logging is disabled. Typed errors contain fixed
codes only. Heartbeats are required every10s from process startup, including
authentication; send them every2s. Parent loss/stdin EOF/output failure stops the
helper and revokes room state. The parent also owns a kill-on-close Windows job.

Every input/output object has v1. Commands are flat ASCII JSON objects bounded
to4096bytes, with unsigned64-bit integers and printable strings. A control
command has request_id; send_latest and heartbeat omit it. Never reuse a control
request_id; source publication sample_id increases within the current peer epoch.
Replies contain request_id, ok and a fixed error string (none on success).
One room mutation is active at a time; busy may be retried boundedly with a new
request ID. send_control success means SDK queue admission, not remote delivery.

| op | Additional fields |
| --- | --- |
| identity, create_room, refresh_invites, shutdown | none |
| invite | room_epoch, user_code |
| accept_invite, decline_invite | invite_ref |
| leave | room_epoch |
| kick | room_epoch, peer_ref, member_epoch |
| bind_peer | room_epoch, peer_ref, member_epoch, transport_epoch, local_nonce |
| reset_peer | room_epoch, peer_ref, member_epoch, transport_epoch |
| send_control | peer_ref, member_epoch, transport_epoch, payload_b64; room_epoch optional |
| send_latest | room_epoch, peer_ref, member_epoch, transport_epoch, sample_id, sample_qpc, payload_b64; no request_id |
| heartbeat | no request_id |

user_code is ET1- plus the actual32hex EOS Product User ID. It is an address,
not claimed Trainer-account ownership or proof of honest performance. Private
rooms require an actual current-member invitation to the guest's identity code and
explicit acceptance from the invitation inbox. There is no invented room-code
join. Cache tokens stay inside the helper; invite_ref is an ephemeral bounded
cache reference. Lobbies are INVITEONLY/max4, no presence/RTC/join-by-ID, with
native host migration enabled and fresh invitations required after kick.

Events: ready(protocol,max_payload1024,max_control512,qpc_frequency), identity,
invitations(revision,entries), room(room_epoch,state,owner_code,local_is_owner,
member_count), peer(room_epoch,peer_ref,member_epoch,transport_epoch,user_code,
state), peer_ready(same epochs,local_nonce,remote_nonce,route=relay), peer_unready
or peer_left(same epochs,reason), data_received/control_received(same epochs,
nonces,sequence,received_qpc,payload_b64), stats and fatal(code). Identity and
payload events are private IPC, never public logs. Room state is emitted before
new peer announcements. Current owner comes from SDK membership, including
promotion/migration. Authentication expiry/loss currently reports an honest
fatal state instead of promising invisible recovery.

Both independent sockets require actual relayed-route witnesses. HELLO/ACK
challenge the parent's fresh128-bit nonce on each membership/transport epoch;
an ACK must echo it before a remote nonce can latch. Old unseen HELLOs/ACKs cannot
admit a new epoch. HELLO alone never binds a nonce; DATA/control cannot create a
member or replace a session. Leave/kick/member removal revokes before close.
After initial room admission, a new member requires an actual SDK JOINED notice,
preventing a stale cached snapshot from resurrecting a removed participant.
Transport reconnect has a15s bound and requires a fresh parent bind; failure
stays per-peer until deliberate reset. Stable application source identity is
opaque parent metadata and is independent from these transport nonces.

Wire: ETP1, uint16 version1, uint8 kind(HELLO1/ACK2/DATA3/CONTROL4), reserved0,
uint16 header64, uint16 payload length, reserved uint32, sender nonce16, recipient
nonce16, uint64 sequence, reserved uint64; explicit little-endian. HELLO/ACK have
zero sequence/no payload; HELLO recipient is zero, ACK recipient echoes HELLO.
DATA is channel1 on SM64TrainerDataV1, <=1024bytes, unreliable unordered.
CONTROL is channel2 on SM64TrainerControlV1, <=512bytes, reliable ordered.
Each socket's HELLO/ACK uses channel0, bounded unreliable retries; automatic
acceptance and delayed delivery are disabled. Exact original length is checked
with fixed SDK-max receive storage; a truncated prefix is rejected.

Separate sockets matter: clearing a P2P queue clears incoming/outgoing data on
every channel of that socket. Conservative DATA shedding cannot delete reliable
SOURCE_INFO/application control. Reliable-control excess fails the peer explicitly
instead of silently discarding metadata. Latest source samples coalesce to one
slot per peer and expire after100ms; missed/stale sends never form a backlog.
All operations/queues/rates are bounded. Runtime counters do not establish
network-loss rates, ranked integrity, one-way latency or rendered Mario delay.

sample_qpc and received_qpc use raw Windows machine-local QPC; ready supplies the
frequency. Parent source frequency must be validated. received_qpc is stamped
immediately at native SDK dequeue before parsing/private output, retaining age
through a stalled stdout pipe. EOS does not expose original network-arrival
timestamps, so earlier SDK/network residence is unknown. Never compare these
values across PCs or call them synchronized capture timestamps.

Build with MSVC x64 /std:c++17 /EHsc /W4 /MT /O2, only this directory and the
approved SDK Include/Win64 import library. Windows libraries include the standard
kernel/system APIs. No Python package or additional runtime download is required.
The EOS DLL still requires its verified MSVCP140/VCRUNTIME140/VCRUNTIME140_1/UCRT
closure even if this helper uses /MT. Compile core_test.cpp separately without
SDK or Windows inputs for bounded parser/wire/session tests. Root owns all native
compilation and runtime probes. No production pass is established by this source.
