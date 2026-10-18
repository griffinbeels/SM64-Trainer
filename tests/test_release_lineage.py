"""Public publication continuity contracts; real disposable Git, no provider calls."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release
import release_candidate as candidate
import release_flow
import release_lineage as lineage
from test_bootstrap_installer import FULL_ASSETS
from test_release_candidate import prepared


def product(version, **fields):
    return {"tag_name": "v" + version, "draft": False,
            "assets": [{"name": name} for name in FULL_ASSETS], **fields}


@pytest.fixture
def commits(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    monkeypatch.setattr(release, "REPO", repo)
    for args in (["init", "-q"], ["config", "core.longpaths", "true"],
                 ["config", "user.name", "Disposable QA"], ["config", "user.email", "qa@example.invalid"]):
        release._run(["git", *args], capture_output=True)
    def commit(name):
        (repo / name).write_text(name)
        release._run(["git", "add", name], capture_output=True)
        release._run(["git", "commit", "-qm", name], capture_output=True)
        return release._capture(["git", "rev-parse", "HEAD"])
    initial = commit("initial")
    previous = commit("published-features")
    current = commit("candidate-features")
    release._run(["git", "checkout", "--detach", initial], capture_output=True)
    old_fork = commit("old-fork-higher-version")
    return previous, current, old_fork


def network(rows, tags, *, pages=None):
    def run(args, **kwargs):
        if args[0] == "git":
            return release._run(args, **kwargs)
        assert args[:2] == ["gh", "api"], "lineage gate tried to mutate GitHub"
        endpoint = args[2]
        if "/releases?" in endpoint:
            value = pages(int(endpoint.split("page=")[-1])) if pages else rows
        elif "/git/ref/tags/" in endpoint:
            value = {"object": tags[endpoint.split("/git/ref/tags/")[-1]]}
        elif "/git/tags/" in endpoint:
            value = {"object": tags[endpoint.rsplit("/", 1)[-1]]}
        else:
            pytest.fail("unexpected endpoint")
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
    return run


def record(source, version="1.10.0-beta.1", channel="beta"):
    return {"source_commit": source, "version": version, "channel": channel}


def test_old_fork_cannot_publish_a_higher_beta_even_when_other_gates_pass(commits):
    previous, _, old_fork = commits
    run = network([product("1.9.1")], {"v1.9.1": {"type": "commit", "sha": previous}})
    approval = {"profile_compatibility": {"1.9.1": {"source_commit": previous, "compatible": True}}}
    with pytest.raises(ValueError, match="drops the previously published product lineage"):
        lineage.verify_publication(record(old_fork), approval, run)


def test_actual_remote_annotated_tag_commit_and_explicit_profile_qa_are_bound(commits):
    previous, current, _ = commits
    run = network([product("1.9.1")], {"v1.9.1": {"type": "tag", "sha": "d" * 40},
                  "d" * 40: {"type": "commit", "sha": previous}})
    approval = {"profile_compatibility": {"1.9.1": {"source_commit": previous, "compatible": True}}}
    proof = lineage.verify_publication(record(current), approval, run)
    assert proof["source_commit"] == current
    assert proof["previous"] == [{"version": "1.9.1", "source_commit": previous, "profile_compatible": True}]


@pytest.mark.parametrize("attestation", [None, {}, {"source_commit": "a" * 40, "compatible": True},
                                         {"source_commit": "match", "compatible": False}])
def test_profile_qa_is_not_inferred_from_clean_ancestral_source(commits, attestation):
    previous, current, _ = commits
    if attestation and attestation.get("source_commit") == "match":
        attestation["source_commit"] = previous
    run = network([product("1.9.1")], {"v1.9.1": {"type": "commit", "sha": previous}})
    with pytest.raises(ValueError, match="stored-profile QA"):
        lineage.verify_publication(record(current), {"profile_compatibility": {"1.9.1": attestation}}, run)


def test_stable_catches_up_with_ahead_next_version_beta_without_downgrade(commits):
    previous, current, old_fork = commits
    rows = [product("1.9.1"), product("1.10.0-beta.2"), product("1.11.0-beta.1")]
    tags = {"v1.9.1": {"type": "commit", "sha": previous},
            "v1.10.0-beta.2": {"type": "commit", "sha": current},
            "v1.11.0-beta.1": {"type": "commit", "sha": old_fork}}
    qa = {"profile_compatibility": {"1.9.1": {"source_commit": previous, "compatible": True},
                                   "1.10.0-beta.2": {"source_commit": current, "compatible": True}}}
    proof = lineage.verify_publication(record(current, "1.10.0", "stable"), qa, network(rows, tags))
    assert {row["version"] for row in proof["previous"]} == {"1.9.1", "1.10.0-beta.2"}


@pytest.mark.parametrize("version,channel", [("1.10.0-beta.2", "beta"), ("1.9.2", "stable")])
def test_each_channel_must_advance_its_eligible_head(version, channel):
    with pytest.raises(ValueError, match="advance the eligible published head"):
        lineage.predecessor_rows([product("1.10.0"), product("1.11.0-beta.1")], version, channel)


def test_published_equal_precedence_is_immutable_and_aliases_are_ambiguous():
    with pytest.raises(ValueError, match="immutable"):
        lineage.predecessor_rows([product("1.10.0-beta.1")], "1.10.0-beta.1", "beta")
    with pytest.raises(ValueError, match="ambiguous"):
        lineage.predecessor_rows([product("1.9.1"), product("1.9.1", tag_name="1.9.1")], "1.10.0", "stable")


def test_discovery_paginates_and_refuses_ambiguous_truncated_history():
    calls = []
    def pages(page):
        calls.append(page)
        return [product("1.9.1", draft=True)] * 100 if page == 1 else [product("1.9.1")]
    assert len(lineage.published_products(network([], {}, pages=pages))) == 101
    assert calls == [1, 2]
    with pytest.raises(ValueError, match="history is incomplete"):
        lineage.published_products(network([], {}, pages=lambda _: [product("1.9.1", draft=True)] * 100))


def test_publication_only_source_changes_preserve_packaging_and_runtime_hooks():
    before = 'def prepare_candidate():\n    return "approved payload"\ndef publication_preflight():\n    return False\n'
    after = before.replace("return False", "return True")
    assert release_flow._publication_only("tools/release.py", before, after)
    assert not release_flow._publication_only("tools/release.py", before, after.replace("approved payload", "changed payload"))


@pytest.mark.parametrize("path,allowed", [("docs/release-pipeline.md", True), ("README.md", True),
                                         ("src/sm64_events/data/legal.md", False)])
def test_promotion_document_exemption_does_not_include_packaged_runtime_data(monkeypatch, path, allowed):
    monkeypatch.setattr(release, "_capture", lambda _: path)
    if allowed:
        release_flow._approved_source_changes("a" * 40, "1.10.0")
    else:
        with pytest.raises(ValueError, match="product source changed"):
            release_flow._approved_source_changes("a" * 40, "1.10.0")


def test_public_preflight_binds_lineage_to_exact_candidate_and_human_approval(tmp_path, monkeypatch):
    directory, _, _ = prepared(tmp_path, private=False)
    candidate_sha = candidate.digest(directory / candidate.RECORD)
    (directory / candidate.VERIFICATION).write_text(json.dumps({"draft_ready": True, "candidate_sha256": candidate_sha}))
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"approved": True, "manual_qa": True, "reviewer": "griff",
                                   "candidate_sha256": candidate_sha, "profile_compatibility": {}}))
    monkeypatch.setattr(release, "UV_LOCK", tmp_path / "uv.lock")
    monkeypatch.setattr(release, "_capture", lambda args: "" if args[1] == "status" else "a" * 40)
    monkeypatch.setattr(release, "_run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0))
    monkeypatch.setattr(release, "_verify_or_run_gate", lambda: None)
    monkeypatch.setattr(release, "_require_full_run", lambda: None)
    calls = []
    def verify(record, approved, run):
        calls.append((record["source_commit"], approved["candidate_sha256"]))
        return {"schema": 1, "source_commit": record["source_commit"], "previous": []}
    monkeypatch.setattr(lineage, "verify_publication", verify)
    release.publication_preflight(directory, approval=approval)
    receipt = json.loads((directory / "candidate-lineage.json").read_text())
    assert calls == [("a" * 40, candidate_sha)]
    assert receipt["candidate_sha256"] == candidate_sha
    assert receipt["approval_sha256"] == candidate.digest(approval)


def test_existing_draft_tag_cannot_hide_wrong_source_behind_matching_target_commitish():
    value = {"isDraft": True, "isPrerelease": True, "tagName": "v1.10.0-beta.1", "targetCommitish": "a" * 40}
    resolved = []
    def run(args, **kwargs):
        if args[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(value))
        assert args[:2] == ["gh", "api"]
        resolved.append(True)
        return subprocess.CompletedProcess(args, 0, json.dumps({"object": {"type": "commit", "sha": "b" * 40}}))
    with pytest.raises(ValueError, match="existing draft tag points to a different product source"):
        lineage.verify_draft(record("a" * 40), run)
    assert resolved == [True]


def test_resumed_wrong_source_draft_is_refused_before_provider_mutation(tmp_path, monkeypatch):
    header, notes = tmp_path / "header.md", tmp_path / "notes.md"
    header.write_text("Setup")
    notes.write_text("Changes")
    monkeypatch.setattr(release, "SETUP_HEADER", header)
    monkeypatch.setattr(candidate, "require_approval", lambda *_: "approved")
    monkeypatch.setattr(release, "publication_preflight", lambda *_, **__: record("a" * 40))
    value = {"isDraft": True, "isPrerelease": True, "tagName": "v1.10.0-beta.1", "targetCommitish": "a" * 40}
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, json.dumps(value), ""))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(value))
        assert args[:2] == ["gh", "api"] and "--method" not in args
        return subprocess.CompletedProcess(args, 0, json.dumps({"object": {"type": "commit", "sha": "b" * 40}}))
    monkeypatch.setattr(release, "_run", run)
    with pytest.raises(ValueError, match="different product source"):
        release.draft_candidate(tmp_path, notes_file=notes, approval=tmp_path / "qa.json")
    assert len(calls) == 2


@pytest.mark.parametrize("status", [404, 403, 429, 500, "network"])
def test_new_draft_discovery_requires_explicit_http_missing_status(tmp_path, monkeypatch, status):
    header, notes = tmp_path / "header.md", tmp_path / "notes.md"
    header.write_text("Setup")
    notes.write_text("Changes")
    monkeypatch.setattr(release, "SETUP_HEADER", header)
    monkeypatch.setattr(candidate, "require_approval", lambda *_: "approved")
    monkeypatch.setattr(release, "publication_preflight", lambda *_, **__: record("a" * 40))

    def discovery(args, **kwargs):
        if args[:3] == ["gh", "release", "view"]:
            # Actual gh release view response omits HTTP status for a missing
            # release. It cannot establish that creation is safe.
            return subprocess.CompletedProcess(args, 1, "", "release not found\n")
        assert args[:2] == ["gh", "api"]
        assert args[2] == "repos/griffinbeels/SM64-Trainer/releases/tags/v1.10.0-beta.1"
        error = "gh: connection timed out" if status == "network" else f"gh: request failed (HTTP {status})"
        return subprocess.CompletedProcess(args, 1, "", error)

    monkeypatch.setattr(subprocess, "run", discovery)
    creates = []

    class CreateRequested(Exception):
        pass

    def create(args, **kwargs):
        assert args[:3] == ["gh", "release", "create"]
        assert "--draft" in args and "--prerelease" in args
        creates.append(args)
        raise CreateRequested

    monkeypatch.setattr(release, "_run", create)
    if status == 404:
        with pytest.raises(CreateRequested):
            release.draft_candidate(tmp_path, notes_file=notes, approval=tmp_path / "qa.json")
        assert len(creates) == 1
    else:
        with pytest.raises(ValueError, match="draft discovery failed"):
            release.draft_candidate(tmp_path, notes_file=notes, approval=tmp_path / "qa.json")
        assert creates == []


@pytest.mark.parametrize("fault", ["source", "version", "channel", "published", None])
def test_draft_resume_and_final_publication_require_exact_identity(fault):
    candidate = record("a" * 40)
    value = {"isDraft": True, "isPrerelease": True, "tagName": "v1.10.0-beta.1", "targetCommitish": "a" * 40}
    if fault == "source":
        value["targetCommitish"] = "main"
    elif fault == "version":
        value["tagName"] = "v1.10.0-beta.2"
    elif fault == "channel":
        value["isPrerelease"] = False
    elif fault == "published":
        value["isDraft"] = False
    def run(args, **kwargs):
        if args[:3] == ["gh", "release", "view"]:
            assert "--repo" in args
            return subprocess.CompletedProcess(args, 0, json.dumps(value))
        assert args[:2] == ["gh", "api"]
        return subprocess.CompletedProcess(args, 0, json.dumps({"object": {"type": "commit", "sha": "a" * 40}}))
    if fault is None:
        lineage.verify_draft(candidate, run)
    else:
        with pytest.raises(ValueError, match="draft identity"):
            lineage.verify_draft(candidate, run)


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_only_explicit_missing_draft_tag_is_tolerated(status):
    def run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr=f"gh: request failed (HTTP {status})")
    if status == 404:
        assert lineage.published_source(run, "v1.10.0-beta.1", allow_missing=True) is None
    else:
        with pytest.raises(subprocess.CalledProcessError):
            lineage.published_source(run, "v1.10.0-beta.1", allow_missing=True)
    with pytest.raises(subprocess.CalledProcessError):
        lineage.published_source(run, "v1.10.0-beta.1")


@pytest.mark.parametrize("tag_source", ["a" * 40, "b" * 40])
def test_final_publication_binds_missing_tag_and_rejects_a_concurrent_wrong_source(tag_source):
    created = []
    def run(args, **kwargs):
        if "--method" in args:
            assert args[args.index("--method") + 1] == "POST" and "sha=" + "a" * 40 in args
            created.append(True)
            return subprocess.CompletedProcess(args, 0, "{}")
        if not created:
            raise subprocess.CalledProcessError(1, args, stderr="gh: Not Found (HTTP 404)")
        return subprocess.CompletedProcess(args, 0, json.dumps({"object": {"type": "commit", "sha": tag_source}}))
    if tag_source == "a" * 40:
        lineage.bind_publication_tag(record("a" * 40), run)
    else:
        with pytest.raises(ValueError, match="different product source"):
            lineage.bind_publication_tag(record("a" * 40), run)
    assert created == [True]
