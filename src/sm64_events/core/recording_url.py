"""Public recording addresses: preserve the link, share only the media identity."""
import ipaddress
import re
from urllib.parse import parse_qs, urlsplit


def validate_recording_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 4096 or any(ord(char) < 33 for char in value):
        raise ValueError("Paste a complete public video link.")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        port = parts.port
    except ValueError as error:
        raise ValueError("Paste a complete public video link.") from error
    if (parts.scheme not in ("http", "https") or not hostname
            or parts.username is not None or parts.password is not None
            or "\\" in value or port not in (None, 80, 443)):
        raise ValueError("Use a public http or https video link.")
    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Use a public video link, rather than a local address.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or not re.fullmatch(r"[a-z0-9.-]+", hostname):
            raise ValueError("Use a public video website address.") from None
    else:
        if not address.is_global:
            raise ValueError("Use a public video link, rather than a local address.")
    return value


def youtube_id(url: str) -> str | None:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    video = None
    if hostname in ("youtu.be", "www.youtu.be"):
        video = parts.path.lstrip("/").split("/")[0]
    elif hostname in ("youtube.com", "www.youtube.com", "m.youtube.com",
                       "youtube-nocookie.com", "www.youtube-nocookie.com"):
        if parts.path == "/watch":
            video = parse_qs(parts.query).get("v", [None])[0]
        elif parts.path.startswith(("/shorts/", "/embed/", "/live/")):
            video = parts.path.split("/")[2]
    return video if video and re.fullmatch(r"[\w-]{11}", video) else None


def media_identity(url: str) -> str:
    video = youtube_id(url)
    return f"https://www.youtube.com/watch?v={video}" if video else url


def start_seconds(url: str) -> float:
    parts = urlsplit(url)
    query = {**parse_qs(parts.fragment), **parse_qs(parts.query)}
    value = query.get("t", query.get("start", ["0"]))[0]
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return min(float(value), 7 * 86400)
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
    if match:
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        return min(hours * 3600 + minutes * 60 + seconds, 7 * 86400)
    return 0
