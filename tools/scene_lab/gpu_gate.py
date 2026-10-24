"""Refuse scene-lab GPU work while Griffin holds the GPU.

On 2026-10-24 a scene-lab measurement loop (the real renderer plus a GPU
browser, relaunched after every kill) froze his PC while he played. His words:
"pause anything heavy on the GPU for now ... I want to be able to play a game
AND use this system". The hold lives in ~/.swarm-engine/gpu-hold.txt and only
he lifts it. Every scene-lab entry point that starts a renderer calls
`require_gpu()` first, so no agent can start one by accident while it exists.
"""
from __future__ import annotations

from pathlib import Path

HOLD = Path.home() / ".swarm-engine" / "gpu-hold.txt"


def require_gpu() -> None:
    if HOLD.is_file():
        reason = HOLD.read_text(encoding="utf-8", errors="replace").strip()
        raise SystemExit(f"GPU held ({HOLD}): {reason}\nNo renderer runs until Griffin lifts the hold in his "
                         "own words; never delete the file on anyone else's say-so.")
