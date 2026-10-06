"""Input documents and saved native poses for ghost-session tests."""
from pathlib import Path
from types import SimpleNamespace

from sm64_events.inputs.document import encode
from sm64_events.inputs.frame import InputFrame


def a_document(target: str = "star 24 1") -> str:
    return encode([(0, InputFrame(0x8000, 0, 40, 40))], target=target,
                  strategy="10 coin", version="us", origin="attempt 1")


class FakeReplay:
    """Saved native assets; calls are recorded without opening a renderer."""
    def __init__(self, saved=(7,), saved_dir=None):
        self._saved = set(saved)
        self.probed = []
        self.saved_dir = Path(saved_dir) if saved_dir else Path()
        self.recorder = SimpleNamespace(ghost_capture=None)

    def native_identity(self, attempt_id):
        return attempt_id

    def find_native(self, attempt_id):
        self.probed.append(attempt_id)
        if attempt_id not in self._saved:
            return None
        return self.saved_dir / f"attempt_{attempt_id}.ghost", {
            "clip_id": f"{attempt_id:032x}", "sha256": f"{attempt_id:064x}",
            "blink_frames": 0}
