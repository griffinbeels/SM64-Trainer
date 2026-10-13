# Community ghosts protocol, version 1

The private beta uses Cloudflare Workers, D1 metadata, private R2 objects and
managed Supabase Auth. Public player names are mutable; the verified Auth
issuer/subject owns data. A valid login does not prove honest emulator play.
No ROMs, videos, passwords or complete practice histories are uploaded.

New-account signup explains automatic sharing before explicit policy acknowledgement.
Completing signup enables sharing; Settings can disable future uploads or withdraw
existing shared ghosts. Consent renewal preserves the existing sharing choice.
The local API's omitted-sharing default remains off; the UI submits its disclosed
choice explicitly. Sheet time imports remain optional and unchecked.

Account names allow20 Unicode code points and63 UTF-8 bytes after NFKC normalization.
The October8 Library snapshot contains454 names; its longest is
SullyLikesBigChungus at20 characters/20 bytes. Native labels use the original
credit when drawable and a colour-linked numbered rival label otherwise; account
identity and full web credits remain unchanged. See the native runtime contract.

## Authentication and build configuration

Use Supabase's maintained OAuth 2.1 server with one registered **public** beta
client, `token_endpoint_auth_method=none`, S256 PKCE and exact redirect
`http://127.0.0.1:8072/api/community/auth/callback`. This deliberately uses
Supabase's documented beta OAuth-server feature for the private beta, rather
than inventing a password service or forwarding browser access tokens into the
trainer. Native OAuth is through Authlib; the hosted login/consent page uses
the maintained Supabase JS SDK for Discord and Twitch (Google and email remain
on the hosted page until its next deployment; the trainer never offers them,
his decision 2026-10-12). The provider hint is held by the hosted portal during
authorization. Only the authorization code and matching one-use state return to
the trainer. Signing in is the trainer's first step: a configured build keeps
the sign-in screen open until the account is active, and a stored refresh token
counts as an account while offline. The same provider identity always resolves
to the same account; `GET /v1/names/check` answers whether a name is free before
the claim, and the claim at `PUT /v1/account` stays the authority.

The native client keeps state and code verifier in memory, expiring after five
minutes; exchange happens off the game/selection path. Tokens do not appear in
URLs, local API responses, exception text, request logs or public profiles.
Refresh tokens use Windows Credential Manager scoped by issuer, OAuth client
and build channel. Access tokens remain in memory. No plaintext fallback.
Cancellation invalidates the pending attempt; duplicate/wrong/expired callbacks
fail. Refresh rotation replaces the stored refresh token before reuse.

The Worker verifies asymmetric ES256/RS256 JWTs through maintained `jose`, with
fixed configured issuer, `aud=authenticated`, current expiry, UUID subject and
the exact registered `client_id`. ID tokens are rejected as API credentials.
JWT validity never bypasses current account status, invitation, consent, forced
rename or owner predicates. Admin routes additionally verify Cloudflare Access
identity and an explicit administrator allowlist; never accept submitted roles.

