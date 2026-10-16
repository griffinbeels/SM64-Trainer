# SM64 Trainer website deployment

The public website uses **sm64trainer.app**. GitHub releases provide
the downloads; Cloudflare Pages serves only the website's public files.
Production HTML contains the exact installer link, version, upload date and
release-notes link even when JavaScript is disabled. The browser does not call
the GitHub API or receive a token.

**auth.sm64trainer.app belongs to the existing community service.** Preserve
that hostname, Worker, Supabase/provider redirects, policies and native localhost
callback. Website setup requires no change to them.

The public beta landing page is **https://sm64trainer.app/beta/**. It uses the
same approved page template and public assets as the homepage. Anyone can join
this beta through the trainer's sign-in and signup flow; the public download
contains no invitation. Install once for future published beta and production
updates in the same installation and profile. Public signup remains a separately
controlled community-service deployment setting; a website build does not enable it.

## Current readiness — October 13, 2026

This is a dated operator-verified snapshot for the existing installation. Recheck
current settings before changing them; it is not a template for a new account.

| Item | Verified state |
| --- | --- |
| Pages project | `sm64-trainer`, Direct Upload, production branch `main` |
| Apex domain | `sm64trainer.app` custom domain **Active**, with a saved proxied apex CNAME to `sm64-trainer.pages.dev` |
| Production switch | `WEBSITE_DEPLOY_ENABLED=true` |
| CI credentials | Repository secret names `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are present; no values belong in documentation |
| Public app release | `1.9.1`, below the stable community floor in `website/release-policy.json` |
| Public website deployment | None yet; the live builder returns a launch hold until an eligible stable community release exists |

An Active domain and enabled switch do not establish that a website has been
uploaded. The release gate still applies. The first eligible deployment is also
the remaining end-to-end check of the stored CI credential's publishing access.
Authentication remains on its existing service and hostname. This dated snapshot
predates the public beta route; current builder eligibility is described below.

## Production build and release validation

For copy, screenshots and layout review, use the safe offline preview in
[website/README.md](../website/README.md). CI never publishes its sample artifacts.
The commands below validate real releases for an operator; they can return a
launch hold and produce no directory. App dependencies and a running trainer or
emulator are unnecessary.

```powershell
uv run --no-project --python 3.13 python -m unittest discover -s tests -p "test_*website.py" -v
$websiteBuild = Join-Path $env:TEMP ('sm64trainer-site-' + [guid]::NewGuid().ToString('N'))
uv run --no-project --python 3.13 python tools/build_website.py --output $websiteBuild
```

Use a new output directory for every build. The builder validates releases
before emitting deployable files. It copies only public assets and writes
`release.json`; source documentation, policy files and credentials are excluded.
Upload the generated directory, **never the source `website/` folder**.

For an explicitly offline production-builder fixture, add
`--releases-json <fixture-path>` with a JSON release object or list. Prefer the
safe preview helper for design review; never publish fixture-built output.
Fixture mode makes no release requests and never waits between attempts. Live requests accept `--attempts` from 1 to 5 and `--retry-delay` from
0 to 30 seconds; CI uses three attempts with a ten-second delay. A local
`GITHUB_TOKEN` environment variable is optional. Do not put a token in arguments,
fixtures, logs or generated files.

| Exit | Meaning | Output and publishing |
| --- | --- | --- |
| `0`, status `ready` | Complete eligible production release | Static website ready for review and publishing. |
| `0`, status `beta_only` | Complete eligible public beta, no community stable | `/beta/` offers beta; `/` explains the stable wait and links to beta. |
| `3`, status `launch_hold` | Neither an eligible stable nor public beta release | No deployable output; CI reports a launch hold. |
| `2` | Invalid metadata, incomplete newest candidate, or failed discovery | No deployable output; CI fails validation. |
| `0`, status `unavailable` | Explicit withdrawal refresh with no eligible release remaining | Static page removes direct installer links. |

The stable policy remains `website/release-policy.json`: **1.10.0 or later**.
Pre-community **1.9.1** cannot advertise community ghosts. Independently, `/beta/`
selects a complete published `v1.10.0-beta` or later beta core, with GitHub's
prerelease flag enabled. Beta-only launch does not need stable 1.10.0 to exist.
Private tester executables never qualify as public download assets. The highest
eligible SemVer wins in each channel, independent of publication dates; beta
ordering reuses the installer's contract, so `beta.10` follows `beta.9`.
Drafts, withdrawn releases, malformed tags, and channel/flag mismatches are
excluded. All six uploaded, positive-size
assets must have unambiguous versioned HTTPS links to this repository:

- `SM64Trainer.exe` and `SM64Trainer.exe.sha256`
- `SM64Trainer-full.zip` and `SM64Trainer-full.zip.sha256`
- `manifest.json` and `manifest.json.sha256`

`SM64Trainer.exe` is the bootstrap installer. Its displayed size is the uploaded
installer's size; it downloads the app payload during installation. Private
tester installers are never website downloads. The withdrawal marker
`<!-- sm64-withdrawn -->` is honored, matching the community release branch.
An incomplete newer eligible release in either channel blocks deployment rather than
silently selecting an older download.

Both routes are built from one release-feed snapshot. The root `release.json`
describes stable availability and includes a `beta` summary pointing to
`/beta/release.json`, which holds the beta's version, installer and checksums.
Without stable, root status is `beta_only` and no stable installer is advertised.
If one channel is unavailable while the other is ready, only the available
channel has direct downloads. A withdrawal refresh can retire either channel.

## Set up a new environment

For the existing installation, start with the readiness snapshot above rather
than repeating setup. For a new environment, prepare the empty project and CI
settings with publishing disabled while access and content are reviewed. Domain
preparation and file publishing are separate steps. Enabling the switch does not
bypass either channel's release validation.

1. Use the **Direct Upload** Pages project `sm64-trainer` in the owning
   Cloudflare account, with production branch `main`. Create it only if absent.
   An empty project has no public deployment. GitHub Actions owns its builds and
   uploads. A Direct Upload project cannot later switch to Pages Git integration
   without a new project.
2. In that account's **API Tokens**, create a custom API token with **Account /
   Cloudflare Pages / Edit**, restricted to the owning account. This CI token
   does not need DNS, Workers, D1, R2 or Supabase permissions. Do not copy the
   machine's broad Wrangler OAuth credential.
3. In `griffinbeels/SM64-Trainer`, open **Settings > Secrets and variables >
   Actions**. Add these repository settings:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Variable | `WEBSITE_PAGES_PROJECT` | `sm64-trainer`, or the exact existing Pages project name |
   | Variable | `WEBSITE_DEPLOY_ENABLED` | `false` while preparing; only literal `true` permits publishing |
   | Secret | `CLOUDFLARE_ACCOUNT_ID` | Owning Cloudflare account ID |
   | Secret | `CLOUDFLARE_API_TOKEN` | Scoped Pages Edit token |

4. Land the approved website, builder and `.github/workflows/website.yml` on
   `main` through the project's usual integration process. Production runs
   always check out current `main`, including release-tag events.
   `WEBSITE_SOURCE_SHA` records that checkout's actual commit in the build
   metadata; a release event's tag commit does not identify the website source.
5. Run **Actions > trainer website > Run workflow** with `retire_download`
   unchecked. Before either eligible channel exists, expect a successful **launch
   hold** and no production website candidate. Separate ready/unavailable/beta-only sample
   preview artifacts remain available for review, with `status: preview` and
   disabled downloads. After a complete stable or public beta release, review the production
   artifact and `release.json` while publishing remains disabled in a new environment.
6. At the approved community-release launch, set `WEBSITE_DEPLOY_ENABLED=true`
   and run the workflow again. If settings are prepared beforehand, publication
   of a complete matching stable or public beta release triggers the same workflow. Missing
   secrets or a disabled gate produce a visible **setup hold**, retaining the
   review artifact. They do not count as a deployment.

GitHub's built-in `GITHUB_TOKEN` reads releases with `contents: read`; no separate
GitHub personal token is needed. Setup checks receive only booleans indicating
whether Cloudflare settings exist. Credential values go to the official
deployment action. Cloudflare's [CI upload guide](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)
documents the Pages Edit permission and the two Cloudflare settings. The
[Direct Upload guide](https://developers.cloudflare.com/pages/get-started/direct-upload/)
covers project creation and deployment-mode choice.

CI pins **Wrangler 4.149.0** and runs `pages deploy ... --force --branch=main`.
In that version, the hidden `--force` option keeps deployment on Pages instead
of delegating a new static project to Workers. This is a hosting choice, not an
asset overwrite or deletion flag. Keep it when updating the CLI, and recheck
its meaning against [the pinned Pages command source](https://github.com/cloudflare/workers-sdk/blob/wrangler%404.149.0/packages/wrangler/src/pages/deploy.ts).
The local `pages project create` command can also delegate new projects when
an agent runs it; use Cloudflare's Pages dashboard or Pages project API when
preparing an empty Pages project.

## Custom domain preparation and launch checks

The existing apex domain is already configured; see the dated snapshot. Do not
remove or re-register it as part of a copy or screenshot change.

For a new environment, use the Pages project's **Custom domains > Set up a
domain** flow. The apex zone must be active in the same Cloudflare account as
the Pages project. Domain association, saved DNS, certificate verification and
website upload are distinct observations. Inspect **DNS > Records** for the
saved apex CNAME to the project's exact `*.pages.dev` hostname. A "Verifying"
state alone does not prove that a record was saved.

Cloudflare flattens apex CNAME records, so public CNAME queries can have no CNAME
answer even when the record is configured. Verify A/AAAA resolution and the Pages
domain status instead. See [CNAME flattening](https://developers.cloudflare.com/dns/cname-flattening/).

If adding `www`, configure an exact **www.sm64trainer.app -> https://sm64trainer.app**
permanent redirect, preserving path and query string. A wildcard redirect for every
subdomain would break `auth.sm64trainer.app`. Domain settings belong to the
operator; this workflow neither edits DNS nor attaches domains. See the
[custom domain guide](https://developers.cloudflare.com/pages/configuration/custom-domains/)
and [www redirect guide](https://developers.cloudflare.com/pages/how-to/www-redirect/).

Verify the public page on desktop and mobile, with JavaScript disabled as well.
Check all download buttons against the version and checksum links in
`https://sm64trainer.app/release.json`. Confirm the installer URL identifies that
exact GitHub tag and release notes open the same version. Confirm
`https://auth.sm64trainer.app/v1/config` and `/policies` still respond, then check
sign-in through the trainer's usual entry point.

