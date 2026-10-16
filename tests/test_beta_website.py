"""Public beta download contracts, independent of app dependencies or GitHub."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_build_website import builder, complete_release, POLICY, NAMES
from test_preview_website import Links


def beta_release(tag="v1.10.0-beta.10"):
    release = complete_release(tag)
    release["prerelease"] = True
    return release


class BetaWebsiteTests(unittest.TestCase):
    def test_numeric_prerelease_precedence_wins_over_dates_and_feed_order(self):
        chosen = builder.select_beta([beta_release("v1.10.0-beta.9"), beta_release(),
                                      beta_release("v1.10.0-beta.2")], POLICY)
        self.assertEqual(chosen["tag"], "v1.10.0-beta.10")
        self.assertEqual(set(chosen["assets"]), set(NAMES))
        self.assertEqual(len(chosen["checksums"]), 3)

    def test_beta_selection_rejects_wrong_channel_core_flags_and_tags(self):
        for tag in ("v1.9.1-beta.99", "v1.10.0-rc.1", "v1.10.0-beta.01",
                    "v01.10.0-beta.1", "1.10.0-beta.1", "V1.10.0-beta.1", "v1.10.0-beta+private", "v1.10.0"):
            with self.subTest(tag=tag), self.assertRaises(builder.LaunchHold):
                builder.select_beta(beta_release(tag), POLICY)
        for field, value in (("draft", True), ("prerelease", False)):
            release = beta_release()
            release[field] = value
            with self.assertRaises(builder.LaunchHold):
                builder.select_beta(release, POLICY)
        with self.assertRaises(builder.LaunchHold):
            builder.select_release(beta_release(), POLICY)

    def test_beta_requires_all_six_exact_versioned_public_assets(self):
        for name in NAMES:
            release = beta_release()
            release["assets"] = [asset for asset in release["assets"] if asset["name"] != name]
            with self.subTest(name=name), self.assertRaises(builder.ReleaseNotReady):
                builder.select_beta(release, POLICY)
        release = beta_release()
        release["assets"][-2]["browser_download_url"] += "?invitation=private"
        with self.assertRaises(builder.ReleaseNotReady):
            builder.select_beta(release, POLICY)
        newer = beta_release("v1.10.0-beta.11")
        newer["assets"][0]["state"] = "new"
        with self.assertRaises(builder.ReleaseNotReady):
            builder.select_channels([newer, beta_release(), complete_release()], POLICY)

    def test_withdrawn_beta_falls_back_and_last_withdrawal_removes_links(self):
        release = beta_release()
        release["body"] += builder.WITHDRAWN_MARKER
        self.assertEqual(builder.select_beta([release, beta_release("v1.10.0-beta.9")], POLICY)["tag"],
                         "v1.10.0-beta.9")
        stable, beta = builder.select_channels([release], POLICY, True)
        self.assertEqual((stable["status"], beta["status"]), ("unavailable", "unavailable"))
        self.assertNotIn("installer_url", beta)
        with self.assertRaises(builder.LaunchHold):
            builder.select_channels([release], POLICY)

    def test_shared_feed_retries_uploads_and_withdrawal_keeps_ready_stable(self):
        incomplete = beta_release()
        incomplete["assets"].pop()
        with mock.patch.object(builder, "fetch_releases", side_effect=[[incomplete], [beta_release()]]) as fetch, mock.patch.object(builder.time, "sleep") as sleep:
            stable, beta = builder.resolve_release(POLICY, None, 3, 10, False, channels=True)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(10)
        self.assertEqual((stable["status"], beta["status"]), ("beta_only", "ready"))
        with mock.patch.object(builder, "fetch_releases", return_value=[complete_release(), incomplete]), mock.patch.object(builder.time, "sleep"):
            stable, beta = builder.resolve_release(POLICY, None, 2, 0, True, channels=True)
        self.assertEqual((stable["status"], beta["status"]), ("ready", "unavailable"))
        with mock.patch.object(builder, "fetch_releases", return_value=[]) as fetch:
            stable, beta = builder.resolve_release(POLICY, None, 3, 10, True, channels=True)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual((stable["status"], beta["status"]), ("unavailable", "unavailable"))

    def test_beta_only_and_stable_plus_beta_production_pages_use_one_template(self):
        for with_stable in (False, True):
            with self.subTest(with_stable=with_stable), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                feed = [beta_release(), complete_release("v1.9.1")]
                if with_stable:
                    feed.append(complete_release())
                fixture = root / "releases.json"
                fixture.write_bytes(json.dumps(feed).encode())
                output = root / "site"
                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(builder, "fetch_releases") as fetch:
                    self.assertEqual(builder.main(["--output", str(output), "--releases-json", str(fixture)]), 0)
                fetch.assert_not_called()
                home = (output / "index.html").read_bytes().decode()
                beta = (output / "beta/index.html").read_bytes().decode()
                self.assertIn("Download beta for Windows", beta)
                self.assertIn("Anyone can join.", beta)
                self.assertIn("future published beta and production updates", beta)
                self.assertIn('href="https://sm64trainer.app/beta/"', beta)
                self.assertIn('content="https://sm64trainer.app/beta/"', beta)
                self.assertIn('href="/styles.css"', beta)
                self.assertIn('src="/release.js"', beta)
                self.assertIn('src="/assets/practice-gold.png"', beta)
                urls = [link["href"] for link in Links(beta).downloads]
                self.assertEqual(urls, ["https://github.com/griffinbeels/SM64-Trainer/releases/download/v1.10.0-beta.10/SM64Trainer.exe"] * 3)
                self.assertIn('href="/beta/"', home)
                self.assertNotIn("v1.9.1", home)
                if not with_stable:
                    self.assertIn("community stable release is not available yet", home)
                    self.assertEqual([link["href"] for link in Links(home).downloads], ["/beta/"] * 3)
                    self.assertNotIn("/releases/download/", home)
                else:
                    self.assertIn("/releases/download/v1.10.0/SM64Trainer.exe", home)
                metadata = json.loads((output / "beta/release.json").read_bytes())
                self.assertEqual(metadata["version"], "1.10.0-beta.10")
                self.assertEqual(metadata["channel"], "beta")

    def test_beta_footer_uses_validated_beta_release_or_history_when_unavailable(self):
        for available in (False, True):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as temporary:
                feed = [complete_release()]
                if available:
                    feed.append(beta_release())
                stable, beta = builder.select_channels(feed, POLICY)
                output = Path(temporary) / "site"
                builder.write_site(builder.ROOT / "website", output, POLICY, stable, beta)
                page = (output / "beta/index.html").read_bytes().decode()
                footer = Links(page[page.index("<footer"):])
                links = [link for link in footer.anchors
                         if link.get("href", "").startswith("https://github.com/griffinbeels/SM64-Trainer/releases")]
                expected = "https://github.com/griffinbeels/SM64-Trainer/releases"
                if available:
                    expected += "/tag/v1.10.0-beta.10"
                self.assertEqual([link["href"] for link in links], [expected])
                self.assertNotIn("/releases/latest", page)

    def test_beta_ci_withdrawal_event_enables_explicit_refresh(self):
        workflow = (builder.ROOT / ".github/workflows/website.yml").read_text()
        self.assertIn("types: [published, released, edited, deleted, unpublished]", workflow)
        start = workflow.index("          import json", workflow.index("Determine whether"))
        end = workflow.index("          PY", start)
        script = "\n".join(line[10:] for line in workflow[start:end].splitlines())
        for action in ("deleted", "unpublished", "edited", "published"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temporary:
                event = Path(temporary) / "event.json"
                output = Path(temporary) / "output"
                release = beta_release()
                if action == "edited":
                    release["body"] += builder.WITHDRAWN_MARKER
                event.write_bytes(json.dumps({"action": action, "release": release}).encode())
                with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
                                                  "GITHUB_EVENT_NAME": "release", "MANUAL_RETIRE_DOWNLOAD": "false"}):
                    exec(compile(script, "website-policy", "exec"), {})
                self.assertEqual(output.read_text().strip(), "allow_unavailable=" + ("false" if action == "published" else "true"))


if __name__ == "__main__":
    unittest.main()
