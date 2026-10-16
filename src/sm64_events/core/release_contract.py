"""Stdlib-only release precedence and eligibility shared by app and installer.

Publication dates never establish product lineage. Invalid installed versions
fail closed. Build metadata does not change SemVer precedence.
"""
from dataclasses import dataclass
from functools import total_ordering
import re
from urllib.parse import urlsplit

from sm64_events.core.update_plan import MANIFEST_ASSET, ZIP_ASSET

_NUMBER = r"(?:0|[1-9][0-9]*)"
_VERSION = re.compile(
    rf"[vV]?({_NUMBER})\.({_NUMBER})\.({_NUMBER})"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
REQUIRED_ASSETS = (ZIP_ASSET, ZIP_ASSET + ".sha256",
                   MANIFEST_ASSET, MANIFEST_ASSET + ".sha256")
WITHDRAWN_MARKER = "<!-- sm64-withdrawn -->"


@total_ordering
@dataclass(frozen=True, eq=False)
class ReleaseVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @property
    def core(self) -> tuple[int, int, int]:
        return self.major, self.minor, self.patch

    def __eq__(self, other):
        if not isinstance(other, ReleaseVersion):
            return NotImplemented
        return (self.core, self.prerelease) == (other.core, other.prerelease)

    def __lt__(self, other):
        if not isinstance(other, ReleaseVersion):
            return NotImplemented
        if self.core != other.core:
            return self.core < other.core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def __hash__(self):
        return hash((self.core, self.prerelease))


def version_key(value: str) -> ReleaseVersion:
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("invalid release version")
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError("release version must be SemVer")
    pre = tuple(match[4].split(".")) if match[4] else ()
    if any(part.isdigit() and len(part) > 1 and part[0] == "0" for part in pre):
        raise ValueError("numeric prerelease identifiers cannot have leading zeros")
    return ReleaseVersion(*(int(match[n]) for n in (1, 2, 3)), pre)


def is_forward(candidate: str, installed: str, *, allow_same: bool = False) -> bool:
    proposed, current = version_key(candidate), version_key(installed)
    return proposed > current or (allow_same and proposed == current)


def release_assets(release: dict) -> dict[str, str]:
    """Offer only complete releases with unambiguous HTTPS asset URLs."""
    assets = release.get("assets")
    if not isinstance(assets, list):
        return {}
    found = {}
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") not in REQUIRED_ASSETS:
            continue
        name, url = asset["name"], asset.get("browser_download_url")
        if name in found or not isinstance(url, str) or any(ord(ch) < 33 for ch in url):
            return {}
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                return {}
        except ValueError:
            return {}
        found[name] = url
    return found if all(name in found for name in REQUIRED_ASSETS) else {}


def release_version(release: dict) -> ReleaseVersion | None:
    if not isinstance(release, dict):
        return None
    if release.get("draft", False) is not False or release.get("withdrawn", False) is not False:
        return None
    body = release.get("body") or ""
    if not isinstance(body, str) or WITHDRAWN_MARKER in body:
        return None
    try:
        version = version_key(release.get("tag_name"))
    except ValueError:
        return None
    if release.get("prerelease", False) is not bool(version.prerelease):
        return None
    return version if release_assets(release) else None


def select_release(releases, installed: str, *, include_beta: bool = False,
                   allow_same: bool = False) -> dict | None:
    """Choose the furthest-forward complete eligible candidate.

    Same-version eligibility is reserved for explicit repair. Opting out while
    ahead of production never moves the installed floor back.
    """
    floor = version_key(installed)
    selected, highest = None, None
    for release in releases:
        candidate = release_version(release)
        if candidate is None:
            continue
        if candidate.prerelease and not include_beta and not (allow_same and candidate == floor):
            continue
        if candidate < floor or (candidate == floor and not allow_same):
            continue
        if highest is None or candidate > highest:
            selected, highest = release, candidate
    return selected
