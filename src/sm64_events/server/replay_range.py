"""HTTP byte ranges over a leased, immutable virtual native MP4."""
import re

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import Response, StreamingResponse


def byte_range(value, size):
    # RFC byte ranges are inclusive on the wire. Unsupported/multiple ranges
    # may be ignored with a complete 200; never invent a multipart boundary.
    if not value or not value.startswith("bytes=") or "," in value:
        return 0, size, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value[:1024]) if len(value) <= 1024 else None
    if match is None or not any(match.groups()):
        raise ValueError("invalid range")
    left, right = match.groups()
    if not left:
        count = int(right)
        if not count:
            raise ValueError("empty range")
        return max(0, size-count), size, 206
    start, end = int(left), min(size, int(right)+1) if right else size
    if start >= size or end <= start:
        raise ValueError("range outside media")
    return start, end, 206


async def serve_media(media, scope, receive, send):
    request = Headers(scope=scope)
    headers = {"Accept-Ranges": "bytes", "Content-Type": "video/mp4", "Cache-Control": "no-store"}
    # A fragment descriptor names immutable bytes, so it is a real entity
    # validator: the browser may keep fetched ranges and resume with If-Range
    # instead of opening a fresh connection for every seek (round 48).
    identity = getattr(media, "identity", None)
    if identity:
        etag = f'"{identity}"'
        headers.update({"ETag": etag, "Cache-Control": "private, max-age=3600"})
    else:
        etag = None
    conditional = request.get("if-range")
    try:
        # Without a validator, or with a stale one, If-Range means: send the
        # complete entity. With the current one, honour the Range as usual.
        ignore_range = bool(conditional) and (etag is None or conditional.strip() != etag)
        start, end, status = byte_range(None if ignore_range else request.get("range"), media.size)
    except ValueError:
        await Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{media.size}"})(scope, receive, send)
        return
    headers["Content-Length"] = str(end-start)
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end-1}/{media.size}"
    if scope["method"] == "HEAD":
        await Response(status_code=status, headers=headers)(scope, receive, send)
        return
    iterator = media.chunks(start, end)
    try:
        await StreamingResponse(iterator, status_code=status, headers=headers)(scope, receive, send)
    finally:
        # Windows cannot unlink an open file. Cancellation must close the byte
        # iterator before ReplayClipResponse releases the archive's group leases.
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(iterator.close)
