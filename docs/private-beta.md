# Private Windows beta builds

An offline beta may leave all four public community configuration fields empty.
It retains a separate beta profile and disables public updates. Mixed empty and
configured fields are refused. An offline artifact proves packaging and local
practice only; community testing needs the configured service and provider checks
below. Do not package placeholder service addresses.

A private beta is one complete installer EXE distributed directly to testers.
It contains the application, native plugins, FFmpeg and reviewed author ghosts.
It installs once without downloading application files, creates a separate beta
shortcut and opens the shared setup/account flow. It does not create a GitHub
release or use the public app's updater/bootstrap.
The [community protocol](community-protocol.md) owns its account and sharing
contracts. A successful local build does not establish deployed login or sharing.
Follow [the staging account guide](../cloud/community/SETUP.md) to configure
Cloudflare, Supabase, Discord, Google and Twitch before creating a cloud-enabled installer.

## Build identity and data

The fixed packaged `build-channel.json`, validated by `core/build_channel.py`,
is the channel authority. Missing metadata preserves the stable application;
present malformed metadata refuses startup. A beta-named EXE also refuses missing
beta metadata, preventing a damaged beta bundle from opening the public profile.
Environment variables cannot select
a beta channel or replace its public service configuration.

| Property | Stable | Private beta |
| --- | --- | --- |
| EXE/folder | SM64Trainer | SM64TrainerBeta |
| Window/tray | SM64 Trainer | SM64 Trainer Beta |
| Frozen profile | LocalAppData/SM64Trainer | LocalAppData/SM64TrainerBeta |
| Frozen server port | 8064, existing override retained | 8072, exact OAuth callback port |
| Public app updates | Existing behavior | Disabled before checks, fake overrides, apply and maintenance |
| Legacy profile migration | Existing stable migration | Disabled |

Beta ignores inherited `SM64_PORT`, `SM64_PRACTICE_ROOT` and
`SM64_GHOST_POPULATION` overrides. Its database, settings, logs, PID, window
geometry, replays and population stay in the beta profile. It does not import
public history automatically. Source builds retain their existing isolated
profile overrides; tests inject an explicit `BuildChannel` instead of selecting
a channel through environment state.

Project64 installation metadata remains at the existing shared Windows-user
location. The machine-wide recorder lock also remains shared. Close the public
trainer before beta recording. Beta plugin installation is explicit and changes
that shared emulator installation; it can affect the public trainer's next
emulator launch. A beta takeover only requests graceful shutdown from a server
whose health response identifies it as beta; it never force-terminates a listener
or stale PID owner.

## Maintainer build

Use the existing project environment with its runtime dependencies and
PyInstaller installed. Prepare the three native DLLs from the exact checkout
first using the existing native builder. `build_beta.py` checks their embedded
source identities without compiling, copying or deleting stale outputs. Missing
FFmpeg, stale natives, a stale defaults seed, missing dependencies or required
bundle assets refuse packaging clearly.

Prepare an explicit JSON file containing only these fields; replace the example
values with the verified staging configuration before a real build:

```json
{
  "schema": 1,
  "channel": "beta",
  "build_id": "friends-20261008-commitid",
  "community_url": "https://community-beta.example.com",
  "supabase_url": "https://beta-project.supabase.co",
  "supabase_publishable_key": "sb_publishable_REPLACE_WITH_PUBLIC_KEY",
  "oauth_client_id": "11111111-2222-4333-8444-555555555555"
}
```

Only HTTPS origins without user information, query strings or fragments are
accepted. The Supabase value must be a publishable key; secret keys and arbitrary
extra fields are rejected. Use a registered public beta OAuth client with exact
callback `http://127.0.0.1:8072/api/community/auth/callback`. No service credentials,
account tokens, personal data or developer environment files belong in this JSON.

An optional `friend_invitation` field holds a 43-character base64url beta join
capability. The service controls whether it is reusable or single-use and whether
it expires. The current tester build uses one reusable capability, valid until
revoked, and explicit uncapped membership. Send the identical EXE to any number
of people; each authenticates independently and receives a separate account.
No invitation step or separate build is needed per tester. Request and storage
quotas remain enforced. The capability is not a Cloudflare or Supabase operator
credential; keep the build private and omit its value from diagnostics/receipts.
Legacy single-use capabilities remain available for narrower operator workflows.

```powershell
python tools/build_beta.py --config C:/private/public-beta-config.json --check
python tools/build_beta.py --config C:/private/public-beta-config.json --ffmpeg D:/ffmpeg/bin/ffmpeg.exe
```

To include Griffin's reviewed portable recordings, add
`--ghosts C:/private/griffman1212.sm64ghosts`. The builder validates all clip
hashes, metadata and targets, requires successful non-practice recordings
credited to `griffman1212`, and pins the approved file's SHA-256. Only that exact
root-level collection is exempt from the private-recording guard. Additional
collections, altered bytes, raw databases and recording folders remain refused.
Human provenance comes from the maintainer's source audit; structural validation
cannot certify how a recording was played.

