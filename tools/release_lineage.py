"""Read-only public publication gates; private preparation never queries GitHub.

SemVer orders artifacts. Git ancestry preserves product continuity. Human QA
separately attests stored-profile compatibility against the discovered heads;
neither source ancestry nor version ordering can establish that compatibility.
"""
from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import quote

from sm64_events.core.release_contract import version_key
from sm64_events.core.update_plan import BOOTSTRAP_ASSET, MANIFEST_ASSET, ZIP_ASSET

REPOSITORY = "griffinbeels/SM64-Trainer"
MAX_PAGES = 12
_RELEASE_FIELDS = "map({tag_name,draft,assets:(.assets | map({name}))})"
_OBJECT_FIELDS = "{object:{type:.object.type,sha:.object.sha}}"


def github_json(run, endpoint: str, fields: str):
    result = run(["gh", "api", endpoint, "--jq", fields], capture_output=True, text=True, timeout=60)
    value = result.stdout
    if not isinstance(value, str) or len(value.encode("utf-8")) > (1 << 20):
        raise ValueError("published release metadata exceeds its limit")
    return json.loads(value)


def published_products(run) -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        values = github_json(run, f"repos/{REPOSITORY}/releases?per_page=100&page={page}", _RELEASE_FIELDS)
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("published release history is invalid")
        rows.extend(values)
        if len(values) < 100:
            return rows
    raise ValueError("published release history is incomplete; publication requires complete discovery")


def _product(row):
    if not isinstance(row, dict) or type(row.get("draft")) is not bool:
        raise ValueError("published release metadata is invalid")
    if row["draft"]:
        return None
    try:
        key = version_key(row.get("tag_name"))
    except ValueError:
        return None  # Unrelated historical tags do not define product precedence.
    assets = row.get("assets")
    if not isinstance(assets, list) or len(assets) > 256:
        raise ValueError("published product assets are invalid")
    if any(not isinstance(asset, dict) or not isinstance(asset.get("name"), str) for asset in assets):
        raise ValueError("published product asset names are invalid")
    names = {asset["name"] for asset in assets}
    full = {ZIP_ASSET, ZIP_ASSET + ".sha256", MANIFEST_ASSET, MANIFEST_ASSET + ".sha256"}
    legacy = {BOOTSTRAP_ASSET, BOOTSTRAP_ASSET + ".sha256"}
    return key if full <= names or legacy <= names else None


def predecessor_rows(rows: list[dict], version: str, channel: str) -> list[dict]:
    current = version_key(version)
    products = [(key, row) for row in rows if (key := _product(row)) is not None]
    if len({key for key, _ in products}) != len(products):
        raise ValueError("published product precedence is ambiguous")
    if any(key == current for key, _ in products):
        raise ValueError("this product version is already published and immutable")
    relevant_head = [key for key, _ in products if channel == "beta" or not key.prerelease]
    if relevant_head and current <= max(relevant_head):
        raise ValueError("publication must advance the eligible published head")
    lower = [(key, row) for key, row in products if key < current]
    if not lower:
        return []
    # Check both last production and the closest lower beta when they differ.
    # A next-version beta above a final stable candidate never blocks catch-up.
    previous = [max(lower, key=lambda item: item[0])]
    stable = [(key, row) for key, row in lower if not key.prerelease]
    if stable:
        stable_head = max(stable, key=lambda item: item[0])
        if stable_head[0] != previous[0][0]:
            previous.append(stable_head)
    return [row for _, row in previous]


def published_source(run, tag: str, *, allow_missing: bool = False) -> str | None:
    try:
        value = github_json(run, f"repos/{REPOSITORY}/git/ref/tags/{quote(tag, safe='')}", _OBJECT_FIELDS)
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr
        if allow_missing and isinstance(message, str) and re.search(r"\bHTTP 404\b", message):
            return None  # An unpublished draft may not have created its tag yet.
        raise
    for _ in range(5):
        obj = value.get("object") if isinstance(value, dict) else None
        if (not isinstance(obj, dict) or not isinstance(obj.get("sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", obj["sha"])):
            raise ValueError("published product source could not be resolved")
        if obj.get("type") == "commit":
            return obj["sha"]
        if obj.get("type") != "tag":
            break
        value = github_json(run, f"repos/{REPOSITORY}/git/tags/{obj['sha']}", _OBJECT_FIELDS)
    raise ValueError("published product tag does not resolve to a bounded commit")


def _ancestor(run, before: str, source: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("candidate source identity is invalid")
    try:
        run(["git", "cat-file", "-e", before + "^{commit}"], capture_output=True)
    except subprocess.CalledProcessError:
        # Obtain that immutable object without changing local branches or tags.
        run(["git", "fetch", "--no-tags", f"https://github.com/{REPOSITORY}.git", before],
            capture_output=True, timeout=120)
    try:
        run(["git", "merge-base", "--is-ancestor", before, source], capture_output=True)
    except subprocess.CalledProcessError:
        raise ValueError("candidate drops the previously published product lineage; review a new forward candidate") from None


def verify_publication(record: dict, approval: dict, run) -> dict:
    compatibility = approval.get("profile_compatibility")
    if not isinstance(compatibility, dict) or len(compatibility) > 16:
        raise ValueError("public publication requires explicit stored-profile compatibility QA")
    prior = predecessor_rows(published_products(run), record["version"], record["channel"])
    previous = []
    for row in prior:
        tag = row["tag_name"]
        source = published_source(run, tag)
        _ancestor(run, source, record["source_commit"])
        version = tag.lstrip("vV")
        attestation = compatibility.get(version)
        if (not isinstance(attestation, dict) or attestation.get("compatible") is not True
                or attestation.get("source_commit") != source):
            raise ValueError("stored-profile QA must cover the exact discovered published predecessor")
        previous.append({"version": version, "source_commit": source, "profile_compatible": True})
    return {"schema": 1, "version": record["version"], "source_commit": record["source_commit"],
            "previous": previous}


def verify_draft(record: dict, run) -> None:
    result = run(["gh", "release", "view", "v" + record["version"], "--repo", REPOSITORY,
                  "--json", "isDraft,isPrerelease,tagName,targetCommitish"],
                 capture_output=True, text=True, timeout=60)
    value = json.loads(result.stdout)
    if (not isinstance(value, dict) or value.get("isDraft") is not True
            or value.get("isPrerelease") is not (record["channel"] == "beta")
            or value.get("tagName") != "v" + record["version"]
            or value.get("targetCommitish") != record["source_commit"]):
        raise ValueError("draft identity does not match the candidate's exact version, channel and source")
    actual = published_source(run, value["tagName"], allow_missing=True)
    if actual is not None and actual != record["source_commit"]:
        raise ValueError("existing draft tag points to a different product source")


def bind_publication_tag(record: dict, run) -> None:
    """Create only a missing tag; a concurrent identical create is safe to resume."""
    tag = "v" + record["version"]
    if published_source(run, tag, allow_missing=True) is None:
        try:
            run(["gh", "api", f"repos/{REPOSITORY}/git/refs", "--method", "POST",
                 "-f", "ref=refs/tags/" + tag, "-f", "sha=" + record["source_commit"]],
                capture_output=True, text=True, timeout=60)
        except subprocess.CalledProcessError:
            pass  # A failed response may follow a completed concurrent create.
    if published_source(run, tag) != record["source_commit"]:
        raise ValueError("publication tag points to a different product source")