Sources: [Supabase OAuth setup](https://supabase.com/docs/guides/auth/oauth-server/getting-started),
[flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows),
[token security](https://supabase.com/docs/guides/auth/oauth-server/token-security).
Configured providers, real desktop return and deployed CPU are required staging
checks; local fixtures do not prove them.

`core/build_channel.py` is the packaged channel authority. Stable defaults retain
existing behavior. Beta: `SM64TrainerBeta`, display `SM64 Trainer Beta`, separate
LocalAppData profile, port8072, public updates disabled, no stable-data migration.
Global recorder ownership and shared emulator installation metadata stay shared.
Packaged metadata contains public community URL, Supabase project URL,
publishable key and OAuth client ID. A private beta build may also carry one
invitation capability, with reuse and expiry controlled by the service. It is
not an operator credential and is sent
only after authenticated login; snapshots and diagnostics omit it. Keep that
build private. No provider secrets or account refresh/access tokens are packaged.

## HTTP contracts

All `/v1` account/ghost responses are JSON with `v:1`; failures have a bounded
`error:{code,message}`. Unknown fields/parameters, invalid types and unsupported
versions fail. Remote authenticated responses are `Cache-Control: private,
no-store`; private internal cached entries use only validated canonical keys.
Admitted account/ghost access reserves an exact account/global daily budget
ticket, including cache hits. A separate fixed daily entry gate also bounds
authenticated lookups before admission; its limits and rejected-request costs
are specified in the [service contract](../cloud/community/README.md).
Burst limits shed traffic before expensive operations.

| Route | Contract |
| --- | --- |
| `GET /v1/config` | Public config:protocol/limits/policy versions, auth public fields, canonical catalog generation. No secrets. |
| `GET /v1/account` | Token-free profile:uid,name,status (`new`,`active`,`rename_required`,`suspended`,`deleted`,`not_invited`),sharing,terms_version,privacy_version,sheet_runner,sheet_verified. |
| `POST /v1/account/invitation` | Authenticated `{token}` beta capability, stored as SHA-256 only. Legacy single-use binds one UID; reusable admits independent UIDs without consumption. Reusable expiry0 means until revoked. Membership is atomically capped unless the operator explicitly enables uncapped mode. Retries preserve profile/visibility epoch; revoked, deleted and suspended accounts cannot re-invite themselves. Request/storage limits always apply. Invalid/unavailable capability returns403 `invite_unavailable`. |
| `PUT /v1/account` | Initial profile `{name,sharing,terms_version,privacy_version,sheet_runner:null|string}`; server requires current policies and invitation. No client owner/status/verification fields. |
| `PUT /v1/account/name` | `{name}`; supports forced rename, retains data and consent. |
| `PUT /v1/account/sharing` | `{enabled:bool}`; opt-out stops uploads; withdrawal is separate. |
| `GET /v1/catalog` | Canonical supported portable targets, clocks/ROMs/strategies, generation and integer frame cutoffs. Server derives every published subdivision. |
| `GET /v1/pool` | Validated target,clock,rom,scope,strategy (Strategy only),min_step,max_step,base_step,cursor. Bounded canonical band; includes baseline rung for faster same-rung fallback. Returns recordings,coverage/generation,next_cursor. Truncation is incomplete, never proof that higher ranks have no entries. |
| `GET /v1/search` | Same target/clock/ROM/scope identity plus bounded Unicode player prefix and keyset cursor; at most20 rows. Indexed prefix range, no wildcard/substring SQL. |
| `GET /v1/recordings/{id}` | Current metadata/publication gate, including explicit pins; recording IDs are immutable64-hex identities independent of public names. |
| `GET /v1/recordings/{id}/asset` | Auth/status/visibility/owner publication gate before immutable compressed bytes; Content-Type application/octet-stream. |
| `POST /v1/uploads` | Admission `{target,clock,rom,strategy,frames,raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,codec:"ghc1",generation}`. Server rejects unsupported catalog entries and derives UID/bin. Returns `upload_id,expires_at` or `already_present/not_faster`. |
| `PUT /v1/uploads/{upload_id}` | Bounded streamed GHC1 bytes; grant binds exact hashes/lengths/identity/generation and is single-use/idempotent. Decode/native validation precede conditional winner publication. |
| `PUT /v1/pins` | At most4 recording IDs; finite expiring leases preserve replaced winners subject to account/global physical-byte limits and publication visibility. |
| `POST /v1/account/withdraw` | Explicitly confirmed withdrawal of own published ghosts; visibility changes before physical collection. |
| `GET /v1/account/export` | Own bounded profile/ghost metadata export; never other account data or credentials. |
| `POST /v1/account/delete` | Explicit confirmation; mark current status/visibility first, collect own objects through reconciliation. No raw SQL or arbitrary delete selectors. |
| `POST /v1/admin/invites` | Access/admin guarded; admit/revoke a specified Auth UUID. New users may authenticate but cannot access shared resources before invitation. |
| `POST /v1/admin/reclaim` | Guarded `{name,expected_owner,destination_uid,transfer_sheet:bool,reason,confirmation}`; audited atomic conditional name transfer, displaced owner forced to rename, both owners retain all data. |

The service owns details of cursors, epoch keys, reservations and reconciliation.
Authenticated mutations carry a UUID `Idempotency-Key`. Retries of one logical
operation reuse that key; another operation uses a new key. Account mutation
replays must report current account state rather than reviving an obsolete claim.
Public owner identifiers are the Auth UUID under the configured, fixed issuer.
Catalog exports omit disabled or ambiguously bound segments, using the existing
portable binding rules. Responses have a four-MiB catalog limit and a one-MiB
limit for other JSON. Fixed rank ranges can span all 45 subdivisions; indexed
pages and bounded pagination expose incomplete coverage explicitly.
Keep response identity names above consistent; extensions require this contract
to change before dependent writers. Model cutoffs are exported from existing
rank derivations, not manually restated in a second ranking implementation.

Metadata row: `{id,player_id,player_name,target,strategy,clock,game_version,frames,
raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,codec,generation,step,
provenance:"community-unverified",structurally_valid:true,full_start:true}`.
`target` is the existing portable star/segment descriptor; enabled local segment
binding must be unique. Server-produced eligibility is structural, not anti-cheat
certification. The client regrades frames using its current strategy/Overall
model and maps public rows into existing `PortableGhost` objects.

## Lossless bounded GHC1 transport

Little-endian envelope20bytes: `<4s4I>` = `GHC1`, decoded raw native byte length,
native header length, native record length, native record count. Then the exact
unchanged native header (160bytes). For each block: `<2I>` block record count,
compressed byte length; followed by a single zlib-wrapped Deflate6 stream of
byte-column-shuffled native records. Every block except the final has256 records;
final has1–256. Reject trailing bytes, extra streams, wrong counts/sizes and
decompression exceeding the declared/allowed block size. Unshuffle reverses
`join(raw_records[offset::record_bytes] for offset in range(record_bytes))`.
Hash the exact restored header+records and compare raw SHA with admission.

Supported unchanged native formats: GHO4(record652/sample636/version5),
GHO5(1268/1252/6), GHO6(1276/1260/7), GHO7(1284/1268/8). Header/numeric flags,
record prefixes, clock/counter identity, finite/bounded poses/camera/root data,
required fields and pad values must validate. Unknown pads remain unknown.
Native max records is100000; stricter16MiB decoded/2MiB compressed cloud limits
apply before allocations. Larger lawful clips remain locally playable.

No wire compression or identity conversion changes committed native files.
Producer header contains recording timestamps/runtime markers; disclose this
metadata in the privacy notice rather than claiming that pose bytes lack it.

## Python seams and ownership

Client worker owns `community/**` and its tests. `CommunityConfig` accepts public
URL, SupabaseURL/publishablekey/OAuthclientid, callbackURL, channel and enabled;
localhost HTTP is allowed only in explicit development fixtures, HTTPS otherwise.
Promote `httpx` to runtime and add Authlib (root owns dependency/lock edits).

`CommunityAccount`: cached token-free `snapshot()`; async `restore()`,
`begin_sign_in(provider)->{attempt_id,url}`, `complete_sign_in(attempt_id,code,state)`,
`complete_profile(name,*,terms_version,privacy_version,sharing=False,sheet_runner=None)`,
`rename(name)`, `set_sharing(enabled)`, `sign_out()`; synchronous
`cancel_sign_in(attempt_id)`. Inject HTTP/token store for meaningful tests.

`CachedPopulation` wraps local provider with the existing synchronous
generation/metadata_snapshot/get/search/resolve/close contracts. Cache reads
never perform network, including under GhostSession selection locks.
`owned_player_ids(local_id)` returns local+authenticated aliases;
`coverage(identity)` reports bounded actual coverage. Remote paths resolve only
after background validated download. Keep old playable roster until replacements
are ready; retain safe full-start adoption.

`CommunitySync`: async `start/stop/search(identity,prefix,*,cursor=None)`;
synchronous `request_pool(identity,*,frame_windows,pinned_ids=())`,
`request_assets(recording_ids)`, thread-safe `offer_source(source)` with bounded
coalescing queue. Pool requests map frame windows to canonical server bins;
cache keys never contain exact PB or unrelated local rank integers. Stop has
bounded cancellation and joins owned work before replay/provider teardown.

Automatic pool metadata refreshes at most every 120 seconds per canonical
request. Chosen-rival metadata refreshes every ten minutes; its hard publication
lease also expires after ten minutes offline. Logout and local withdrawal still
clear the account view immediately. A fresh visibility response can reuse
validated cached bytes only when both stored hashes match. Pin retention leases
renew once per day or when their owner/IDs change; the server lease lasts seven
days. Static catalog data stays cached for an hour, with `generation_changed`
invalidating that lease before the next operation.

The actual client cadence test covers eight hours with four chosen rivals,
twenty two-call uploads and eight deliberate account-panel refreshes: 493
ticketed Worker operations against the 500/day account limit. Public config
reads and provider OAuth calls do not consume that account ticket. The UI's
ten-second status polling reads local cached state. Additional searches,
account-panel opens, target changes and failures consume the remaining budget;
the test does not promise unlimited interaction under a fixed daily quota.

HTTP streams have byte bounds and total deadlines (180 seconds for selected
ghost assets, 120 seconds otherwise), in addition to idle read timeouts.
Transient connection errors receive at most three immediate attempts with
jitter. Rate limiting and exhaustion trigger an account-scoped cooldown using
bounded `Retry-After`; they do not repeat three immediate requests. The worker
retains at most 32 transiently failed source offers, coalesces each canonical
bin to its fastest pending source and retries without another PB event. Grant
and publication retries keep their mutation keys until the grant expires or
catalog generation changes. An expired upload grant replaces its grant key
before retry.

Only a completed, certified own run with sharing enabled at attempt enqueue
creates a durable upload intent. Its account UID and consent generation are
captured before queue or preservation delay. The local outbox stores at most
256 intents / 1 MiB of metadata and immutable retry keys, never tokens or another
copy of the ghost. Normal app close preserves intents. Startup/login retries
the same owner's fastest eligible run per canonical subdivision, admitting at
most eight recovered winners per minute. Historical, imported and bundled
recordings are never assigned to a new account.

Explicit logout or disabling sharing durably cancels pending intents and blocks
older qualification/recovery jobs. Disabling sharing takes effect locally before
its remote call completes. Deleted or changed clips, unsupported catalog
identities, capacity eviction or failed local storage can prevent retry; local
PB/playback remains separate. One serial contribution task runs beside the
practice IO task. Initial pool/assets run first, and a blocked upload does not
delay later rival downloads. Their shared catalog refresh is deduplicated and
retains its one-hour lease.

Source offer is a dict: `{target,clock,game_version,strategy,frames,path,raw_sha256,
full_start:true,valid:true}` prepared by existing native qualification off-thread.
Root offers before/independently of local-ladder same-bin skip. Native source
bytes are read and compressed only on the community background worker; unknown
or unsupported identity refuses sharing without damaging private PB playback.

Root owns `main.py`, server lifecycle/community API/request guards, ghost session,
rival/recorder qualification composition, shared UI/API/onboarding, dependency
files and final docs. Service worker owns `cloud/community/**` + local tests.
Beta worker owns channel/path/updater/desktop identity and beta builder/tests;
it leaves main.py and community config to root/client respectively. Writers
use separate worktrees, commit only owned files and report tested commits.
