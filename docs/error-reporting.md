# Automatic error reports

Packaged builds can send unexpected Python errors, browser errors, startup failures
and terminal Practice Replay installation errors to a private Sentry project.
Players do not need accounts or to submit reports. Settings -> Error reports turns
collection off and deletes pending uploads and breadcrumbs. Reporting is enabled by
default in configured builds; an unreadable preference fails closed. The first
error of each issue in a session also carries the same filtered Debug report a
player can export. The export remains available independently. A build without a
configured DSN sends nothing and says so.

## What a failure needs

Debugging capture requires the app and native build identities, current producer
and protocol state, the last successful stage, native error/refusal codes, queue
counts and stage timing. Ghost failures also need the requested overlay state,
native preparation/playback state, displayed flag and refusal counts. Setup needs
its actual ROM region and individual picture, plugin, game and input checks.
Input *contents*, account names and unrelated applications do not answer those
questions.

`core/diagnostic_snapshot.py` assembles these passive facts for both consumers:

| Evidence | Automatic Sentry error | Local Debug report |
| --- | --- | --- |
| Build, capture stage, native codes, first/latest GPU failure | Yes | Yes |
| Ghost enabled/target-present, native playback/display state, counts | Yes | Yes |
| Cached setup checks, ROM region, polling/input health counters | Yes | Yes |
| Own memory/handle/thread counts | Yes | Yes |
| Recognized native log events and numeric/enum fields | Latest 32 within a 24 KiB snapshot; absent log files only counted | Bounded recognized records from known log tails |
| The Debug report itself | Attached to the first error of each issue, at most 5 per session | Exported on request |
| Operational log lines (the report's server-log filter) | INFO as up to 100 breadcrumbs; WARNING and above as Sentry Logs | Last 400 lines, successful requests skipped |
| Local journal/UI observations | Only inside the attached report | Counts, clocks and states; display labels omitted |
| Raw native messages, account/profile/roster labels or arbitrary log fields | No | No |
| ROMs, video, inputs, databases, screenshots, environment, request bodies, locals | No | No file contents or attachments |

The native reader opens the existing control page read-only and reads only fixed
wrapper log filenames under the last observed installation, including Windows UAC
VirtualStore paths. It does not search drives, install plugins, enumerate programs,
attach game memory, enable capture or renew a lease. Missing, busy or failed
sources remain explicit. A native log row states whether PID, process birth and
build match; old-process history is not presented as current proof. Each source
has a read time, while retained observations keep their own times. These fields
are eventually consistent, not one atomic emulator snapshot.

Pre-channel failures retain the native request-acceptance/surface receipt,
receipt availability, callback progress/pacing and actual capture-stage timings.
Automatic GL evidence is restricted to numeric codes/dimensions, a major.minor
version, known missing entry-point names, and the context's GL vendor/renderer
strings (scrubbed, 140 characters) with the first bindings invalidation behind a
no-surface startup (10034): without them that failure named neither the GPU nor
the refusing check (beta.30's AMD tester). Driver version text, request nonces
and adapter LUIDs are excluded. Boolean fallback/pose-owner/pause
facts distinguish footage recovery from the independent ghost lifetime; owner
presence is not evidence of delivered poses or visible playback.

Errors automatically receive this snapshot before being queued. Unexpected native
ghost model/pose/playback faults also report when no Python exception occurs;
ordinary preparation, another scene and waiting for an attempt do not. A saved
PB that keeps no Mario poses reports an error with the pose archive's reason:
it can never become a ghost, and until 2026-10-25 nothing said so. There is
no background native/log sampler or automatic report-file generation.

The attached report comes from the same builder and redaction as the export.
The upload worker builds it after capture, never the failing thread: about 18 ms
and 82 KB on a 17,669-event journal with native logs (2026-10-24). An attachment
is cut at 256 KiB. Delivery holds that error until the report is attached or
refused. The report reads only the latest 100 journal rows. Identity is the
exception type and its last frame, or the message, so differing error text
shares one report. Sentry Logs pass the same logger allowlist and scrubber.
Their attributes keep logger, level, release, function, line and module path.
Hostname, PIDs, raw arguments and extras are dropped. Each log and error keeps
the SDK's random per-run trace id (32 hex, like a PID it names a run, not a
player): Sentry refuses a log without one, and it lists an error's logs by it.
Beta.31 dropped every log for lack of it (outcome `invalid`, `no_data`). Logs stay in memory, at
most 500 lines a day, and an identical line repeats at most once a minute.

Updater failures carry the running release plus the installed and target versions,
failed phase, attempt/count, planned download bytes and available OS/HTTP error
numbers. Versions and counters form a small `extra.update` group so the SDK's
dictionary-width limit cannot discard later fields before privacy projection.
The first failed retry reports immediately, even if the player closes the app
before retries finish. A terminal failure keeps its exception and safe updater
breadcrumbs. The retry report uses a distinct message so deduplication cannot
suppress the terminal exception. Normal reporting preferences and rate limits
apply; request URLs, headers and installation paths are excluded.

## Silent failures with evidence of expected work

`core/silent_failures.py` observes cached operational counters once a second on
one app-lifetime worker. It sends an error through the same SDK and bounded queue
when expected work makes no progress for at least 20 seconds of fresh, moving
game observations with recent player activity:

- Recording is enabled, not gated/paused/storage-limited, and the player is
  active, but the current media source delivers no new pictures.
- A validated saved-ghost command has an active attempt origin and is neither
  hidden, suspended nor zero-opacity, but native preparation/activation stops
  progressing or GP_READY playback produces no qualified display receipt.
- Setup was explicitly started and remains incomplete, the verified installation
  has a supported game running, and freshly polled game checks move while required
  plugin, picture or input checks remain missing.

Player activity comes from the existing ActivityTap's actual IGT/action/reset or
level-entry evidence. A static Usamune menu's advancing global timer does not
establish missing picture progress. This reporting qualification does not make
controller input a new requirement for normal setup completion.

Picture counts, native state/generation/preparation changes and setup-check
changes restart the grace period. Source/ROM changes, clock resets, stale or
missing observations, app sleep and reporting consent changes discard prior
waits. Completed or unmounted setup, a closed emulator, no ROM, unsupported
cartridges, manual pause, hidden ghosts, unavailable clips, absent attempt clocks,
another scene and finished ghosts are ordinary states, not failures. An intentional
idle/static desktop picture is not evidence of an encoding fault. Windows
permission prompts retain their existing bounded operation/error reporting;
merely leaving a prompt open does not create a new setup-stall report.

Healthy observations do not scan native mappings/log files, resolve recordings,
acquire capture or upload a telemetry stream. Only an actual admitted error gets
the passive technical snapshot above. Opaque local scope markers and counter
history stay in memory and never enter reports. The observer attempts at most one
report per category per hour; existing durable daily/monthly budgets still apply.
Turning Error reports off immediately retires observation timers. A temporary
consent marker is consumed before event projection so an in-flight or previously
timed failure cannot upload after opt-out/re-enable. A saved opt-out is never
overridden by the default-on setting, and an unreadable preference stays off.

These are operational invariants, not visual recognition. A short intermittent
no-show, failed work without enough current native evidence, or UI progress before a running
game cannot all be classified honestly. Existing browser/operation exceptions
still report, but this detector does not claim every invisible failure is
observable or that an absent receipt proves a particular GPU root cause.

## Privacy boundaries

`core/diagnostic_privacy.py` is the complete automatic snapshot allowlist. The SDK
callback, transport admission and restart/retry each rebuild the allowed payload.
Unknown nested fields and native freeform detail are discarded. Path, credential,
email and IP patterns are scrubbed from retained error text. Native nonces and
request tokens, player/account identities, machine names and persistent device
identifiers are excluded. Ephemeral producer PID/generation identifies a runtime,
not a player. No administrative Sentry credential is bundled.

Only named operational loggers, uvicorn's own included (`OPERATIONAL_LOGGERS`),
supply breadcrumbs, Sentry Logs and the report's server-log lines. One rule
(`operational_log`) drops successful request lines (1xx-3xx) from all three and
keeps failing ones (4xx/5xx), so the page's polling cannot fill the 400-line tail.
Gameplay event wires, unrelated
process rankings and community/account logs are excluded. Turning reporting off
also prevents breadcrumb collection and clears prior breadcrumbs, so re-enabling
cannot upload prior log history from the disabled period. It also drops queued
log lines and any report still being built; a log line written before the latest
re-enable is never sent. Automatic native/GPU
history is also bounded to the current reporting period. Normal performance records keep
aggregate machine load and Trainer/emulator resources; unrelated program names
are not retained in logs or `/health`.

Tracing, session replay, continuous profiling, SDK request integrations and
automatic session tracking remain disabled. A Sentry Trace page can therefore
show an error without a waterfall. The error's technical snapshot, timestamped
breadcrumbs and native phase timings provide the relevant capture history.
Enabling generic HTTP tracing would not supply native renderer state.

Sentry necessarily receives the network connection used to send an error. Client
PII filtering alone does **not** establish that Sentry stores no IP-derived
location: configure the project's **Security & Privacy** settings to prevent IP
storage and remove user/geo fields. Verify the stored event after that change.
The connector used for error inspection does not expose those settings. See
[Sentry data collection](https://docs.sentry.io/platforms/python/data-management/data-collected/)
and [server-side scrubbing](https://docs.sentry.io/security-legal-pii/scrubbing/advanced-datascrubbing/).
Cloud retention is controlled by the Sentry account and is separate from the
app's 24-hour local cleanup. Opt-out cannot retract an already accepted upload.

## Local storage limits

| Disposable artifact | Bound and cleanup |
| --- | --- |
| Exported reports | 24 hours, at most 3 files, at most 1 MiB each; cleanup at normal startup and export |
| Unsent automatic envelopes and abandoned upload temporaries | 24 hours, at most 20 / 2 MiB total; attached reports never fill the last 512 KiB; expiry during active sessions, shutdown and restart |
| Unsent Sentry Logs | Memory only, at most 10 batches; lost at exit |
| Python server log | 1 MiB plus one backup; stale/oversized legacy debug logs removed at startup |
| Performance log | 1 MiB plus one previous file; stale/oversized files removed at startup |
| UI observation log | Existing 2 MB bound; stale file removed at normal startup |
| Native wrapper logs | Existing approximately 12.5 MiB total bound; stale inactive owned files pruned by the native writer at startup and once a minute |

Native file-age cleanup does not promise every line is younger than a day: an
actively written rotating file can contain older lines until rotation. The byte
bound still applies. Cleanup checks exact owned filenames, refuses link escapes
and contains denied filesystem operations. An export refuses redirected folders
or a new file when its quota cannot be reclaimed. It never deletes recordings, practice
databases, account settings or arbitrary user files. No cleanup runs on renderer
callbacks. A provider outage does not block practice or create unbounded files.
Cooldown and admission budgets remain one matching failure per hour, ten errors
per day and fifty per month per installation.

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

GPU failures include bounded scalar evidence: startup stage, selected encoder,
actual adapter/driver when established, native capture build, Open result and
known operation/HRESULT. OpenGL vendor/renderer/version and a missing function
are sampled once on the existing renderer startup thread. The renderer also
reports the same three strings from its context creation, and a no-surface
startup failure carries the bindings cause: numeric site, GL error, flags,
counts and one plain sentence naming it. A renderer witness rebuild logs a
numeric `bindings_reconstructed` event, and a failure line counts the rebuilds.
These strings pass
the same privacy scrubber. Raw helper stderr and native log text are never uploaded.
Expected AV1 codec refusal followed by H.264 negotiation is not a failure report.
The local Debug report separately includes the current recording route and its
picture/input matching method, first failure retained through cleanup/fallback,
latest attempt, and last observed setup status. Export reads cached facts; it
does not enable capture or retry installation. Restarting Trainer clears this
in-memory history; automatic reports already queued remain governed by retention.

## Practice Replay permission handling

Install first tries the normal user's permissions. A qualifying denied plugin
write automatically starts the Windows UAC prompt from that same Install gesture.
The setup UI stays on Installing until the retry finishes. A narrowly scoped helper
handles plugin files, while the original process keeps its own per-user registry
and settings. A user cancellation or final failed retry produces its actual error.
File use, unsupported targets and unavailable payloads retain distinct explanations.
Background plugin refresh never prompts for administrator permission.
A denied background refresh exposes the existing Install action and stops
repeating the same denied writes for that bundle and target. Explicit successful
installation clears the retained denial; a changed bundle can retry normally.

Files roll back until the helper commits. If its final acknowledgment is lost
after commit, the complete verified DLL pair may remain while the original user's
settings and graphics selection restore. Setup does not claim success or select
the wrapper in that ambiguous case; retrying installation is safe.

## Verification limits

Synthetic transport tests establish capture, scrubbing, queue/retry and opt-out.
`tests/test_error_report_evidence.py` covers the attachment, Sentry Logs and
breadcrumbs the same way. Neither has been observed in the real project yet.
Confirm on the first real event that the project keeps the attachment (plan
quota and attachment settings) and that its Logs view shows the warning lines.
Offline browser tests establish Settings and the continuous Installing state.
Installer tests use scratch files and controlled native boundaries. The opt-in
`tests/test_sentry_live_reporting.py` exercises normal startup and the real failed
installation API with isolated fixture data. On 2026-10-19 its error was observed
in Sentry with readable Python filenames, functions and line numbers; event identity
was verified through ingestion and is retained across queue/retry. Normal test runs
skip this live check unless `SM64_SENTRY_LIVE_VERIFY=1` is explicitly set.
On 2026-10-21 the expanded synthetic snapshot was observed in stored event
`21b172b875884e7fbf8e0b03b9b1fb45`, including capture, ghost, setup and input
health fields without the injected player-label sentinel. The stored event also
confirmed that Sentry still enriches location from the connection; cloud-side
scrubbing needs settings verification before claiming that metadata is absent.

The organization settings saved on 2026-10-21 require the data scrubber/default
scrubbers and prevent IP storage. A fresh synthetic event
`b6b801af8009443599eebdc11fd9d7b8` still contained `user.geo`, as Sentry's
[documented geographic processing](https://docs.sentry.io/security-legal-pii/scrubbing/server-side-scrubbing/#geographic-information)
predicts. Removing location additionally requires the Advanced Data Scrubbing
rule **Remove / Anything / `$user.geo.**`**; verify a new event afterward.
Keep Global Safe Fields empty. Optional narrow Global Sensitive Fields are
`username`, `user_id`, `account_id`, `email`, `ip_address`, `hostname`,
`computer_name` and `command_line`. Avoid broad names such as `name`, `id`,
`user`, `state` or `reason`: sensitive-field terms also match field values and
can hide useful technical facts. Griffin is managing the cloud settings.
Packaged field delivery still requires verification against the distributed build.
A real UAC prompt and affected-tester installation must be verified on an authorized
interactive run; injected helper success is not that evidence.