The builder writes an immutable SHA-256/count/author descriptor for this exact
collection. A frozen beta reads its bounded metadata as an additional local
population, so these ghosts become available automatically. Only selected clips
are extracted/decoded into a separate 64 MiB cache; the app does not bulk import
all recordings on first launch or write practice/PB entries. It never publishes
historical author recordings as the tester's own runs. The local provider keeps
`source_status=bundled-author-recording`, `certified=false`, `full_start=null`
and the original `recorded_game_version` (including unknown). A current-ROM
query supplies playback compatibility context, not a new recording certificate.
Known mismatching ROMs stay excluded. Structural packaging checks cannot prove
honest gameplay. Controller frames without captured inputs remain unknown.

The first command is a read-only prerequisite check. The second builds the
windowed directory application using the same assets/dependency collection as
the stable builder. Outputs are isolated under `dist/private-beta/`; PyInstaller
work/config/spec files stay under `build/private-beta/`. The release version is
unchanged. The public build identifier distinguishes private test artifacts.

Successful packaging produces `SM64TrainerBeta-<build_id>.exe` and its `.sha256`
receipt, plus a portable ZIP and checksum as an alternative. A small PyInstaller
stub carries the verified app ZIP as an appended payload. The application is
extracted into `%LOCALAPPDATA%/Programs/SM64TrainerBeta` once; normal launches
use the installed app, so the full runtime is not unpacked every time.
The installer streams SHA-256 verification before changing program files,
rejects unsafe Windows ZIP paths/aliases/links, bounds files and expanded size,
and stages a sibling directory before the atomic swap. A kernel lock serializes
installers; a journal restores the previous installation after interruption.
Rerunning the same installer reuses a complete existing installation. The app's
separate practice profile survives repair and replacement.

The artifact gate checks required UI/seed/native/FFmpeg/config files,
rejects private data, hidden files and public executables, tests ZIP integrity
and hashes the complete archive. It writes `README-BETA.txt` into the folder.
Neither the public bootstrap nor release publishing is called.
The hidden-file check permits only two exact-path, SHA-256-pinned NumPy fixtures
included by dependency collection. Modified bytes or the same name elsewhere
remain rejected; all private-data and path-containment checks still apply.

## Tester handoff and acceptance

Send the installer EXE and checksum receipt through the private distribution
path. The tester double-clicks that one file, signs in, and follows the existing
Project64/Usamune setup. The beta shortcut opens subsequent sessions. The trainer
includes its renderer/wrapper/encoder DLLs and installs the renderer and wrapper
through the existing explicit setup action, while Project64 is closed. The tester
supplies their supported Project64 and practice ROM; those files are not bundled.
Actual ROM and moving game/input/picture evidence still gates completion.

If Windows lacks WebView2 or a usable .NET engine, the beta opens the same local
UI in the default modern browser automatically. Its tray owns Show and Quit;
closing a browser tab does not quit the recorder. No runtime download blocks
offline practice. Stable desktop behavior remains unchanged. Microsoft's
[WebView2 distribution guide](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
describes the per-user/per-machine registry detection used by this fallback.

Portable users may still extract the whole ZIP into a writable folder and keep
the EXE and `_internal` folder together. Running the beta does not install the public app.
Practice remains available offline; community access requires an invited staging
account and the applicable consent. New accounts disclose automatic sharing
before consent and enable it on completion; existing choices survive renewal.
Sheet imports begin unchecked. Settings allows sharing to be disabled and
explains that existing published ghosts remain until explicitly removed.
Account names allow at most20 normalized Unicode characters and63 UTF-8 bytes.

To replace a beta, close it and run the next private installer. Portable users
replace the entire extracted folder instead. The separate LocalAppData profile
survives. Never replace individual runtime files while the app is running.

Before handoff, run the packaged executable's maintainer probe against a fresh
absolute report path outside any application profile:

```powershell
./SM64TrainerBeta.exe --beta-self-check C:/private/receipts/beta-check.json
```

The report's parent directory must exist. This beta-only entry point exits before
opening the desktop, server, recorder or helper processes. It checks the packaged
build identity, profile/port isolation, disabled updates, required assets, native
PE headers and community imports. It emits no credentials and does not create a
practice profile. Source and stable execution refuse this probe.

This receipt does not prove desktop interaction, native playback or provider
login. Test visible beta identity/build, normal/helper entry points and installed
recording dependencies separately with a disposable beta profile. Verify the real
registered desktop login return and staging exchange independently.
An agent's Python test launcher can replace `sys.executable` through
`__PYVENV_LAUNCHER__`. Remove that launcher override in the owned probe parent
before launching the frozen EXE; otherwise its identity check can fail even
with valid beta metadata. Do not inspect or print environment values.
Do not use the maintainer's running trainer, Project64 or live recording for
agent verification. Unit tests and disposable ZIP fixtures do not substitute for
these frozen-artifact and deployed-service receipts.
