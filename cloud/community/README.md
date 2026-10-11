# Community service private beta

One Worker, D1 metadata, private immutable R2 assets and maintained Supabase Auth
implement [protocol v1](../../docs/community-protocol.md). This checkout is disabled
with placeholders. Actual beta bindings and provisioning receipts are private
operator artifacts; this portable config does not describe their live state. Login proves
account ownership. Recordings remain **community-unverified**. Structural native
checks cannot attest honest play, ROM identity, full start, or 100-coin completion.

## Local verification

Use Node 24 in this folder: `npm ci`, `npm test`, `npm run catalog:check`,
`npm run build`, then `npm run test:runtime`. The last command is a Wrangler
dry run followed by isolated local workerd/D1/R2 and intercepted JWKS. It cannot
deploy. test/portal-smoke.py uses existing Python Playwright/headless Chrome,
intercepts every request, and tests the real bundled portal/local assets.
Generated public/portal.js, node_modules and .wrangler are ignored.
Build the portal before any operator preview.

On this Windows machine, scripts/run-local.py owns a hidden, below-normal
two-core process tree, kills descendants on exit and has a 600-second timeout.
No check uses the live trainer, recorder, existing browser profile or user DB.
npm run dev deliberately starts a local server; stop it when finished.
Never use remote storage for local checks.

## Operator setup

Explicit approval is required before resource/account changes. Keep R2 public
access, r2.dev and custom-domain object access disabled. Only the default Worker
gets a public route. MetadataCache and AssetCache are private loopback exports.
Apply migrations in order to a new staging D1 database. An existing 0001 database
gets only migrations/0002_friend_beta.sql; never replace its initial schema.
Fill D1/R2 bindings and public
vars in wrangler.jsonc for one owned HTTPS service origin. Supply actual operator
name/contact and published policy URL on that same origin. Keep SERVICE_ENABLED
false until policies, providers, admission, secrets and staging gates pass.
The implemented /policies page renders required operator/contact and policy
versions with HTML escaping. Point POLICY_URL at that same-origin route and
review its Terms/Privacy before enabling. It states the actual public fields,
native runtime/input data, providers, opt-in, moderation, withdrawal/copy limits,
asynchronous deletion and minimal retained tombstone/audit records; it makes no
compliance, age or jurisdiction promise.

