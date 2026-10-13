"""Offline contract tests; run with stdlib unittest, without the app runtime."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_website as builder

POLICY = {"schema_version": 1, "repository": "griffinbeels/SM64-Trainer",
          "site_url": "https://sm64trainer.app", "minimum_version": "1.10.0"}
# Explicit consumer contract, independent of the generator's registry.
NAMES = ("SM64Trainer-full.zip", "SM64Trainer-full.zip.sha256", "manifest.json",
         "manifest.json.sha256", "SM64Trainer.exe", "SM64Trainer.exe.sha256")


def complete_release(tag="v1.10.0", published="2026-10-14T12:00:00Z"):
    return {"id": 123, "tag_name": tag, "draft": False, "prerelease": False,
            "body": "Public community release.", "published_at": published,
            "html_url": f"https://github.com/{POLICY['repository']}/releases/tag/{tag}",
            "assets": [{"id": index + 1, "name": name, "state": "uploaded",
                        "size": 12_000_000 if name == "SM64Trainer.exe" else 100,
                        "created_at": "2026-10-15T18:30:00+02:00",
                        "browser_download_url": f"https://github.com/{POLICY['repository']}/releases/download/{tag}/{name}"}
                       for index, name in enumerate(NAMES)]}


class ReleaseSelectionTests(unittest.TestCase):
    def test_highest_semver_wins_over_publication_order(self):
        high = complete_release("v1.12.0", "2026-10-13T10:00:00Z")
        backport = complete_release("v1.10.2", "2026-10-20T10:00:00Z")
        selected = builder.select_release([backport, high], POLICY)
        self.assertEqual(selected["tag"], "v1.12.0")
        self.assertEqual(selected["installer_uploaded_at"], "2026-10-15T16:30:00Z")
        self.assertEqual(set(selected["assets"]), set(NAMES))
        self.assertEqual(len(selected["checksums"]), 3)

    def test_old_stable_cannot_launch_community_site(self):
        with self.assertRaises(builder.LaunchHold):
            builder.select_release(complete_release("v1.9.1"), POLICY)

    def test_canonical_tags_and_public_visibility_only(self):
        variants = ["v01.10.0", "v1.010.0", "v1.10.00", "v1.10.0-beta.1",
                    "v1.10.0+build", "1.10.0", "V1.10.0", "v1.10", "v1.10.0/extra"]
        for tag in variants:
            with self.subTest(tag=tag), self.assertRaises(builder.LaunchHold):
                builder.select_release(complete_release(tag), POLICY)
        for field in ("draft", "prerelease"):
            release = complete_release()
            release[field] = True
            with self.subTest(field=field), self.assertRaises(builder.LaunchHold):
                builder.select_release(release, POLICY)

    def test_withdrawal_uses_prior_eligible_release_and_all_withdrawn_holds(self):
        withdrawn = complete_release("v1.11.0")
        withdrawn["body"] += builder.WITHDRAWN_MARKER
        self.assertEqual(builder.select_release([withdrawn, complete_release()], POLICY)["tag"], "v1.10.0")
        with self.assertRaises(builder.LaunchHold):
            builder.select_release(withdrawn, POLICY)

    def test_incomplete_newer_release_does_not_silently_downgrade(self):
        newer = complete_release("v1.11.0")
        newer["assets"].pop()
        with self.assertRaises(builder.ReleaseNotReady):
            builder.select_release([newer, complete_release()], POLICY)

    def test_every_required_asset_is_required(self):
        for name in NAMES:
            release = complete_release()
            release["assets"] = [asset for asset in release["assets"] if asset["name"] != name]
            with self.subTest(name=name), self.assertRaises(builder.ReleaseNotReady):
                builder.select_release(release, POLICY)

    def test_duplicates_extras_and_invalid_asset_fields_are_rejected(self):
        duplicate = complete_release()
        duplicate["assets"][-1] = copy.deepcopy(duplicate["assets"][0])
        extra = complete_release()
        extra["assets"].append(copy.deepcopy(extra["assets"][0]))
        for release in (duplicate, extra):
            with self.assertRaises(builder.ReleaseNotReady):
                builder.select_release(release, POLICY)
        for field, value in (("size", 0), ("size", -1), ("size", True), ("size", "100"),
                             ("state", "new"), ("id", True), ("created_at", "2026-10-13"),
                             ("created_at", "9999-12-31T23:59:59-01:00")):
            release = complete_release()
            release["assets"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(builder.ReleaseNotReady):
                builder.select_release(release, POLICY)

    def test_asset_urls_are_exact_repository_version_and_filename(self):
        baseline = complete_release()["assets"][0]["browser_download_url"]
        variants = [baseline.replace("https:", "http:"),
                    baseline.replace("github.com", "github.com.evil.example"),
                    baseline.replace("github.com", "user@github.com"),
                    baseline.replace("github.com", "github.com:443"),
                    baseline.replace("griffinbeels/", "other/"),
                    baseline.replace("/v1.10.0/", "/v1.9.1/"),
                    baseline + "?token=private", baseline + "#fragment",
                    baseline.replace(".zip", "%2ezip")]
        for url in variants:
            release = complete_release()
            release["assets"][0]["browser_download_url"] = url
            with self.subTest(url=url), self.assertRaises(builder.ReleaseNotReady):
                builder.select_release(release, POLICY)

    def test_malformed_feed_identity_flags_and_timestamps_fail(self):
        for feed in (None, "release", [None], [True]):
            with self.subTest(feed=feed), self.assertRaises(builder.BuildError):
                builder.select_release(feed, POLICY)
        for field, value in (("draft", "false"), ("prerelease", None), ("id", True),
                             ("published_at", None), ("html_url", "https://evil.example/"),
                             ("body", 0), ("body", False), ("body", [])):
            release = complete_release()
            release[field] = value
            with self.subTest(field=field), self.assertRaises(builder.ReleaseNotReady):
                builder.select_release(release, POLICY)
        with self.assertRaises(builder.ReleaseNotReady):
            builder.select_release([complete_release(), complete_release()], POLICY)


class WebsiteBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "website"
        self.source.mkdir()
        (self.source / "assets").mkdir()
        anchors = '\n'.join('<a class="button download-link" href="https://github.com/griffinbeels/SM64-Trainer/releases/latest"><svg><path d="M1 1"/></svg><span class="download-label">Download for Windows</span><span>↗</span></a>' for _ in range(3))
        self.template = ('<!doctype html><html><head><link rel="canonical" href="https://example.invalid">'
                         '<meta property="og:url" content="https://example.invalid"></head><body>\n'
                         '<div class="preview-note">Preview <span>only</span></div>\n'
                         '<p id="release-meta">Checking…</p><noscript>Choose SM64Trainer.exe</noscript>\n'
                         + anchors + '\n<a href="https://github.com/griffinbeels/SM64-Trainer/releases/latest">Release notes</a>'
                         '<details id="faq"><summary>Question</summary><p>Answer &amp; unchanged.</p></details></body></html>')
        (self.source / "index.html").write_text(self.template, encoding="utf-8")
        (self.source / "styles.css").write_text("body{color:#fff}", encoding="utf-8")
        (self.source / "release.js").write_text("// FAQ deep links only", encoding="utf-8")
        (self.source / "_headers").write_text("/*\n  X-Content-Type-Options: nosniff\n", encoding="utf-8")
        (self.source / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        (self.source / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
        (self.source / "release-policy.json").write_text(json.dumps(POLICY), encoding="utf-8")
        (self.source / "README.md").write_text("PRIVATE NOTES", encoding="utf-8")
        (self.source / "assets" / "logo.png").write_bytes(b"public-image")
        (self.source / "assets" / ".private.png").write_bytes(b"PRIVATE NOTES")
        (self.source / "assets" / "private.json").write_text('{"internal":"PRIVATE NOTES"}', encoding="utf-8")
        self.fixture = self.root / "releases.json"
        self.output = self.root / "output"

    def run_cli(self, release, *extra):
        self.fixture.write_text(json.dumps(release), encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), mock.patch.object(builder, "fetch_releases") as network, mock.patch.object(builder.time, "sleep") as sleep:
            code = builder.main(["--source", str(self.source), "--output", str(self.output),
                                 "--releases-json", str(self.fixture), *extra])
        network.assert_not_called()
        sleep.assert_not_called()
        return code, json.loads(buffer.getvalue())

    def test_ready_build_prerenders_all_downloads_and_only_public_files(self):
        release = complete_release()
        release["body"] = 'Do not include this <script>alert("PRIVATE NOTES")</script>'
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-secret-never-print"}, clear=True):
            code, status = self.run_cli(release)
        self.assertEqual((code, status["status"]), (0, "ready"))
        rendered = (self.output / "index.html").read_text(encoding="utf-8")
        url = "https://github.com/griffinbeels/SM64-Trainer/releases/download/v1.10.0/SM64Trainer.exe"
        self.assertEqual(rendered.count(f'href="{url}"'), 3)
        self.assertIn("v1.10.0 · 12.0 MB installer · Uploaded ", rendered)
        self.assertIn('<time data-release-upload datetime="2026-10-15T16:30:00Z">'
                      'Oct 15, 2026 at 16:30 UTC</time>', rendered)
        self.assertIn("Answer &amp; unchanged.", rendered)
        self.assertIn('<link rel="canonical" href="https://sm64trainer.app">', rendered)
        self.assertNotIn("Checking", rendered)
        self.assertNotIn("preview-note", rendered)
        self.assertNotIn("<noscript", rendered)
        self.assertNotIn("/releases/latest", rendered)
        expected = {"index.html", "styles.css", "release.js", "_headers", "robots.txt",
                    "sitemap.xml", "release.json", "assets/logo.png"}
        actual = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(actual, expected)
        output_text = "".join(path.read_text(encoding="utf-8") for path in self.output.rglob("*")
                              if path.is_file() and path.suffix != ".png")
        self.assertNotIn("PRIVATE NOTES", output_text)
        self.assertNotIn("test-secret-never-print", output_text)
        metadata = json.loads((self.output / "release.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["installer_uploaded_at"], "2026-10-15T16:30:00Z")
        self.assertEqual(len(metadata["build"]["source_sha256"]), 64)

    def test_build_identity_uses_explicit_checkout_sha_only(self):
        for index, source_sha in enumerate((None, "b" * 40, "not-a-commit")):
            self.output = self.root / f"identity-{index}"
            environment = {"GITHUB_SHA": "a" * 40}
            if source_sha is not None:
                environment["WEBSITE_SOURCE_SHA"] = source_sha
            with mock.patch.dict(os.environ, environment, clear=True):
                code, _ = self.run_cli(complete_release())
            self.assertEqual(code, 0)
            metadata = json.loads((self.output / "release.json").read_text(encoding="utf-8"))
            if source_sha == "b" * 40:
                self.assertEqual(metadata["build"]["commit"], source_sha)
            else:
                self.assertNotIn("commit", metadata["build"])

    def test_launch_hold_and_invalid_new_release_leave_existing_output_unchanged(self):
        self.output.mkdir()
        sentinel = self.output / "index.html"
        sentinel.write_bytes(b"existing-site\0unchanged")
        self.assertEqual(self.run_cli(complete_release("v1.9.1"))[0], 3)
        incomplete = complete_release()
        incomplete["assets"].pop()
        self.assertEqual(self.run_cli(incomplete)[0], 2)
        self.assertEqual(sentinel.read_bytes(), b"existing-site\0unchanged")
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertFalse(list(self.root.glob(".website-build-*")))

    def test_explicit_unavailable_page_retires_direct_links_and_version_claims(self):
        code, status = self.run_cli([], "--allow-unavailable")
        self.assertEqual((code, status["status"]), (0, "unavailable"))
        rendered = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertEqual(rendered.count('class="download-label">View release history'), 3)
        for forbidden in ("SM64Trainer.exe", "/releases/download/", "v1.10.0", "Checking", "<noscript"):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("Downloads are temporarily unavailable", rendered)
        metadata = json.loads((self.output / "release.json").read_text(encoding="utf-8"))
        self.assertNotIn("version", metadata)
        self.assertNotIn("installer_url", metadata)
        self.assertNotIn("assets", metadata)

    def test_incomplete_release_can_emit_unavailable_only_when_explicit(self):
        incomplete = complete_release()
        incomplete["assets"].pop()
        self.assertEqual(self.run_cli(incomplete, "--allow-unavailable")[0], 0)
        self.assertEqual(json.loads((self.output / "release.json").read_text())["status"], "unavailable")

    def test_success_never_replaces_an_existing_directory(self):
        self.output.mkdir()
        sentinel = self.output / "index.html"
        sentinel.write_bytes(b"original")
        self.assertEqual(self.run_cli(complete_release())[0], 2)
        self.assertEqual(sentinel.read_bytes(), b"original")

    def test_missing_hooks_or_runtime_release_fetch_leave_no_output(self):
        for bad_template in (self.template.replace('id="release-meta"', 'id="other"'),
                             self.template.replace('class="download-label"', 'class="other"', 1)):
            (self.source / "index.html").write_text(bad_template, encoding="utf-8")
            self.assertEqual(self.run_cli(complete_release())[0], 2)
            self.assertFalse(self.output.exists())
        (self.source / "index.html").write_text(self.template, encoding="utf-8")
        (self.source / "release.js").write_text('fetch("https://api.github.com/releases")', encoding="utf-8")
        self.assertEqual(self.run_cli(complete_release())[0], 2)
        self.assertFalse(self.output.exists())

    def test_output_within_source_is_rejected(self):
        release = builder.select_release(complete_release(), POLICY)
        with self.assertRaises(builder.BuildError):
            builder.write_site(self.source, self.source / "generated", POLICY, release)
        self.assertFalse((self.source / "generated").exists())

    def test_mid_write_failure_cleans_only_owned_staging(self):
        original_write = Path.write_bytes

        def fail_second_file(path, data):
            if path.name == "styles.css" and path.parent.name.startswith(".website-build-"):
                raise OSError("simulated write failure")
            return original_write(path, data)

        with mock.patch.object(Path, "write_bytes", fail_second_file):
            code, status = self.run_cli(complete_release())
        self.assertEqual((code, status["status"]), (2, "invalid"))
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".website-build-*")))
        self.assertTrue((self.source / "styles.css").exists())

    def test_linked_assets_are_rejected(self):
        target = self.root / "outside.png"
        target.write_bytes(b"not-public")
        link = self.source / "assets" / "linked.png"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("This host does not permit creating test symlinks.")
        self.assertEqual(self.run_cli(complete_release())[0], 2)
        self.assertFalse(self.output.exists())

    def test_invalid_policy_fails_before_output(self):
        for key, value in (("repository", "other/repo"), ("minimum_version", "01.10.0"),
                           ("site_url", "https://evil.example/"), ("schema_version", True)):
            policy = {**POLICY, key: value}
            (self.source / "release-policy.json").write_text(json.dumps(policy), encoding="utf-8")
            with self.subTest(key=key):
                self.assertEqual(self.run_cli(complete_release())[0], 2)
                self.assertFalse(self.output.exists())


class FetchAndRetryTests(unittest.TestCase):
    def test_live_incomplete_assets_retry_and_reselect(self):
        incomplete = complete_release()
        incomplete["assets"].pop()
        with mock.patch.object(builder, "fetch_releases", side_effect=[[incomplete], [complete_release()]]) as fetch, mock.patch.object(builder.time, "sleep") as sleep:
            selected = builder.resolve_release(POLICY, None, 3, 10, False)
        self.assertEqual(selected["status"], "ready")
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(10)

    def test_transient_network_failure_is_bounded_and_not_unavailable(self):
        with mock.patch.object(builder, "fetch_releases", side_effect=builder.BuildError("Feed unavailable")) as fetch, mock.patch.object(builder.time, "sleep") as sleep:
            with self.assertRaises(builder.BuildError):
                builder.resolve_release(POLICY, None, 3, 10, True)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_http_error_details_and_auth_header_never_leave_fetch_boundary(self):
        opener = mock.Mock()
        opener.open.side_effect = OSError("transport error contains TEST-SECRET")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "TEST-SECRET"}, clear=True), mock.patch.object(builder.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(builder.BuildError) as caught:
                builder.fetch_releases(POLICY)
        self.assertNotIn("TEST-SECRET", str(caught.exception))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer TEST-SECRET")
        self.assertEqual(request.full_url, "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases?per_page=100&page=1")

    def test_fetch_pages_before_semver_selection(self):
        first = [complete_release("v1.10.0") for _ in range(100)]
        second = [complete_release("v1.12.0")]
        responses = []
        for page in (first, second):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(page).encode()
            responses.append(response)
        opener = mock.Mock()
        opener.open.side_effect = responses
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(builder.urllib.request, "build_opener", return_value=opener):
            feed = builder.fetch_releases(POLICY)
        self.assertEqual(len(feed), 101)
        self.assertEqual(opener.open.call_count, 2)

    def test_cli_rejects_unbounded_retry_options(self):
        for options in (["--attempts", "0"], ["--attempts", "6"], ["--retry-delay", "-1"],
                        ["--retry-delay", "31"], ["--retry-delay", "nan"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    builder.main(["--output", "unused", *options])
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
