---
paths:
  - "src/sm64_events/ghost/**"
  - "src/sm64_events/server/ghost_api.py"
  - "src/sm64_events/ui/ghostsession.js"
  - "src/sm64_events/ui/components/ghostswitch.js"
  - "plugin/gfxwrap/ghost_*"
---

# Ghost — where to change what

Runtime contracts, format versions and witnesses: [ghost-native-runtime](../../docs/ghost-native-runtime.md).

| To change... | Edit |
|---|---|
| A ghost setting (default, range, sentence, db key, native field) | One row in `ghost/settings.py` `ROWS`. It drives validation, load/apply, the payload, `limits` and the native command. |
| A range or limit the card shows | The row's `limits`. `GET /api/ghost` publishes them and the card reads `session.limits` (`ui/ghostsession.js`). JS never restates a Python number. |
| The words of an on/off switch | `SWITCH_LABELS` in `ui/ghostsession.js`, keyed by the row name |
| Which ghosts play | `ghost/selection.py`; the native set is built in `ghost/native_session.py` |
| What the renderer draws, and how it fades | `plugin/gfxwrap/ghost_*`. The fade rule and its constants are in `ghost_occlusion.h`; see `native-plugin.md` |

**A new on/off setting touches four files:**
1. A `switch(name, db_key, default, sentence, native_field)` row in `ghost/settings.py`.
2. A `SWITCH_LABELS` row in `ui/ghostsession.js`.
3. When the renderer reads it: the field in `ghost/native_protocol.py`, which the channel encodes from `native_switches()`.
4. The native side itself.

`tests/test_ghost_settings.py` covers the row with no edit: refused, applied, remembered, carried to the native command, and read by the card. The fixture and the API docs follow the registry. A setting of another kind also needs its sample pair in that test's `SAMPLES`, plus its control in `ui/components/ghostswitch.js`.
