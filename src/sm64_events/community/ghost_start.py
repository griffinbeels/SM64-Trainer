"""A ghost's start on the community wire, and the seam to the module that owns it.

A ghost's start is the game state the ghost begins from (glossary: "Ghost's
start"). With it, the ghost's recorded pads re-simulate its whole run exactly.
The community client only carries it. On upload it asks the owner for the
start of the ghost it is sharing and sends those bytes after the ghost's own.
On download it keeps the copy beside the cached ghost and hands it to the
owner. It never reads inside the bytes.

A downloaded start came from another player's trainer through the service,
which only checks its size and hash. It is untrusted data: the owner must
validate it before any use. The size bound below only limits what this client
holds in memory.

STUB: the owner's two functions are not frozen yet (the handoff-boundary task
builds them). Until then a ghost has no start to send, and a downloaded start is
only kept in the cache. Replace the two bodies with calls to the owner.
"""

MAX_BYTES = 256 * 1024
"""The largest start this trainer sends or downloads. The service advertises its
own cap in `/v1/config` (64 KiB at first); the smaller of the two applies."""


def start_for_upload(path, raw_sha256):
    """The start of the local ghost clip at `path` whose bytes hash to
    `raw_sha256`, as bytes, or None when it has none."""
    return None


def accept_download(recording_id, raw_sha256, blob):
    """Hand the start downloaded with community recording `recording_id` (whose
    ghost bytes hash to `raw_sha256`) to its owner. `blob` is untrusted."""
    return None
