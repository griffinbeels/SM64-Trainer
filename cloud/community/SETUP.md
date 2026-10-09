# Set up an operator-only cloud test

This guide matches protocol v1 and the service under `cloud/community`.
Use Discord, Google and Twitch first. Email signup/recovery can follow later. Creating
accounts/resources, enabling R2 billing, setting remote secrets, applying remote
migrations and deploying are operator actions; this guide does not perform them.

Choose one HTTPS origin on an existing Cloudflare-managed domain, for example
`https://community-beta.example.com`. Replace this example everywhere with the
same actual origin. The current config disables `workers.dev`; add a Worker
Custom Domain route for the chosen hostname. This needs an active zone you own,
and the hostname cannot already have a conflicting CNAME. Domain registration
is separate from the service's free allowances.
[Cloudflare Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).

## 1. Prepare locally

From `cloud/community`, using Node 24:

```powershell
npm ci
npm test
npm run catalog:check
npm run build
npm run test:runtime
```

These checks use local fixtures. The runtime command builds a Wrangler dry-run
bundle and uses isolated workerd/D1/R2, with provider requests intercepted. It
does not deploy or prove real OAuth, distributed cache behavior or Free CPU fit.
On this Windows machine, agents run subprocesses through `scripts/run-local.py`
as hidden, below-normal two-core jobs and serialize heavy jobs with other agents.

## 2. Create staging accounts and storage

