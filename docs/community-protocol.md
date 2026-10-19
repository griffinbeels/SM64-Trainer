# Community ghosts v1 and additive player pages v2

The community service uses Cloudflare Workers, D1 metadata, private R2 objects and
managed Supabase Auth. Public player names are mutable; the verified Auth
issuer/subject owns data. A valid login does not prove honest emulator play.
No ROMs, videos, passwords or complete practice histories are uploaded.

Claimed player pages add separate `/v2/` endpoints and the explicit
`progress-1` saved-PB disclosure. They do not expand strict v1 account/recording
metadata or silently renew global ghost consent. Their exact wire schemas,
additive migrations, D1 payload budget, revision/publisher fences and owner
privacy routes are maintained in the [community service README](../cloud/community/README.md#current-player-pages-v2).
The loopback page/settings adapters are documented in [the API](api.md#community-account-localhost-only).
Current saved-PB snapshots are display/comparison facts, including manual and
imported marks; they are not additional ghost evidence for rank calibration.

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

Use Supabase's maintained OAuth 2.1 server with a registered **public** native
client, `token_endpoint_auth_method=none`, S256 PKCE and exact redirect
`http://127.0.0.1:8064/api/community/auth/callback` for normal installations.
The legacy isolated beta retains its separately registered 8072 callback; the
Worker validates the configured client/callback pair. This deliberately uses
Supabase's documented beta OAuth-server feature, rather
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
and installation namespace. Normal beta and stable builds share that namespace;
the legacy isolated beta has its own. Access tokens remain in memory. No plaintext fallback.
Cancellation invalidates the pending attempt; duplicate/wrong/expired callbacks
fail. Refresh rotation replaces the stored refresh token before reuse.

The Worker verifies asymmetric ES256/RS256 JWTs through maintained `jose`, with
fixed configured issuer, `aud=authenticated`, current expiry, UUID subject and
the exact registered `client_id`. ID tokens are rejected as API credentials.
JWT validity never bypasses current account status, admission, consent, forced
rename or owner predicates. Admin routes additionally verify Cloudflare Access
identity and an explicit administrator allowlist; never accept submitted roles.

Sources: [Supabase OAuth setup](https://supabase.com/docs/guides/auth/oauth-server/getting-started),
[flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows),
[token security](https://supabase.com/docs/guides/auth/oauth-server/token-security).
Configured providers, real desktop return and deployed CPU are required staging
checks; local fixtures do not prove them.

`core/build_channel.py` is the packaged channel authority. Schema2 normal beta
and stable builds use `SM64Trainer.exe`, one LocalAppData profile, the same
account namespace, port8064 and public updates. A beta opts the installation into
future betas once unless the player has chosen; stable builds preserve that
choice. Only the legacy schema1 standalone beta uses `SM64TrainerBeta`, separate
practice data, port8072 and disabled public updates.
Global recorder ownership and shared emulator installation metadata stay shared.
Packaged metadata contains public community URL, Supabase project URL,
publishable key and OAuth client ID. A private beta build may also carry one
invitation capability, with reuse and expiry controlled by the service. It is
not an operator credential and is sent
only after authenticated login; snapshots and diagnostics omit it. Keep that
build private. No provider secrets or account refresh/access tokens are packaged.

## Public beta admission

`PUBLIC_BETA_SIGNUP=true` explicitly permits authenticated viewers to complete
normal profile setup without a packaged invitation. The default remains private
admission. Reading an eligible new account returns the setup state without
persisting an account; deliberate profile creation establishes admission with
the name and current policy acknowledgement. Authenticated read budgets still
apply before admission, and account/member budgets apply at creation.

Open signup does not restore revoked, suspended or deleted identities, reset a
profile, change sharing without consent, or bypass name uniqueness or ownership.
The private invitation endpoint remains available for private-mode clients.
Deployment mode and member-cap configuration are operator responsibilities;
choosing open beta does not disable request, upload or storage limits.

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
| `PUT /v1/account` | Initial profile `{name,sharing,terms_version,privacy_version,sheet_runner:null|string}`; requires current policies and admission through an invitation or enabled public signup. Public signup persists only on deliberate creation. No client owner/status/verification fields. |
| `PUT /v1/account/name` | `{name}`; supports forced rename, retains data and consent. |
| `PUT /v1/account/sharing` | `{enabled:bool}`; opt-out stops uploads; withdrawal is separate. |
| `GET /v1/catalog` | Canonical supported portable targets, clocks/ROMs/strategies, generation and integer frame cutoffs. Server derives every published subdivision. |
| `GET /v1/pool` | Validated target,clock,rom,scope,strategy (Strategy only),min_step,max_step,base_step,cursor. Bounded canonical band; the trainer asks for the whole ladder (at most 45 bins) so it can widen the rival range above, then below, until the list is full (round 49). Returns recordings,coverage/generation,next_cursor. Truncation is incomplete, never proof that higher ranks have no entries. |
| `GET /v1/search` | Same target/clock/ROM/scope identity plus bounded Unicode player prefix and keyset cursor; at most20 rows. Indexed prefix range, no wildcard/substring SQL. |
| `GET /v1/recordings/{id}` | Current metadata/publication gate, including explicit pins; recording IDs are immutable64-hex identities independent of public names. |
| `GET /v1/recordings/{id}/asset` | Auth/status/visibility/owner publication gate before immutable compressed bytes; Content-Type application/octet-stream. |
| `POST /v1/uploads` | Admission `{target,clock,rom,strategy,frames,raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,codec:"ghc1",generation}`. Server rejects unsupported catalog entries and derives UID/bin. Returns `upload_id,expires_at` or `already_present/not_faster`. Ghost ceiling: a run with `frames*100 > edge*110`, where `edge` is the strategy ladder's step-1 cutoff, answers `not_faster` before any upload budget, so deployed trainers skip it silently. |
| `PUT /v1/uploads/{upload_id}` | Bounded streamed GHC1 bytes; grant binds exact hashes/lengths/identity/generation and is single-use/idempotent. Decode/native validation precede conditional winner publication. The clip must bear out the admission's claims (422): `rom_mismatch` (the header's ROM layout, 1 = us, 2 = jp, differs from `rom`), `incomplete_inputs` (a record without its pad), `frames_mismatch` (the clip's own clock disagrees with `frames`; exact for a star, bounded for a segment). The trainer runs the same checks before uploading (`community/claims.py`). |
| `PUT /v1/pins` | At most4 recording IDs; finite expiring leases preserve replaced winners subject to account/global physical-byte limits and publication visibility. |
| `POST /v1/recordings/retract` | Owner-only `{raw_sha256,keep_strategy:string|null}`. Unpublishes the caller's recordings of those bytes under every other strategy (all when null), removes their winners and pins, expires their unfinished grants and bumps the affected scope epochs. Bytes are collected by reconciliation, as after withdrawal. Returns `{retracted:n}`. Persisted owners keep it after suspension, like withdrawal. |
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
after background validated download. A seat whose recording is still
downloading is listed as pending and does not play; retain safe full-start
adoption. A recording whose bytes or metadata fail validation leaves automatic
seats for the session; one the service refuses (gone or hidden) leaves them for
the retry interval. Network and quota failures never empty the lineup. The
cache never evicts a recording the lineup on screen uses (`keep`), so four
large seats may exceed the byte cap by at most their own size. Automatic seats
read only that lineup's list; a chosen rival outside it is looked up by id.
Incoming player names follow the cloud's own rule (`names.incoming_name`, the
twin of `names.js` checkedName): a recording whose name the cloud would refuse
is dropped from its page. A failed token rotation keeps the account's view;
only signing out or another account clears it.

`CommunitySync`: async `start/stop/search(identity,prefix,*,cursor=None)`;
synchronous `request_pool(identity,*,frame_windows,pinned_ids=())`,
`request_assets(recording_ids)`, thread-safe `offer_source(source)` with bounded
coalescing queue. Rival lists, recording downloads and contributions run on
three workers, so a 180-second download never holds up a list request. Pool requests map frame windows to canonical server bins;
cache keys never contain exact PB or unrelated local rank integers. Stop has
bounded cancellation and joins owned work before replay/provider teardown.

One rival list covers the whole ladder, so its request names the account, the
identity and the calibration revision, never the PB or rival settings: a new PB
re-picks locally. `request_pool` is safe to call every cycle. It queues nothing
while that list is queued or running, or while the list and the chosen rivals it
last served are inside their publication leases or a failure's back-off. Each
held list owns its rows (16 lists at most), so one strategy's list never evicts
another's. A held list and chosen-rival metadata renew once, a minute before
their hard ten-minute publication lease lapses, and only while that identity is
on screen with the overlay on; the lease also expires offline. Logout and local withdrawal still
clear the account view immediately. A fresh visibility response can reuse
validated cached bytes only when both stored hashes match. Pin retention leases
renew once per day or when their owner/IDs change; the server lease lasts seven
days. Static catalog data stays cached for an hour, with `generation_changed`
invalidating that lease before the next operation.

The actual client cadence test covers eight hours with four chosen rivals,
twenty two-call uploads and eight deliberate account-panel refreshes: 301
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

`retract_other_strategies(*,raw_sha256,keep_strategy)` runs when a run is
reclassified, from any thread. It drops pending offers of those bytes under
other strategies, refuses later ones, and saves one owner-bound retraction in
`pending-retractions.json` (at most 256, the newest keep per clip). The
contribution task sends it with a fixed Idempotency-Key and retries it like an
offer; it survives restart and logout until its owner is active again. Signed
out or sharing off, only local intents change. An older Worker's 404 drops it.

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
