# Automatic error reports

Packaged builds can send unexpected Python errors, browser errors, startup failures
and terminal Practice Replay installation errors to a private Sentry project.
Players do not need accounts or to submit reports. Settings -> Error reports turns
collection off and deletes pending uploads. The existing Debug report export remains
available for support. A build without a configured DSN sends nothing and says so.

## Connect the free service

Create a Sentry Developer account and Python project. Copy its **public DSN** from
the project's Client Keys page. No administrative API token belongs in the app.
The checked-in public client destination in `src/sm64_events/data/error-reporting.json`
is the default for builds. `SM64_SENTRY_DSN` can override it in the build environment;
an explicit empty value disables reporting for that build. The builder validates the
destination and embeds `error-reporting.json` in the bundle. Source runs read that
environment variable explicitly; packaged
apps ignore environment overrides and use their own bundle. Both normal and legacy
beta builders stage the configuration. Configure email alerts in Sentry; the free
plan's current allowance is 5,000 error events per month and one dashboard user.
Player count is not the dashboard-seat count. See [current pricing](https://sentry.io/pricing/).

The report includes the exact runtime release, build identifier and channel. It
does not serialize community account settings or build metadata wholesale.
Diagnostic envelopes are scrubbed before persistence and upload. Screenshots,
ROMs, recordings, raw databases, credentials, request bodies and frame locals are
excluded. The collector bounds disk use, repeated errors and retries; a provider
outage does not block practice. Turning reporting off stops capture and clears
queued reports. Already uploaded reports remain subject to Sentry's retention.

## Practice Replay permission handling

Install first tries the normal user's permissions. A qualifying denied plugin
write automatically starts the Windows UAC prompt from that same Install gesture.
The setup UI stays on Installing until the retry finishes. A narrowly scoped helper
handles plugin files, while the original process keeps its own per-user registry
and settings. A user cancellation or final failed retry produces its actual error.
File use, unsupported targets and unavailable payloads retain distinct explanations.
Background plugin refresh never prompts for administrator permission.

Files roll back until the helper commits. If its final acknowledgment is lost
after commit, the complete verified DLL pair may remain while the original user's
settings and graphics selection restore. Setup does not claim success or select
the wrapper in that ambiguous case; retrying installation is safe.

## Verification limits

Synthetic transport tests establish capture, scrubbing, queue/retry and opt-out.
Offline browser tests establish Settings and the continuous Installing state.
Installer tests use scratch files and controlled native boundaries. The opt-in
`tests/test_sentry_live_reporting.py` exercises normal startup and the real failed
installation API with isolated fixture data. On 2026-10-19 its error was observed
in Sentry with readable Python filenames, functions and line numbers; event identity
was verified through ingestion and is retained across queue/retry. Normal test runs
skip this live check unless `SM64_SENTRY_LIVE_VERIFY=1` is explicitly set.
Packaged field delivery still requires verification against the distributed build.
A real UAC prompt and affected-tester installation must be verified on an authorized
interactive run; injected helper success is not that evidence.