Use a separate **Supabase Free project**. Choose the explicitly approved Workers
plan and a measured CPU limit. Griffin approved **Workers Paid** for the current
private beta; its deployment uses a **100 ms** per-invocation CPU limit. This
is a resource bound, not a monthly spending cap.
Supabase Free includes social providers and OAuth 2.1 Server, currently 50,000
MAUs and two active projects; projects can pause after one inactive week. OAuth
Server is still beta and has no separate charge; its users count toward MAUs.
[Supabase pricing](https://supabase.com/pricing),
[OAuth server setup](https://supabase.com/docs/guides/auth/oauth-server/getting-started).

In Cloudflare, activate R2 through Storage & databases > R2 > Overview. This
includes checkout and a valid payment method for usage billing. The CLI step
below creates a **Standard** bucket named `sm64-community-beta-ghosts`. Keep both
`r2.dev` access and bucket custom-domain access disabled. The Worker uses its R2
binding; do not create S3 credentials for it.
[R2 activation](https://developers.cloudflare.com/r2/get-started/),
[billing/payment](https://developers.cloudflare.com/billing/understand/billing-policy/),
[private bucket defaults](https://developers.cloudflare.com/r2/buckets/public-buckets/).

After operator approval for remote creation, from `cloud/community`:

```powershell
npx wrangler login
npx wrangler d1 create community
npx wrangler r2 bucket create sm64-community-beta-ghosts
```

Copy the new D1 ID into `wrangler.jsonc` under `d1_databases`; preserve binding
`DB`, database name `community` and `migrations_dir: "migrations"`. Replace the
R2 bucket placeholder with the chosen bucket name; preserve binding `GHOSTS`.
Use a **new empty staging database**, then apply the actual migration:

```powershell
npx wrangler d1 migrations apply community --remote
```

The existing `npm run migrate` command is local-only. A populated database needs
a reviewed incremental migration, not a replay of the initial schema. In the
new database's D1 console, initialize `settings.generation` with the current
validated digest from `catalog.json`:

```sql
UPDATE settings
SET generation = '89671c5769e024241a41290b19328b7b73f1eafddbb03b65cae1e50d3b99996e'
WHERE id = 1 AND generation = '';
SELECT generation, enabled FROM settings WHERE id = 1;
```

Check that the selected digest matches `catalog.json`; use its actual digest if
the canonical catalog changed before setup. Existing service generations require
the reviewed regrade flow rather than this empty-database initialization.
[D1 creation](https://developers.cloudflare.com/d1/get-started/),
[D1 migration command](https://developers.cloudflare.com/workers/wrangler/commands/d1/#d1-migrations-apply),
[R2 bucket creation](https://developers.cloudflare.com/r2/buckets/create-buckets/).

## 3. Configure Supabase and its public desktop client

In Authentication > URL Configuration, use the service origin as **Site URL**
and register the two hosted redirect URLs below. In Authentication > OAuth
Server, consciously enable the beta and use Authorization Path
`/auth/authorize`. In Authentication > OAuth Apps, add **SM64 Trainer Beta** as a
**Public** client with token endpoint authentication method `none`, authorization
code and refresh token grants, and the exact native redirect below. Request only
`openid email profile`. Record its client UUID; there is no desktop client secret.

| Where to enter it | Exact value using the example origin |
| --- | --- |
| Supabase Site URL | `https://community-beta.example.com` |
| OAuth Server Authorization Path | `/auth/authorize` |
| Supabase general Redirect URLs | `https://community-beta.example.com/auth/callback` and `https://community-beta.example.com/auth/recovery` |
| Public OAuth client Redirect URI | `http://127.0.0.1:8072/api/community/auth/callback` |
| Provider redirect in Discord/Google | `https://<project-ref>.supabase.co/auth/v1/callback` |

These redirects serve different steps. Register complete exact URLs, without
wildcards or a substituted `localhost`, port or path. Actual dashboard acceptance
and end-to-end use of the fixed loopback callback are staging gates. The hosted
portal starts at `/auth/start`, authenticates through `/auth/callback`, displays
consent at `/auth/authorize`, and returns only code/state to the native callback.
[OAuth setup](https://supabase.com/docs/guides/auth/oauth-server/getting-started),
[OAuth flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows),
[general redirect configuration](https://supabase.com/docs/guides/auth/redirect-urls).

Ensure the active Supabase JWT signing key uses **ES256 or RS256**, not HS256.
Keep the normal access-token claims: `aud: authenticated`, Auth UUID `sub`,
`role: authenticated`, and the registered OAuth `client_id`. The service validates
all of these using fixed project JWKS. Browser session and ID tokens do not grant
desktop service access.
[JWT signing keys](https://supabase.com/docs/guides/auth/signing-keys),
[OAuth access-token claims](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows).

## 4. Add Discord, Google and Twitch

For Discord, create an application in the
[Discord Developer Portal](https://discord.com/developers/applications). Under
OAuth2 > Redirects, add the **Supabase provider callback** from the table. Copy
the provider Client ID and Client Secret into Supabase Authentication > Sign In /
Providers > Discord, then enable the provider. A bot and guild permissions are
not needed for this sign-in flow.
[Official Supabase Discord instructions](https://supabase.com/docs/guides/auth/social-login/auth-discord).

For Google, create a Google Cloud project and configure Google Auth Platform's
branding, support/contact and audience. Create an OAuth client of type **Web
application**. Its authorized JavaScript origin is the service origin; its
authorized redirect URI is the **Supabase provider callback**. Copy that
provider Client ID/Secret into Supabase's Google provider and enable it. Configure
only `openid`, email and profile access. Review the real application's name/logo,
policy links and provider warnings before inviting friends.
[Official Supabase Google instructions](https://supabase.com/docs/guides/auth/social-login/auth-google).

For Twitch, register an application in the
[Twitch Developer Console](https://dev.twitch.tv/console/apps) with the same
**Supabase provider callback**. The developer account needs verified email and
2FA. Set the application's Client Type to **Confidential**. Under Applications >
Manage, choose **New Secret**, then enter the application's Client ID/Secret in
Supabase's Twitch provider and enable it. Supabase handles the provider exchange;
no Twitch secret or provider token belongs in the desktop package. No streaming,
chat, moderation or channel-management permissions are required for sign-in.
[Official Supabase Twitch instructions](https://supabase.com/docs/guides/auth/social-login/auth-twitch),
[Twitch application registration](https://dev.twitch.tv/docs/authentication/register-app/).

Start in Google's Testing state. Current Google guidance exempts basic identity
scopes (`openid`, `email`, `profile`) from the usual test-user allowlist
restriction. Supabase UUID invitations remain our private-beta admission gate.
Workspace administrators can still block an OAuth app.
[Google app-state rules](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview).

Provider secrets stay in Supabase/provider consoles; none belong in the beta
package, source, public Worker vars or this guide.

## 5. Protect operator routes with Access

Create a Cloudflare Zero Trust Free organization. Under Access controls >
Applications, create a self-hosted application with the service hostname and
path `/v1/admin/*`. Add an Allow policy containing only actual operator emails
and choose an operator login method. Keep the normal portal/API paths outside
this admin policy so the desktop can use its Supabase tokens. Access is for
operators; friends do not need Access seats for this service.
[Access application setup](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/),
[Access plans](https://www.cloudflare.com/plans/zero-trust-services/).

Use `https://<team>.cloudflareaccess.com` as `ACCESS_ISSUER`. Copy the application's
Audience (AUD) tag into `ACCESS_AUD`, and put the same approved emails in the
comma-separated `ADMIN_EMAILS`. The Worker independently verifies Access JWTs
and the explicit email allowlist.
[AUD and JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).

## 6. Fill public configuration and store secrets

Keep `SERVICE_ENABLED: "false"` during preparation. Fill the public vars and
bindings in `wrangler.jsonc`:

| Field | What to supply |
| --- | --- |
| `SUPABASE_URL` | Fixed staging project's HTTPS origin. |
| `SUPABASE_PUBLISHABLE_KEY` | Modern browser-safe `sb_publishable_...` key. |
| `OAUTH_CLIENT_ID` | Registered public OAuth client UUID. |
| `NATIVE_CALLBACK` | Exact port-8072 callback from the table. |
| `ACCESS_ISSUER`, `ACCESS_AUD`, `ADMIN_EMAILS` | Reviewed Access application and operator identities. |
| `OPERATOR_NAME`, `OPERATOR_CONTACT` | Real accountable operator and reachable contact. |
| `TERMS_VERSION`, `PRIVACY_VERSION` | Versions of the reviewed notices. |
| `POLICY_URL` | `https://community-beta.example.com/policies` on the same service origin. |
| `CATALOG_GENERATION` | Validated `catalog.json` digest, matching D1 settings. |

Add the chosen Custom Domain in Worker Settings > Domains & Routes, or use its
documented `routes` entry with `custom_domain: true`. Keep gateway caching
disabled, the two named cache exports private, and R2 direct public access off.

Create an independent random **CURSOR_SECRET** of at least 32 characters. Create
a dedicated Supabase server **secret API key** (`sb_secret_...`) for this backend
and store it as **SUPABASE_SECRET_KEY**. This credential only goes to the server
for queued Auth account deletion; it is never a publishable key or user token.
Enter both through Worker Settings > Variables and Secrets as type **Secret**,
or Wrangler's protected interactive secret input. Do not paste them into chat,
source, command arguments or packaged config.
[Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys),
[Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/).

**Remote version caveat:** `wrangler secret put` immediately deploys a new Worker
version. It belongs in the approved remote stage. `wrangler versions secret put`
creates a held version instead, but does not deploy that version. Dashboard
secret changes also require its Deploy action. Initial code deployment must
keep the gateway disabled; configure both secrets before enabling it.

Build before the approved first deployment:

```powershell
npm run build
npx wrangler deploy --dry-run
```

After reviewing origin, bindings, notices, providers and Access, the operator
uses `npx wrangler deploy` for the approved disabled staging version. Then enter
the two server secrets in the dashboard, or use these interactive commands:

```powershell
npx wrangler secret put CURSOR_SECRET
npx wrangler secret put SUPABASE_SECRET_KEY
```

Each command creates and deploys a version. Keep `SERVICE_ENABLED` false for
both; a missing secret also keeps readiness closed. To hold the new version
instead, use `wrangler versions secret put` and review it before deployment.
Review the implemented `/policies` page during the controlled test window. It
discloses automatic sharing on new-account completion, the Settings opt-out, separate policy acknowledgement,
native timing/input/runtime metadata, unverified provenance, withdrawal/copy
limits and asynchronous deletion. Missing operator/contact remains fail-closed.

## 7. Enable an operator test window and invite explicitly

`SERVICE_ENABLED: "false"` also blocks `/v1/config` and the portal. Real OAuth
and authenticated CPU tests therefore need a controlled operator-only window:
enable the configured staging gateway, with no friend invitations yet. The
empty schema's `settings.enabled` is 1; keep it admitted for this window. Unknown
valid Auth identities can inspect `not_invited`, but cannot retain account/quota
rows or use pool/upload routes. Only Access-admin invitation persists an account.

Sign in using the operator test identity and locate its **Auth UUID** in
Supabase Authentication > Users. After authenticating through the Access-protected
admin path, submit `POST /v1/admin/invites` from a same-origin operator session.
Use `Content-Type: application/json`, a fresh UUID `Idempotency-Key` reused only
for that operation's retry, and this exact request shape:

```json
{
  "subject": "<the operator test Auth UUID>",
  "invited": true,
  "reason": "Private beta operator test",
  "confirmation": {
    "phrase": "CHANGE INVITATION",
    "issued_at": 0
  }
}
```

Replace `issued_at` with the current numeric Unix seconds immediately before
sending; the confirmation is accepted for five minutes. Access supplies the
verified operator assertion to the Worker; do not send a fake role or a browser
Supabase bearer token as admin authority. Use this same endpoint with
`invited: false` to revoke admission. Persisted owners retain export/removal.

## 8. Rebuild the beta with four public service values

An offline beta does not pick up cloud configuration from runtime environment
variables. Prepare a private local JSON config with only these fields:

```json
{
  "schema": 1,
  "channel": "beta",
  "build_id": "friends-cloud-test-commitid",
  "community_url": "https://community-beta.example.com",
  "supabase_url": "https://<project-ref>.supabase.co",
  "supabase_publishable_key": "sb_publishable_REPLACE_WITH_PUBLIC_KEY",
  "oauth_client_id": "11111111-2222-4333-8444-555555555555"
}
```

Replace all example values with verified public staging values. The client ID
must be the registered canonical UUID. Secret keys and partial configurations
are rejected. From the repository root, use the existing builder/environment:

```powershell
python tools/build_beta.py --config C:/private/public-beta-config.json --check
python tools/build_beta.py --config C:/private/public-beta-config.json --ffmpeg D:/ffmpeg/bin/ffmpeg.exe
```

Use the actual existing FFmpeg path. Follow [the private-beta build guide](../../docs/private-beta.md)
for native/seed prerequisites and ZIP/receipt verification. Test the packaged
app on port **8072**; stable port 8064 and source port 8065 are different clients.
Never add service secrets to this config or the archive.

## 9. Prove the cloud gates before inviting friends

Record evidence from the actual staging service and packaged beta:

- Discord, Google and Twitch sign-in, provider hints, policy acknowledgement, consent,
  code/state return to port 8072, refresh and sign-out. Browser tokens never
  return to native. Reject wrong state/client/redirect and ID tokens.
- Unknown-user and revoked-user admission; Access denial and operator allowlist;
  guarded invitation/reclaim and preserved recording ownership.
- Real eligible upload/download, fastest-winner races, immutable hashes, warmed
  metadata/asset revocation, pins and interrupted multipart recovery.
- Complete cold/warm authenticated HTTP admission/upload CPU, including JWT,
  decode/hash, D1 and R2. Free Workers allows **10 ms CPU per invocation**;
  local elapsed time and compression benchmarks do not prove the deployed fit.
- Actual D1 row/index writes, reads and query count for both HTTP and scheduled
  regrade/recovery/collection. Free D1 allows **50 queries per Worker invocation**;
  lower bounded maintenance work if necessary instead of silently upgrading.
- Withdrawal and deletion, including queued Supabase identity deletion, eventual
  R2 cleanup, retries/accounting and absence of secret logs. Downloaded copies
  cannot be recalled. Recordings remain community-unverified.

Only after these gates pass, invite friend Auth UUIDs through the same audited
endpoint and distribute the privately reviewed cloud-enabled ZIP/checksum.

## Email later, costs and shutdown

Discord/Google can be exercised before email delivery. For friend email/password
signup and recovery, configure custom SMTP in Supabase. Its default sender only
delivers to organization-team addresses and currently permits **two messages
per hour**. Register the same hosted callback/recovery URLs and test confirmation
and recovery from the real beta. SMTP provider charges are separate.
[Supabase SMTP requirements](https://supabase.com/docs/guides/auth/auth-smtp).

Free Workers includes 100,000 requests/day. Free D1 includes 5M reads and 100K
writes/day, **500 MB per database** and 5 GB total account storage. Standard R2's
monthly free tier includes 10 GB-month, 1M Class A and 10M Class B operations;
excess usage is billable. Other applications share account allowances. These are
not a $5 or other spending cap; application quotas and billing alerts cannot
prove a zero bill. Measure this pilot's actual usage and lower its budgets when
needed; never change the operator's approved plan implicitly.
[Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/),
[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/),
[D1 limits](https://developers.cloudflare.com/d1/platform/limits/),
[R2 pricing](https://developers.cloudflare.com/r2/pricing/).

`SERVICE_ENABLED: "false"` disables the public gateway. Changing
`settings.enabled` to 0 stops new user/admin tickets; already charged maintenance
can continue within budgets. Disable the cron/Worker in the operator console to
stop scheduled outbound work. Access revocation stops operator admission.

## Name limit evidence

The read-only Round 19 inventory measured the actual Library snapshot
`data/sheet_library.json.gz`, revision **2026-10-08T12:38:14**: **454 distinct
names** from its runner roster and runner fields. The longest,
`SullyLikesBigChungus`, has **20 Unicode code points / 20 UTF-8 bytes**. This is
the observed Library snapshot, not a count of names in the packaged seed.

Claimed names allow at most **20 Unicode code points and 63 UTF-8 bytes after
NFKC normalization and trimming**. Supplementary characters count once, although
their UTF-8 representation can consume four bytes. Japanese names are preserved;
full Unicode casefold, indexed uniqueness and hidden-control/reserved-name checks
remain in force. Search prefixes retain their separate 128-byte input limit.
The observed longest name motivates the count limit; it does not certify native
label width. Native display-width evidence belongs to the beta's rendering gate.

Provider documentation above was opened from primary sources on **2026-10-08**.
