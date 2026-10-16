"""Build public stable and beta downloads from complete GitHub releases.

Exit 3 holds first launch; exit 2 leaves invalid builds untouched. Withdrawal
refreshes may explicitly emit an unavailable page. The withdrawal marker is
shared with the community branch, which has not landed on main yet.
"""
from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.update_plan import BOOTSTRAP_ASSET, MANIFEST_ASSET, ZIP_ASSET
from sm64_events.core.release_contract import version_key

REPOSITORY = "griffinbeels/SM64-Trainer"
WITHDRAWN_MARKER = "<!-- sm64-withdrawn -->"
ASSET_NAMES = tuple(name for base in (ZIP_ASSET, MANIFEST_ASSET, BOOTSTRAP_ASSET)
                    for name in (base, base + ".sha256"))
PUBLIC_FILES = ("index.html", "styles.css", "release.js", "_headers")
OPTIONAL_FILES = ("robots.txt", "sitemap.xml")
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
                  ".woff", ".woff2", ".ttf", ".mp4", ".webm"}
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}


class BuildError(ValueError):
    """Fixed public errors; remote bodies and credential errors stay private."""


class ReleaseNotReady(BuildError):
    """A stable candidate exists but cannot advertise safe downloads yet."""


class LaunchHold(BuildError):
    """No nonwithdrawn community stable version exists."""


def version_tuple(value: object, *, tag: bool = False) -> tuple[int, ...] | None:
    if not isinstance(value, str) or (tag and not value.startswith("v")):
        return None
    match = VERSION_RE.fullmatch(value[1:] if tag else value)
    if not match:
        return None
    try:
        return tuple(int(part) for part in match.groups())
    except ValueError:
        return None


def load_policy(path: Path) -> dict:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BuildError("Release policy could not be read.") from error
    if (not isinstance(policy, dict) or type(policy.get("schema_version")) is not int
            or policy["schema_version"] != 1 or policy.get("repository") != REPOSITORY
            or version_tuple(policy.get("minimum_version")) is None
            or policy.get("site_url") not in ("https://sm64trainer.app", "https://sm64trainer.app/")):
        raise BuildError("Release policy is invalid.")
    return policy


def utc_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ReleaseNotReady("Release timestamps must include a timezone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as error:
        raise ReleaseNotReady("Release timestamps must include a timezone.") from error


def validate_release(release: dict, policy: dict) -> dict:
    tag = release["tag_name"]
    release_url = f"https://github.com/{policy['repository']}/releases/tag/{tag}"
    if (type(release.get("id")) is not int or release["id"] <= 0
            or release.get("html_url") != release_url):
        raise ReleaseNotReady("Release identity or URL is invalid.")
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) != len(ASSET_NAMES):
        raise ReleaseNotReady("The release needs exactly six public assets.")
    validated = {}
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") not in ASSET_NAMES:
            raise ReleaseNotReady("The release asset set is invalid.")
        name = asset["name"]
        url = f"https://github.com/{policy['repository']}/releases/download/{tag}/{name}"
        if (name in validated or asset.get("state") != "uploaded"
                or type(asset.get("size")) is not int or asset["size"] <= 0
                or type(asset.get("id")) is not int or asset["id"] <= 0
                or asset.get("browser_download_url") != url):
            raise ReleaseNotReady("A release asset is duplicate, incomplete or unsafe.")
        validated[name] = {"id": asset["id"], "url": url, "size": asset["size"],
                           "uploaded_at": utc_timestamp(asset.get("created_at"))}
    if set(validated) != set(ASSET_NAMES):
        raise ReleaseNotReady("The release is missing a required asset.")
    installer = validated[BOOTSTRAP_ASSET]
    return {"status": "ready", "version": tag[1:], "tag": tag,
            "release_id": release["id"], "release_url": release_url,
            "published_at": utc_timestamp(release.get("published_at")),
            "installer_url": installer["url"], "installer_size": installer["size"],
            "installer_uploaded_at": installer["uploaded_at"], "assets": validated,
            "checksums": {base: validated[base + ".sha256"]["url"]
                          for base in (ZIP_ASSET, MANIFEST_ASSET, BOOTSTRAP_ASSET)}}