| Field | Meaning |
| --- | --- |
| SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY | Fixed HTTPS project and browser-safe public key. |
| OAUTH_CLIENT_ID | Registered public OAuth-server client with token_endpoint_auth_method none. |
| NATIVE_CALLBACK | Exactly http://127.0.0.1:8072/api/community/auth/callback. |
| ACCESS_ISSUER / ACCESS_AUD / ADMIN_EMAILS | Fixed Access app and explicit operator email allowlist. Protect /v1/admin/* with Access. |
| CURSOR_SECRET | Independently generated server secret, at least 32 characters. |
| SUPABASE_SECRET_KEY | Server-only admin/service-role credential used solely for Auth identity deletion. |
| BETA_SHARED_PUBLISHERS | Optional JSON array of at most eight immutable Auth UUIDs. Friend beta uses Griffin's verified UID; `[]` allows only the caller's own recordings. Unset enables the general community policy. Display names never approve authors. |

Supply server credentials through Wrangler's secret input mechanism. Never
commit them, put them in literal shell arguments, public assets/config or desktop
metadata, or inspect an existing secret for fixtures. Readiness fails closed if
operator, policy, public client or admin-deletion secret configuration is missing.

Consciously enable Supabase's documented OAuth-server beta. Register authorization
UI /auth/authorize, exact native callback above, and provider/email redirect paths
/auth/callback and /auth/recovery on this service origin. Configure real Discord,
Google and email providers through Supabase/provider consoles. Native OAuth and
Google request only openid/email/profile. /auth/start validates the full native
PKCE authorize URL and retains the provider hint. Only code+state return to native.
Browser sessions stay on the hosted origin; no browser tokens are forwarded.

On an empty DB, initialize settings.generation with the validated catalog digest
or let the first maintenance regrade initialize it. A catalog rollout admits one
migration, regrades 20 retained recordings per invocation, then retires former
generation winners. During rollout, generation_changed blocks new cloud pool/
admission requests while local playback remains available. Finish an admitted
migration before replacing its catalog again.

## Private friend invitation

An installer can carry a fresh 32-byte base64url capability (43 characters).
The operator stores only its SHA256 in beta_invitations with an expiry. After
provider login, POST /v1/account/invitation accepts exactly `{token}`. A guarded
D1 transaction binds it to that authenticated UID once, creates the admitted
account and invalidates visibility caches. Retries by the same UID succeed while
the invitation remains valid. Other identities cannot reuse it. The user still
must claim a name and accept the current policies before ghost APIs work.
The capability is neither an Auth credential nor a Cloudflare/Supabase key.
Do not log it or ship the same capability to multiple friends. Whoever redeems
an unclaimed installer first owns its invitation; revoke/reissue a lost installer.
Invalid, expired, revoked and other-UID use returns 403 invite_unavailable.

beta_admission.max_members defaults to eight, with a hard schema maximum of 64.
The cap counts retained accounts, including tombstones, so revocation cannot
reopen an unlimited account-creation loop. Operator-created invitations respect
the same cap. Disabled D1 admission, revoked membership, deleted and suspended
accounts cannot redeem; only the operator can restore an existing revoked member.
Register/revoke capabilities through reviewed operator management; there is no
anonymous signup or public invitation-issuance endpoint.

Friend beta stores tester contributions under their real UID but serves only
approved shared publishers plus the viewer's own ghosts. The gate covers pool,
search, metadata, asset access and pins. The viewer/allowlist also binds private
cached query and cursor identities. Curated local ghosts use a separate trusted
manifest and do not become cloud-certified recordings.

## Catalog and wire details

catalog.json is the unchanged root exporter output: 264 portable target/ROM entries,
SHA256 89671c5769e024241a41290b19328b7b73f1eafddbb03b65cae1e50d3b99996e.
Shape: `{v:1,generation,entries:[{target,clock,game_version,strategies:[{name,
cutoffs:[{step,frames}]}],overall_cutoffs:[{step,frames}]}]}`. Hash entries only as
recursively sorted-key compact UTF-8 JSON without ASCII escaping. Inclusive maximum
frame cutoffs ascend by step, with nonincreasing frames; ties choose the highest
qualifying step. Disabled/ambiguous portable entries are omitted.

Account and metadata envelopes are direct `{v:1,...fields}`. Config uses flat
terms_version/privacy_version/terms_url/privacy_url. UID/player_id is the Auth
UUID under one fixed issuer. Admission disposition upload has upload_id and
epoch-seconds expires_at; already_present has recording_id; not_faster has neither.
PUT returns published with recording_id or not_faster. JSON mutations require a
UUID Idempotency-Key reused for one logical retry. Account mutation replays return
current state. Binary retries bind their immutable grant. HMAC cursors bind exact
identity/generation/epochs, canonical base64url and a five-minute expiry.

Pool pages scan 256 indexed rows across at most 45 inclusive subdivisions, with
four pages maximum. Search returns 20 rows and scans at most 64 indexed names per
page; an empty page can be incomplete with a next cursor. coverage.complete=false
always exposes truncation, including the fourth page. Never infer absence from it.
An empty first automatic-pool page returns at most four fastest compatible
eligible recordings using the same envelope. It retains target, clock, ROM,
generation, strategy/overall and publisher restrictions. Continued pages and
manual searches do not use this fallback. No unsupported ROM is relabeled.
Stale upload generations return 409 generation_changed before storage admission.

## Visibility, quotas and recovery

Admitted user/admin gateway calls, including cache hits, consume exact account and
global D1 tickets. One atomic batch inserts and advances both daily rows;
failed tickets roll back new rows as well as both counters. Ordinary Auth
identities only look up accounts; operator or one-use installer invitations create them. Unknown,
uninvited, suspended and deleted identities may read their status without
persistent writes. Unknown identities fail other calls before quota admission.
Persisted owners retain budgeted export/removal after revocation or suspension;
other denied calls fail before quota admission.
Admission additionally consumes daily bytes/operations and reserves account/global
storage in a guarded batch. Consumed daily bytes/operations never refund. Strictly
faster owned-slot publication wins; equal frames keep the prior winner. Publication
bumps the target epoch transactionally; names/moderation/withdrawal bump global
visibility. Current primary account/epoch gates precede cached pool metadata;
current publication/owner state precedes cached immutable bytes. New requests
begun after withdrawal commits deny that owner's data. In-flight/downloaded copies
cannot be recalled. Sharing off stops uploads; explicit withdrawal hides old rows.
Internal cache keys exclude tokens, forwarded headers, arbitrary URLs, exact PB
and search prefixes. Search is uncached.

GHC1 preserves native versions 4–7, 160-byte header, exact <=256-record blocks,
byte-column shuffle and single zlib Deflate6 streams. Hash, structural/finite
fields, clock continuity, portable clock/course and playable-pose checks precede
R2. Raw/encoded limits are 16 MiB/2 MiB before decoding allocations.

Multipart create, one part and complete reserve three Class A tickets; recovery
abort uses another. Persist multipart intent before any part write. Abort fences
expired receiving work before refund. A completed object instead becomes charged
physical storage and resumes publication. Losers/replaced/pinned assets and failed
deletes remain charged until confirmed physical deletion. Pins: four recordings,
seven-day leases. Collection prevents new pins after deleting-state admission
and waits for all remaining pin references, including expired prune backlog.
The internal MaintenanceAlarm Durable Object runs every 15 minutes and collects
at most 20 assets per invocation; monitor
backlog. Expired pins/receipts/quota history/terminal grants prune in bounded batches.
Indexed pruning handles up to 400 expired quota rows, 400 receipts and 200
terminal grants per tick (38,400/38,400/19,200 per day), above their configured
admission ceilings. Quota days older than seven days expire; admission creates
at most 30,001 rows per day including its global row. Outages
and exhausted Free D1 capacity can still create backlog; monitor it. Publication
and admission are transactionally fenced while a catalog migration is active.

Successful admitted gateway requests arm one fixed durable object if no alarm
exists. There is no public endpoint to choose a job, clock or resource. The alarm
schedules its next tick before external IO, then independently runs identity
deletion, catalog regrade and storage reconciliation. Persisted diagnostics contain
fixed outcome labels and timestamps, never provider errors. Individual failed
storage intents remain charged, receive a 15-minute retry and do not block other
due objects. Cron may remain an operator fallback; disable it when using only
alarms. Deploy the MAINTENANCE binding and friend-beta-maintenance-v1 SQLite DO
migration together.

Maintenance R2 work has separate exact UTC daily counters: 10,000 intents,
6,000 Class A and 6,000 Class B tickets. Exhausted player/global admission quotas
cannot starve cleanup. Exhausted maintenance allowance postpones IO and retains
charges. Stages handle at most 20 identities/recordings/assets per tick; provider
deletion has a ten-second deadline per identity. Fixed cadence is 96 ticks/day
even while idle. Alarms are at-least-once, so storage/state transitions remain
idempotent. [Cloudflare alarm behavior](https://developers.cloudflare.com/durable-objects/api/alarms/).

Limits in src/config.js: 64 MiB/account retained; 10,000,000,000 global physical+
reserved bytes; 24 MiB/account/day and 256 MiB/global/day uploads; 500 account and
30,000 global operations/day; 20,000 global Class A and 200,000 Class B tickets/day.
Downloads conservatively count Class B even when cached. Application limits do
not guarantee a spending cap, monthly storage averaging, D1 capacity or a $5 bill.
D1 guard/index/counter/maintenance writes and loopback Worker requests have costs.
Free D1 can exhaust before the application cap. Measure actual rows/CPU and lower
operator budgets accordingly; never change the approved billing plan implicitly.

429 responses supply Retry-After: UTC midnight for daily ticket exhaustion,
60 seconds for the pre-auth burst limit, and 300 seconds for storage/grant denials.
The burst binding is per location and eventually consistent; exact D1 counters
provide the accounting bound. [Cloudflare rate-limit binding](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).
Eight hours with a two-minute pool lease, four manual metadata renewals each ten
minutes and an hourly catalog lease uses 440 calls (55/hour) before login,
search, pin renewals and uploads. At 15-minute manual metadata renewals this is
376 calls (47/hour). Keep the 500/day cap and make client renewal deliberate;
outages must retain last-good playback and avoid retrying daily denials.

Kill switches: SERVICE_ENABLED=false disables the public gateway. Changing
settings.enabled to zero denies new user/admin tickets. Maintenance can still
process already charged storage/deletions within its reserved quotas. Access revocation stops
admin admission. Disable the scheduler/Worker in the operator console to stop scheduled
outbound work. Billing alerts observe spending; they do not cap it.

## Administrator and account deletion

Invites/revokes bind Auth UUID. Reclaim binds expected current owner, destination
UUID, explicit sheet-transfer choice, reason and fresh `{phrase,issued_at}` within
five minutes. Phrases: CHANGE INVITATION or RECLAIM plus the exact normalized name.
Both routes require Access, administrator allowlist and Idempotency-Key. Guarded
batch includes audit and receipt; displaced owners must rename and retain all
recording ownership. Submitted roles/raw SQL/bucket keys never confer authority.

Deletion tombstones visibility and queues Auth identity removal atomically;
identity_deletion=pending describes asynchronous work. Maintenance uses maintained
Supabase admin.deleteUser(uid), server-only secret, no persistent SDK session,
ten-second request deadline and 20 due identities maximum. Only deleted local
accounts qualify. Documented user_not_found counts as already deleted. Failures
use a five-minute lease, exponential retry and sanitized codes; eight failures
become operator_pending. Repair credentials/upstream availability and reset only
a reviewed UID's pending attempt. Never reactivate a deleted account for cleanup.
R2 removal retries separately and stays charged until confirmed. Minimal local
ownership tombstones/outbox/audits remain for replay/admission safety; the real
privacy notice must state their retention and asynchronous deletion behavior.

## Staging evidence before friend-cloud enablement

Measure complete cold/warm authenticated admission+upload CPU, including JWT,
decode/hash, D1 and R2. Node elapsed time/compression benchmarks/dry-run are not
proof of Free Workers' 10-ms CPU fit. Measure D1 rows read/written including
indexes for pools/search/publishing/recovery/identity deletion at proposed load.
Exercise races/crash recovery against actual staging D1/R2; warm global named
cache exports and verify withdrawal/name revocation. Complete real Discord,
Google, Twitch and consent through the packaged beta desktop callback. Email
signup/recovery needs a separately configured and exercised delivery service.
Check wrong state/client/redirect, ID-token rejection, Access admission/reclaim,
automatic Auth deletion, operator/policies, native timestamps/runtime disclosures
and absence of secret logs. Keep friend access closed until evidence exists. Local fixtures
do not prove real providers, cross-datacenter cache behavior or deployed CPU.

## Opened primary sources and unchanged assets

[Supabase OAuth setup](https://supabase.com/docs/guides/auth/oauth-server/getting-started),
[flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows),
[token security](https://supabase.com/docs/guides/auth/oauth-server/token-security),
[admin deleteUser](https://supabase.com/docs/reference/javascript/auth-admin-deleteuser),
[Auth errors](https://supabase.com/docs/guides/auth/debugging/error-codes),
[jose](https://github.com/panva/jose),
[Worker cache](https://developers.cloudflare.com/workers/cache/configuration/),
[cache keys](https://developers.cloudflare.com/workers/cache/cache-keys/),
[D1 batch](https://developers.cloudflare.com/d1/worker-api/d1-database/),
[R2 multipart](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/),
[R2 consistency](https://developers.cloudflare.com/r2/reference/consistency/).

Unchanged assets are local img/font resources; SVG contains only svg/path:

| File | Official source / SHA256 |
| --- | --- |
| discord-clyde-white.svg | https://cdn.prod.website-files.com/6257adef93867e50d84d30e2/66e3d7f4ef6498ac018f2c55_Symbol.svg / eb3bf5a2e8b9f9aa39c1b1d38aeaa7c2997084567634c55f4141aa39cf426cec |
| google-g.png | https://developers.google.com/static/identity/images/g-logo.png / d1ce9c2af0b10a7333abc99bc706f9a6a199e5b65bf3e3009624f076b8638e6a3 |
| google-sans.ttf | Google Fonts Google Sans Medium text subset for Continue with Google / 4bf72f2d546a9c96201dd8552b1825daf0dd87a97aa1c2eb19377c4da5e6caf1 |

Google Sans OFL accompanies its official subset. No external runtime font request.
See [Discord brand](https://discord.com/branding) and
[Google sign-in brand](https://developers.google.com/identity/branding-guidelines).
casefold.json is full C/F [Unicode 16 data](https://www.unicode.org/Public/16.0.0/ucd/CaseFolding.txt),
under the [Unicode license](https://www.unicode.org/license.txt). Runtime NFKC
normalization follows the deployed JavaScript engine; check it in staging.
