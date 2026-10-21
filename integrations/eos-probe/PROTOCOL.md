# R2 bounded transport experiment

This original MIT experiment transports424bytes. It defines no Mario pose,
Trainer identity, rank result or native-renderer structure. EOS authenticates the
transport peer; checked current-lobby membership/owner selects its permitted role.
These bytes cannot create a member or authenticate an account.

All integers are unsigned little-endian, encoded explicitly. No packed C++ struct
is cast onto incoming memory. Exact original and received length must both be424.

| Offset | Bytes | Field |
| --- | --- | --- |
|0|4|ASCII EPR2|
|4|2|Version2|
|6|1|Kind: DATA1, ECHO2, SESSION3|
|7|1|Actual lobby role: owner1, guest2|
|8|2|Encoded bytes424|
|10|2|Header bytes80|
|12|4|Reserved zero|
|16|16|Random per-process sender incarnation|
|32|8|Sender sequence; SESSION uses0|
|40|16|Recipient incarnation; SESSION uses all zero|
|56|8|Echoed originating DATA sequence; other kinds use0|
|64|8|DATA's origin-local monotonic send stamp; ECHO copies it; SESSION uses0|
|72|352|Reserved/padding zero|

Sender nonces are nonzero. DATA/ECHO recipient nonces, sequences and stamps are
nonzero. Sequence values are bounded to1,000,000. Undefined kinds/roles, malformed
header/padding or wrong channels are rejected. SESSION uses channel8; DATA/ECHO
use channel7. All use unreliable unordered delivery, delayed delivery off and
automatic acceptance off.

After actual membership and explicit SDK acceptance, bounded SESSION attempts
start a connection at most3Hz without admitting payload data. A received SESSION
can latch a remote nonce only after the matching actual relayed-route callback
and permitted role. It never changes an existing nonce. A valid DATA/ECHO must
match both that remote nonce and the recipient's current local nonce. The probe
supports only one membership epoch per process and ends after a current member
departs; deliberate within-process rejoin is unsupported. These experiment
nonces are not Trainer's production incarnation contract.

DATA is generated at a nominal30Hz. A delayed sender skips missed sample slots
and creates one current sample; it never emits catch-up bursts. Sequences advance
only when the SDK accepts that peer's DATA. SDK acceptance is not delivery.
SESSION retries stop after a valid peer DATA/ECHO confirms knowledge of our nonce.
No packet changes membership, closes another member's room or triggers a reply
loop. Responses apply only to already admitted peers on the expected socket.

Every tenth DATA sequence requests an echo implicitly. A new valid DATA may
produce one same-size ECHO, rate-limited to at most3Hz per peer. Duplicates and
ECHO/SESSION messages never elicit ECHOs. RTT accepts only an outstanding request
from that same admitted peer, correct run nonces, exact sequence and exact local
origin stamp. An unmatched, duplicate, clock-reversed or older-than8s echo cannot
add a sample. The outstanding ring holds64requests and the RTT sample buffer1024.
RTT is local elapsed time across SDK/helper scheduling and the full round trip;
no remote clock is subtracted and no one-way latency is reported.

A128sequence receive window distinguishes duplicates/reordering within its
retained range. Gaps that refill reduce current holes. Holes aging out are
recorded as unresolved at window expiry; a still older packet cannot later be
classified reliably as a duplicate versus a recovered hole. The initial prefix
before first receipt and the final unobserved sender tail are reported separately.
There is no claimed network-loss percentage or final delivery acknowledgement.

Queue samples expose SDK counts and bytes; the SDK exposes no per-packet age
through this query. Local receiver arrival age/gaps and local outstanding echo
age are distinct measurements. The generic probe's timestamps do not measure
actual game capture, presentation latency or local practice impact.

Every stdout record has report_version2 and probe_version2. No PUID, lobby,
socket capability, configuration value or nonce is logged. `peer_experiment`
uses only an ephemeral numeric binding ordinal and aggregate counters. R1 logs
and packages are neither converted nor overwritten.