def select_release(feed: object, policy: dict) -> dict:
    releases = [feed] if isinstance(feed, dict) else feed
    if not isinstance(releases, list) or any(not isinstance(item, dict) for item in releases):
        raise BuildError("Release feed must be a release object or list of objects.")
    minimum = version_tuple(policy["minimum_version"])
    candidates, seen = [], set()
    for release in releases:
        version = version_tuple(release.get("tag_name"), tag=True)
        if version is None or version < minimum:
            continue
        if type(release.get("draft")) is not bool or type(release.get("prerelease")) is not bool:
            raise ReleaseNotReady("Stable release visibility flags are invalid.")
        if release["draft"] or release["prerelease"]:
            continue
        body = release.get("body")
        if body is None:
            body = ""
        if not isinstance(body, str):
            raise ReleaseNotReady("Stable release withdrawal status is invalid.")
        if WITHDRAWN_MARKER in body:
            continue
        if version in seen:
            raise ReleaseNotReady("The feed repeats a stable release version.")
        seen.add(version)
        candidates.append((version, release))
    if not candidates:
        raise LaunchHold("No eligible community stable release is available.")
    # Never silently downgrade while a newer stable release is incomplete.
    return validate_release(max(candidates, key=lambda item: item[0])[1], policy)


def select_beta(feed: object, policy: dict) -> dict:
    """Select a published beta by the installer's shared SemVer precedence.

    A beta may launch before the stable floor ships, but its core must reach
    that floor. Validate the newest candidate rather than quietly downgrading.
    """
    releases = [feed] if isinstance(feed, dict) else feed
    if not isinstance(releases, list) or any(not isinstance(item, dict) for item in releases):
        raise BuildError("Release feed must be a release object or list of objects.")
    candidates, seen = [], set()
    for release in releases:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v") or "+" in tag:
            continue
        try:
            version = version_key(tag)
        except ValueError:
            continue
        if (version.core < version_tuple(policy["minimum_version"])
                or not version.prerelease or version.prerelease[0] != "beta"):
            continue
        if type(release.get("draft")) is not bool or type(release.get("prerelease")) is not bool:
            raise ReleaseNotReady("Beta release visibility flags are invalid.")
        if release["draft"] or not release["prerelease"]:
            continue
        body = release.get("body")
        if body is not None and not isinstance(body, str):
            raise ReleaseNotReady("Beta release withdrawal status is invalid.")
        if WITHDRAWN_MARKER in (body or ""):
            continue
        if version in seen:
            raise ReleaseNotReady("The feed repeats a beta release version.")
        seen.add(version)
        candidates.append((version, release))
    if not candidates:
        raise LaunchHold("No eligible public beta release is available.")
    return {**validate_release(max(candidates, key=lambda item: item[0])[1], policy), "channel": "beta"}


def select_channels(feed: object, policy: dict, allow_unavailable: bool = False) -> tuple[dict, dict]:
    """One feed snapshot controls both routes; neither channel lowers the stable floor."""
    channels = []
    for selector in (select_release, select_beta):
        try:
            channels.append(selector(feed, policy))
        except LaunchHold:
            channels.append({"status": "unavailable", "reason": "no_eligible_release",
                             "release_url": f"https://github.com/{policy['repository']}/releases"})
        except ReleaseNotReady:
            if not allow_unavailable:
                raise
            channels.append({"status": "unavailable", "reason": "release_not_ready",
                             "release_url": f"https://github.com/{policy['repository']}/releases"})
    stable, beta = channels
    beta["channel"] = "beta"
    if stable["status"] != "ready" and beta["status"] == "ready":
        stable["status"] = "beta_only"
    elif stable["status"] != "ready" and not allow_unavailable:
        raise LaunchHold("No eligible community stable or public beta release is available.")
    return stable, beta


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BuildError("GitHub release feed redirects are not accepted.")


