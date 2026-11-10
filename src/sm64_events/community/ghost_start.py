"""A ghost's start on the community wire, and the seam to the module that owns it.

A ghost's start is the game state the ghost begins from (glossary: "Ghost's
start"). With it, the ghost's recorded pads re-simulate its whole run exactly.
The community client only carries it. On upload it asks the owner for the
start of the ghost it is sharing and sends those bytes after the ghost's own.
On download it keeps the copy beside the cached ghost and hands it to the
owner. It never reads inside the bytes.

A downloaded start came from another player's trainer through the service,
which only checks its size and hash. It is untrusted data: its owner
(ghost/start_frame.py) bounds it, checks its shape, binds it to this ghost's
first record and keeps it apart from his own starts; the scene lab checks it
against his ROM before anything runs from it. The size bound below only limits
what this client holds in memory. A start that is refused never costs the
ghost: the caller logs it and the ghost plays without one.
"""

MAX_BYTES = 256 * 1024
"""The largest start this trainer sends or downloads. The service advertises its
own cap in `/v1/config` (64 KiB at first); the smaller of the two applies."""


def _root():
    from sm64_events.core.paths import ghost_population_root
    return ghost_population_root()


def start_for_upload(path, raw_sha256):
    """The start of the local ghost clip at `path` whose bytes hash to
    `raw_sha256`, as bytes, or None when it has none (start_frame.start_blob:
    only his own start, without main-pool changes)."""
    from sm64_events.ghost.start_frame import start_blob
    return start_blob(_root(), raw_sha256)


def accept_download(recording_id, raw_sha256, blob, raw=None):
    """Hand the start downloaded with community recording `recording_id` (whose
    ghost bytes `raw` hash to `raw_sha256`) to its owner. `blob` is untrusted:
    start_frame.accept_start_blob checks it and binds it to the ghost, raising
    StartError with a plain sentence when it refuses. Without the ghost's bytes
    there is nothing to bind it to, so it is not kept."""
    import hashlib

    from sm64_events.ghost.start_frame import StartError, accept_start_blob
    if raw is None:
        return None
    if hashlib.sha256(raw).hexdigest() != raw_sha256:
        raise StartError("This start came with other ghost bytes than the ones it names.")
    return accept_start_blob(_root(), raw, blob)
