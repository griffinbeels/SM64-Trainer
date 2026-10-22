# Capture startup header and lifecycle

Maintenance authorized by human event179d4cce7f4cfc15c45012200997a4c5a90e9e5e5b6861a5a6e6c8238770b841.
Owned branch codex/capture-header-startup, base main2b147986. Separate from private M3.

| Node | Done when | Depends on | Owns | Check |
| --- | --- | --- | --- | --- |
| Reproduce | Actual x86 producer pauses before header publication; current client fails | — | Native fixture/IPC tests | Red test on published source |
| Fix | Pending headers wait boundedly; malformed published headers stay rejected | Reproduce | replay/gpuchannel.py | Actual x86/x64 channel witness |
| Lifecycle | Producer exit, ROM close/switch retire waiting opens; next request succeeds | Fix | Capture/lease tests, runtime docs | Focused lifecycle and cleanup regressions |
| Deliver | Integrated source and corrected public beta pass release gates | Lifecycle | Release notes/private build records | Local gate, GitHub full run, exact-package QA |

Never wait on the renderer thread, weaken identity/version/layout checks, edit an
installed plugin, touch a real recorder or include private M3/EOS in the package.
The actual raw failed historical header is unavailable; controlled reproduction
must demonstrate a real reachable mechanism without claiming historical bytes.