def fetch_releases(policy: dict) -> list:
    opener = urllib.request.build_opener(_NoRedirect())
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SM64Trainer-website",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    releases = []
    try:
        for page in range(1, 11):
            url = f"https://api.github.com/repos/{policy['repository']}/releases?per_page=100&page={page}"
            with opener.open(urllib.request.Request(url, headers=headers), timeout=15) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise BuildError("GitHub release feed exceeded the size limit.")
            batch = json.loads(raw)
            if not isinstance(batch, list):
                raise BuildError("GitHub release feed is invalid.")
            releases.extend(batch)
            if len(batch) < 100:
                return releases
        raise BuildError("GitHub release feed exceeded the pagination limit.")
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise BuildError("GitHub release feed could not be read.") from error


def resolve_release(policy: dict, fixture: Path | None, attempts: int, retry_delay: float,
                    allow_unavailable: bool, *, channels: bool = False) -> dict | tuple[dict, dict]:
    if fixture is not None:
        try:
            feed = json.loads(fixture.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise BuildError("Offline release fixture could not be read.") from error
    for attempt in range(1 if fixture is not None else attempts):
        try:
            snapshot = feed if fixture is not None else fetch_releases(policy)
            if channels:
                # Retry incomplete uploads before an explicit withdrawal removes links.
                try:
                    return select_channels(snapshot, policy)
                except LaunchHold:
                    if allow_unavailable:
                        return select_channels(snapshot, policy, True)
                    raise
                except ReleaseNotReady:
                    if allow_unavailable and (fixture is not None or attempt + 1 >= attempts):
                        return select_channels(snapshot, policy, True)
                    raise
            return select_release(snapshot, policy)
        except LaunchHold:
            if not allow_unavailable:
                raise
            return {"status": "unavailable", "reason": "no_eligible_release",
                    "release_url": f"https://github.com/{policy['repository']}/releases"}
        except ReleaseNotReady:
            if fixture is None and attempt + 1 < attempts:
                time.sleep(retry_delay)
                continue
            if not allow_unavailable:
                raise
            return {"status": "unavailable", "reason": "release_not_ready",
                    "release_url": f"https://github.com/{policy['repository']}/releases"}
        except BuildError:
            if fixture is not None or attempt + 1 >= attempts:
                raise
            time.sleep(retry_delay)
    raise BuildError("No release resolution completed.")


def _start_tag(tag: str, attrs: list, updates: dict) -> str:
    merged = dict(attrs)
    merged.update(updates)
    return "<" + tag + "".join(" " + key if value is None else
                              f' {key}="{html.escape(value, quote=True)}"'
                              for key, value in merged.items()) + ">"


class _Template(HTMLParser):
    """Locate approved DOM hooks without rewriting unrelated template markup."""

    def __init__(self, source: str, metadata: dict, site_url: str):
        super().__init__(convert_charrefs=False)
        self.source, self.metadata, self.site_url = source, metadata, site_url
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.stack, self.edits = [], []
        self.counts = {"download": 0, "label": 0, "metadata": 0}

    def position(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self.position()
        end = start + len(self.get_starttag_text())
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        kind = None
        copy_key = values.get("data-site-copy")
        if copy_key and copy_key in self.page_copy:
            kind = "copy:" + copy_key
        if "download-link" in classes:
            if tag != "a":
                raise BuildError("A download hook must be an anchor.")
            self.edits.append((start, end, _start_tag(tag, attrs, {"href": self.download_url})))
            self.counts["download"] += 1
        elif tag == "a" and values.get("href") == f"https://github.com/{REPOSITORY}/releases/latest":
            self.edits.append((start, end, _start_tag(tag, attrs, {"href": self.metadata["release_url"]})))
        if "download-label" in classes:
            if tag != "span" or not any("download-link" in (dict(item[3]).get("class") or "").split()
                                      for item in self.stack):
                raise BuildError("Download labels must belong to download anchors.")
            kind = "label"
        elif values.get("id") == "release-meta":
            kind = "metadata"
        elif "preview-note" in classes or tag == "noscript":
            kind = "remove"
        if tag == "link" and values.get("rel") == "canonical":
            self.edits.append((start, end, _start_tag(tag, attrs, {"href": self.site_url})))
        elif tag == "meta" and values.get("property") == "og:url":
            self.edits.append((start, end, _start_tag(tag, attrs, {"content": self.site_url})))
        elif tag == "meta" and self.metadata.get("channel") == "beta":
            key = values.get("property") or values.get("name")
            if key in ("og:title", "og:description", "description"):
                value = self.page_copy["title"] if key == "og:title" else self.page_copy["description"]
                self.edits.append((start, end, _start_tag(tag, attrs, {"content": value})))
        if tag not in VOID_TAGS:
            self.stack.append((tag, start, end, attrs, kind))

    def handle_startendtag(self, tag, attrs):
        if tag in VOID_TAGS:
            self.handle_starttag(tag, attrs)

    @property
    def download_url(self) -> str:
        if self.metadata["status"] == "beta_only":
            return "/beta/"
        return self.metadata.get("installer_url", self.metadata["release_url"])

    @property
    def page_copy(self) -> dict:
        if self.metadata.get("channel") == "beta":
            return {"title": "SM64 Trainer — Try the Ghost Overlay beta",
                    "hero": "Try the Ghost<br>Overlay <span>beta.</span>",
                    "description": "Early access to community ghosts in SM64 Trainer. Install once, sign in, and practice.",
                    "notice": "Early access: expect rough edges. Install once to receive future published beta and production updates in the same app, with your profile and practice history.",
                    "signup": "Anyone can join. Sign in with Discord or Twitch, choose your name, and review ghost sharing before you practice.",
                    "route": '<a class="text-link" href="/">SM64 Trainer home ↗</a>'}
        notice = ("The Ghost Overlay is available in early access. The community stable release is not available yet. "
                  if self.metadata["status"] == "beta_only" else "")
        return {"notice": notice,
                "route": '<a class="text-link" href="/beta/">Try the Ghost Overlay beta ↗</a>'}

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise BuildError("The website template has mismatched tags.")
        _, start, content_start, _, kind = self.stack.pop()
        if kind == "remove":
            self.edits.append((start, self.position() + len(f"</{tag}>"), ""))
        elif kind in ("label", "metadata"):
            self.counts[kind] += 1
            value = self.release_text() if kind == "metadata" else (
                ("Download beta for Windows" if self.metadata.get("channel") == "beta" else "Download for Windows")
                if self.metadata["status"] == "ready" else
                "Try the Ghost Overlay beta" if self.metadata["status"] == "beta_only" else "View release history")
            self.edits.append((content_start, self.position(), value))
        elif kind and kind.startswith("copy:"):
            self.edits.append((content_start, self.position(), self.page_copy[kind[5:]]))

    def release_text(self) -> str:
        if self.metadata["status"] == "beta_only":
            return 'Community stable release coming later. <a href="/beta/">Get the current public beta.</a>'
        if self.metadata["status"] != "ready":
            return "Downloads are temporarily unavailable. Please check the release history for updates."
        uploaded = datetime.fromisoformat(self.metadata["installer_uploaded_at"].replace("Z", "+00:00"))
        date = f"{uploaded:%b} {uploaded.day}, {uploaded:%Y at %H:%M} UTC"
        text = f"{self.metadata['tag']} · {self.metadata['installer_size'] / 1_000_000:.1f} MB installer · Uploaded "
        timestamp = html.escape(self.metadata["installer_uploaded_at"], quote=True)
        clock = f'<time data-release-upload datetime="{timestamp}">{html.escape(date)}</time> '
        return (html.escape(text) + clock + f'<a href="{html.escape(self.metadata["release_url"], quote=True)}" '
                'target="_blank" rel="noopener noreferrer">Release notes ↗</a>')

    def render(self) -> str:
        self.feed(self.source)
        self.close()
        if self.stack or self.counts != {"download": 3, "label": 3, "metadata": 1}:
            raise BuildError("The website template must have three downloads and one metadata hook.")
        rendered = self.source
        previous = len(rendered)
        for start, end, value in sorted(self.edits, reverse=True):
            if end > previous:
                raise BuildError("Website template hooks overlap.")
            rendered = rendered[:start] + value + rendered[end:]
            previous = start
        return rendered


def public_files(source: Path) -> dict[str, bytes]:
    if not source.is_dir() or source.is_symlink() or source.is_junction():
        raise BuildError("Website source must be a real directory.")
    files = {}
    for name in PUBLIC_FILES + OPTIONAL_FILES:
        path = source / name
        if not path.exists() and name in OPTIONAL_FILES:
            continue
        if not path.is_file() or path.is_symlink() or path.is_junction():
            raise BuildError("A required public website file is missing or linked.")
        files[name] = path.read_bytes()
    assets = source / "assets"
    if not assets.is_dir() or assets.is_symlink() or assets.is_junction():
        raise BuildError("Website assets must be a real directory.")
    for path in sorted(assets.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise BuildError("Website assets may not contain symbolic links or junctions.")
        relative = path.relative_to(source)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_file() and path.suffix.lower() in ASSET_SUFFIXES:
            files[relative.as_posix()] = path.read_bytes()
    return files


def write_site(source: Path, output: Path, policy: dict, release: dict, beta: dict | None = None) -> dict:
    if source.is_symlink() or source.is_junction() or output.is_symlink() or output.is_junction():
        raise BuildError("Website source and output may not be linked directories.")
    source, output = source.resolve(), output.resolve()
    if output.exists() or output == source or output.is_relative_to(source):
        raise BuildError("Output must be a new directory outside the website source.")
    files = public_files(source)
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8") + b"\0" + content + b"\0")
    metadata = {"schema_version": 1, "repository": policy["repository"],
                "minimum_version": policy["minimum_version"], **release,
                "build": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                          "source_sha256": digest.hexdigest()}}
    commit = os.environ.get("WEBSITE_SOURCE_SHA", "")
    if re.fullmatch(r"[0-9a-f]{40}", commit):
        metadata["build"]["commit"] = commit
    try:
        template = files["index.html"].decode("utf-8")
        files["index.html"] = _Template(template, metadata,
                                         policy["site_url"]).render().encode("utf-8")
        if beta is not None:
            beta_metadata = {**metadata, **beta, "channel": "beta"}
            # Stable-only fields must not leak into an unavailable beta route.
            beta_metadata = {key: value for key, value in beta_metadata.items()
                             if key in ("schema_version", "repository", "minimum_version", "build", "channel") or key in beta}
            files["beta/index.html"] = _Template(template, beta_metadata,
                                                   policy["site_url"].rstrip("/") + "/beta/").render().encode("utf-8")
            files["beta/release.json"] = (json.dumps(beta_metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
            metadata["beta"] = {"status": beta["status"], "path": "/beta/", "metadata": "/beta/release.json"}
            if beta.get("version"):
                metadata["beta"]["version"] = beta["version"]
        script = files["release.js"].decode("utf-8")
        if "api.github.com" in script or re.search(r"\bfetch\s*\(", script):
            raise BuildError("Website JavaScript must not fetch release metadata.")
    except UnicodeError as error:
        raise BuildError("Website source must be UTF-8.") from error
    files["release.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".website-build-", dir=output.parent))
    try:
        for name, content in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if output.exists():
            raise BuildError("Output was created by another operation.")
        staging.rename(output)
    finally:
        if staging.exists():
            if staging.resolve().parent != output.parent or not staging.name.startswith(".website-build-"):
                raise BuildError("Website staging cleanup escaped its parent.")
            shutil.rmtree(staging)
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "website")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--releases-json", type=Path)
    parser.add_argument("--attempts", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--retry-delay", type=float, default=10)
    parser.add_argument("--allow-unavailable", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.retry_delay <= 30:
        parser.error("--retry-delay must be between 0 and 30 seconds")
    try:
        policy = load_policy(args.policy or args.source / "release-policy.json")
        release, beta = resolve_release(policy, args.releases_json, args.attempts,
                                        args.retry_delay, args.allow_unavailable, channels=True)
        metadata = write_site(args.source, args.output, policy, release, beta)
        print(json.dumps({"status": metadata["status"], "output": str(args.output),
                          "version": metadata.get("version")}))
        return 0
    except LaunchHold as error:
        print(json.dumps({"status": "launch_hold", "message": str(error)}))
        return 3
    except (BuildError, OSError) as error:
        message = str(error) if isinstance(error, BuildError) else "Website build IO failed."
        print(json.dumps({"status": "invalid", "message": message}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
