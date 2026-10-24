"""Only mutable asset IO failures ask the roster to retry unchanged ghosts."""
from types import SimpleNamespace as NS

import pytest

from sm64_events.ghost.native_session import NativeGhost


@pytest.mark.parametrize("error, retryable", [
    (PermissionError("temporary cache access"), True),
    (FileNotFoundError("asset not present yet"), True),
    (ValueError("invalid immutable clip"), False),
    (LookupError("incompatible target"), False),
])
def test_portable_failure_classification_preserves_permanent_refusals(error, retryable):
    native = NativeGhost(None)
    target = {"kind": "star", "entity_key": "2-2"}
    ghost = NS(**target)

    def failed(*_args):
        raise error

    native.set_portable(NS(resolve=failed))
    answer, asset = native._resolve_portable(target, ghost, {"available": False, "reason": "no_native_poses"})
    assert asset is None and answer["available"] is False
    assert answer.get("retryable", False) is retryable


def test_partial_transient_failure_is_retained_beside_a_playable_ghost(tmp_path, monkeypatch):
    native = NativeGhost(None)
    ghosts = [NS(id=n, origin=f"attempt:{n}", frames=30, pb=False) for n in (1, 2)]

    def resolve(_target, ghost):
        result = {"available": ghost.id == 1, "reason": None, "reason_text": None,
                  "template_id": ghost.id, "attempt_id": ghost.id}
        if ghost.id == 2:
            return {**result, "retryable": True}, None
        return result, (tmp_path / "clip.ghost", {"clip_id": "aa" * 16})

    monkeypatch.setattr(native, "_resolve_template", resolve)
    answer = native.resolve({"kind": "star", "entity_key": "2-2"}, ghosts)
    assert answer["available"] is True and answer["retryable"] is True
    assert len(native._asset) == 1 and len(answer["ghosts"]) == 2
