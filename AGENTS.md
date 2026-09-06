# SM64 Trainer — agent entrypoint

Read [`CLAUDE.md`](CLAUDE.md) now. It is the shared project guide for Codex and
Claude Code: commands, live-recording protection, domain contracts and verification.

Before editing, read [`docs/rule-index.md`](docs/rule-index.md) and open every
rule matching the touched files. Codex uses that map to load project rules
explicitly; Claude Code also loads matching `paths:` rules automatically.
The index is generated from the rules' metadata, so there is one route map.

This file holds reader routing only. Add project facts to the shared guide or
the relevant rule, never a second copy here. Hook configuration and shared-skill
ownership are described in the guide and checked by the parity tests.
