"""Two operator actions over release.py's shared prepare/verify/publication phases.

Ship beta prepares an immutable candidate. Release approved beta promotes its
approved source to a separately built stable candidate. Neither action publishes
unless the operator subsequently runs release.py's explicit draft/publish phase.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import subprocess
import tomllib

import release
from release_candidate import public_fingerprint, read_record, require_approval
from sm64_events.core.build_channel import read_metadata
from sm64_events.core.release_contract import version_key


def _version_only(path: str, before: str, after: str, version: str) -> bool:
    """Allow the product's own version field; preserve every dependency/config field."""
    if path.endswith("version.py"):
        return release.bump_version_py(before, version) == after
    old, new = tomllib.loads(before), tomllib.loads(after)
    if path == "pyproject.toml":
        old_project, new_project = old["project"], new["project"]
        if new_project.get("version") != version:
            return False
        new_project["version"] = old_project["version"]
    else:
        def own_package(value):
            own = [item for item in value["package"] if item.get("name") == "sm64-events"]
            if len(own) != 1:
                raise ValueError("dependency lock must contain exactly one product package")
            return own[0]
        old_package, new_package = own_package(old), own_package(new)
        if new_package.get("version") != version:
            return False
        new_package["version"] = old_package["version"]
    return old == new


def _approved_source_changes(source: str, version: str) -> None:
    changed = release._capture(["git", "diff", "--name-only", source, "HEAD"]).splitlines()
    allowed = {"src/sm64_events/core/version.py", "pyproject.toml", "uv.lock"}
    root_guides = {"AGENTS.md", "CLAUDE.md", "README.md"}
    harmless = {name for name in changed if (name.startswith("docs/") and name.endswith(".md"))
                or name in root_guides or name.startswith("tests/")
                or name == "tools/release_lineage.py"}
    tooling = {"tools/release.py", "tools/release_flow.py"}
    if set(changed) - allowed - harmless - tooling:
        raise ValueError("product source changed after beta approval; prepare and test a new beta")
    for name in changed:
        if name in harmless:
            continue
        before = release._capture(["git", "show", f"{source}:{name}"])
        after = release._capture(["git", "show", f"HEAD:{name}"])
        valid = _publication_only(name, before, after) if name in tooling else _version_only(name, before, after, version)
        if not valid:
            raise ValueError("product dependencies or configuration changed after beta approval; test a new beta")


def _publication_only(path: str, before: str, after: str) -> bool:
    allowed = ({"publication_preflight", "draft_candidate", "publish_candidate"} if path == "tools/release.py"
               else {"_approved_source_changes", "_publication_only"})
    def normalized(text):
        module = ast.parse(text)
        module.body = [node for node in module.body
                       if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in allowed)
                       and not (isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].name == "ast")]
        return ast.dump(module, include_attributes=False)
    return normalized(before) == normalized(after)


def promotion_source(candidate: Path, approval: Path, stable_version: str, config: Path) -> str:
    original = read_record(candidate)
    approved = require_approval(candidate, approval)
    old, new = version_key(original["version"]), version_key(stable_version)
    if original["channel"] != "beta" or not old.prerelease or new.prerelease or old.core != new.core:
        raise ValueError("promotion requires the approved beta's corresponding stable version")
    build = read_metadata(config)
    if build.channel != "stable" or build.installation != "normal" or build.friend_invitation:
        raise ValueError("stable promotion requires normal public build configuration")
    if public_fingerprint(build) != original["public_config_sha256"]:
        raise ValueError("promotion cannot change the approved public community configuration")
    # A merge commit is fine; product code changing after beta QA is not.
    release._run(["git", "merge-base", "--is-ancestor", original["source_commit"], "HEAD"], capture_output=True)
    _approved_source_changes(original["source_commit"], stable_version)
    return approved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    beta = actions.add_parser("ship-beta", help="prepare and verify a beta; no publication")
    stable = actions.add_parser("release-approved-beta", help="prepare stable from exact approved beta; no publication")
    for command in (beta, stable):
        command.add_argument("version")
        command.add_argument("destination", type=Path)
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--ffmpeg")
        command.add_argument("--ghosts", type=Path)
        command.add_argument("--ghost-author", type=Path, help="reviewed author Auth issuer/subject binding JSON")
        command.add_argument("--service-provenance", type=Path,
                             help="bounded public worker/catalog/migration revision JSON")
        command.add_argument("--check", action="store_true")
    beta.add_argument("--public", action="store_true", help="prepare public assets without a private invitation")
    stable.add_argument("--beta-candidate", required=True, type=Path)
    stable.add_argument("--approval", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        import json
        provenance = json.loads(args.service_provenance.read_text(encoding="utf-8")) if args.service_provenance else None
        if args.action == "ship-beta":
            if not version_key(args.version).prerelease:
                raise ValueError("Ship beta requires a prerelease version")
            promoted = None
            private = not args.public
        else:
            promoted = promotion_source(args.beta_candidate, args.approval, args.version, args.config)
            private = False
        release.prepare_candidate(args.version, args.destination, config=args.config, ffmpeg=args.ffmpeg,
                                  ghosts=args.ghosts, ghost_author=args.ghost_author, private=private, check_only=args.check,
                                  service_provenance=provenance, promoted_from=promoted)
        print("Prerequisites verified; no build run." if args.check else
              "Candidate prepared and verified. No tag, push, release or deployment was performed.")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        # Configuration can contain a one-use invitation; do not print external
        # exception bodies, process output or supplied values.
        print("Release preparation refused. Check the source, configuration, prerequisites and candidate receipts.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
