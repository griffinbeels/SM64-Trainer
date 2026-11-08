# Public-source credentials and release inputs

SM64 Trainer is open source. Git commits, GitHub logs, release assets and every
byte of a distributed desktop app must be treated as public. These rules apply
to every service integration, including EOS, Supabase, Cloudflare and Sentry.

## Decide where authority belongs

| Kind | Treatment |
| --- | --- |
| Public service URLs, product/sandbox/deployment IDs, OAuth client IDs, Supabase publishable keys, validated Sentry ingest DSN | May be published through the owning exact configuration schema. They do not authorize privileged operations. |
| EOS player-client credential required by the shipping SDK | Treat as recoverable from the app. Use an untrusted client policy requiring an authenticated user, only necessary service actions, and a deliberate development/production configuration. Do not mistake the portal's “Client Secret” label for confidentiality after distribution. |
| Provider admin/service credentials, Supabase privileged keys, Cloudflare management tokens, Sentry API tokens, private signing/encryption keys | Backend or developer secret storage only. Never put them in app source, UI, build metadata, helpers, installers or release assets. |
| Player access/refresh tokens, EOS identity material, browser sessions, room invitations/admission capabilities | Private runtime state. Use the existing secure credential store or provider storage; never repository fixtures, public build metadata, diagnostics or screenshots. |

A player client cannot hold an administrative secret safely. Obfuscation,
encrypted constants and reading a value from an environment variable during a
build do not make the resulting distributed value confidential. Perform
privileged operations on the backend with authorization checked there. Treat
requests from modified desktop clients as untrusted input.

## Local setup and provider configuration

The current community-project budget is **$0 ongoing service fees**. Use a
documented fee-free service or an actually free-only plan with hard stops. An
included allowance, budget alert, projected usage or client-reported traffic
does not prevent a metered invoice. Do not add a payment method, enable automatic
overages or change existing billing to implement a feature. Keep the provider
boundary changeable so adoption/funding can support a separately approved upgrade.
The explicitly authorized one-off API-credit review is a separate task expense.

Check this project's GPL-3.0-or-later license (GPL-2.0-or-later for the code
built into or loaded with the graphics plugin; `licenses/README.md` lists it)
against the actual selected SDK,
dependencies, interface and redistributable package. A proprietary SDK is not
automatically compatible or incompatible merely because it runs in another
process. New code can have its own documented license; existing third-party and
historical code cannot be silently relicensed. Required runtime designations,
source availability, notices and end-user terms must be reviewed before shipping.

- Keep private local integration configuration under the primary checkout's
  ignored `data/integrations/` directory, separate from `src/sm64_events/data`.
  The latter contains public, approved runtime assets and is a packaging input.
- Keep EOS SDK downloads and headers in ignored local build/setup storage.
  Publish only explicitly required redistributable runtime files and notices
  after checking the downloaded SDK's terms. Never copy its whole tree.
- Store backend deployment secrets in the service's secret store and CI
  credentials in GitHub Actions secrets. Use scoped tokens for each purpose.
  Never use a privileged provider credential as desktop configuration.
- Templates contain placeholders only. `.gitignore` helps prevent accidental
  staging; it does not protect an already tracked file or remove Git history.
- Never put credential values in shell arguments, copied commands, chat, agent
  notes, screenshots, source examples, tracebacks, test reports or telemetry.
  Query names/readiness only. Read needed values directly into their consumer
  without echoing the file or response. Use private input files or standard
  input where a deployment tool needs a secret.
- A portal login is for development access. It is not an application credential
  and must never be reused by the shipped app. Keep development and production
  client configuration distinct and independently revocable.

## Checks before source or assets leave the machine

Use the pinned Gitleaks adapter, with no live token in a test fixture:

```text
python tools/secret_scan.py --install
python tools/secret_scan.py --staged
python tools/secret_scan.py
```

The install action downloads an official, version-pinned archive and checks its
SHA-256 before extracting. Verification does not silently download a tool. The
staged check reads actual index changes, so cleaning the working copy cannot
hide a staged credential. The source check scans the selected Git source tree,
including new unignored files. Already tracked files are checked even if an
ignore rule now matches them. Ignored untracked runtime/setup data is not an
input. Detector absence or failure refuses the
check. Output is redacted; do not print raw findings or source lines.

GitHub secret scanning and push protection are enabled on the public upstream
repository as checked on 2026-10-20. Retain both. The mandatory security job in
the full run also gates the exact commit used for release. Run the same source
check for pull requests without providing deployment secrets to untrusted code.
Detection reduces mistakes; it does not establish the absence of all secrets or
replace least privilege and backend authorization.

Reviewed noncredential matches use exact rule/path/coordinates plus hashes of the
matched expression and complete source line in
`tools/verification/secret-scan-reviewed.json`. Changes to the match or context
require review; a new value elsewhere in the same file remains detectable.
Staged scans read the indexed review policy. The scan identity includes its hash.

First-party app data and UI assets have explicit controlled inputs. Unknown
local files must not silently enter a bundle. Validate the actual immutable
release archive as well as the staging folder. Generated public configuration
uses its owning schema. Keep third-party runtime files distinct from first-party
configuration; any exceptional packaged path needs narrow evidence and tests.
Source detection cannot prove that an arbitrary compiled binary contains no
secret, so controlled inputs and exact schemas remain necessary.

## If a real credential is exposed

Revoke or rotate it first through its provider. Check access/use, then remove
it from current source, logs and distributed assets and address Git history
deliberately. Deleting the latest file or rewriting history does not make an
already public credential safe. Report locations and credential categories
without repeating values. Do not rotate or rewrite history merely because a
scanner found a documented synthetic test value.