## Automatic updates and withdrawal

The workflow runs for website/builder/asset-registry changes on `main`, matching
pull requests, release `published`, `released`, `edited`, `deleted` and
`unpublished` events, and manual dispatch. It has no recurring schedule.
Pull requests test their merge commit and build an artifact. They receive no
Cloudflare deployment settings and never publish. PR concurrency is separate
from production, so a new PR cannot cancel a pending production refresh.
Production runs discover current releases rather than trusting the triggering
release as the download candidate. GitHub documents the [release event types](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release).

For a stable release at or above the community floor or an eligible public beta, deletion/unpublishing,
or an edit adding the withdrawal marker, enables an explicit withdrawal refresh.
An earlier complete release in the same channel can replace the withdrawn one.
If none remains in a channel, CI removes that channel's direct download links.
If no channel remains, CI can publish the `unavailable` pages. Deleting an old
1.9.x release cannot launch the community website prematurely. Published beta
events refresh `/beta/` automatically through the same main-branch workflow.

After launch, a manual run with **retire_download=true** permits the same
unavailable output when no eligible release remains. Leave it unchecked for
ordinary builds and first-launch checks. It does not override invalid metadata
or bless an incomplete release. Reconcile a missed event or failed lookup with
a manual run and inspect its result.

One production concurrency group covers checkout, build and upload. With
`queue: max` and `cancel-in-progress: false`, GitHub keeps up to 100 pending runs
without replacing a queued withdrawal when an ordinary website update arrives.
Running uploads are not canceled. Pending runs enter their slots in FIFO order
by the time they started waiting, which can differ from event dispatch order.
Each checks out current `main` and fetches current releases after taking its slot.
An older queued event therefore cannot restore its old tag or old website code.
Additional runs are canceled when the 100-run queue is full. If a withdrawal is
lost at that limit, run manual reconciliation with `retire_download=true` after
the queue clears and verify the public page. PR groups remain separate and use
the same queue setting; they cannot consume the production queue.
See [GitHub's concurrency behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

The queue regression is a running normal deployment, then a pending withdrawal
of the last stable release, then an ordinary website push. GitHub's default
single-pending mode drops the withdrawal; the ordinary build returns a launch
hold and cannot remove the old page's links. Keeping both pending runs lets the
withdrawal publish an unavailable page before the ordinary launch hold. Local
regression validation models the documented queue behavior and checks this
workflow's actual configuration; it does not claim a live GitHub queue test.

CI tests the website tooling and creates separate ready/unavailable/beta-only sample
preview artifacts from the actual source. Those artifacts have disabled downloads
and `status: preview`; the Cloudflare upload never consumes them.

For a real eligible release, CI saves a production review artifact, checks
settings, then builds again into a new directory inside Wrangler's pre-command, after Wrangler installation and
immediately before upload. A failed final build aborts upload. When present,
the final publishing candidate is saved separately, including after an upload
failure. Release/tag/body data is parsed from the event JSON, never interpolated
into shell code.

A release can still change between the last GitHub response and Cloudflare
committing the upload. The services have no shared transaction. Follow-up
release events and manual reconciliation correct that window; updates cannot
be instantaneous during an upstream outage or missed event. The fifteen-minute
job timeout bounds a run, not outage duration. Avoid rolling Pages back to an
artifact containing withdrawn download links. Prefer a fresh validated build
or an explicit unavailable refresh.

The workflow uses the repository's current GitHub action majors (`checkout`,
`setup-python`, `upload-artifact` v7) and the official
[wrangler-action v4](https://github.com/cloudflare/wrangler-action). Logs and the
job summary identify successful uploads. A green launch/setup hold means the
public website was left alone.
