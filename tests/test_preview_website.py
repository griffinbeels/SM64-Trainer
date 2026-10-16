"""Offline review contracts against the actual checked-in website source."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_website as builder
import preview_website as preview


class Links(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.downloads = []
        self.anchors = []
        self.ids = set()
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a":
            self.anchors.append(attrs)
        if tag == "a" and "download-link" in attrs.get("class", "").split():
            self.downloads.append(attrs)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "review"
        self.source = builder.ROOT / "website"

    def test_real_ready_and_unavailable_pages_are_inert_and_offline(self):
        for state in ("ready", "unavailable", "beta-only"):
            with self.subTest(state=state), mock.patch.object(
                    builder, "fetch_releases", side_effect=AssertionError("network discovery")):
                output = self.output / state
                metadata = preview.build_preview(self.source, output, state)
                page = (output / "index.html").read_text(encoding="utf-8")
                self.assertEqual(metadata["status"], "preview")
                self.assertEqual(metadata["preview"]["rendered_state"], state)
                self.assertTrue(metadata["preview"]["downloads_disabled"])
                self.assertIn("sample release data", page)
                self.assertIn('id="release-meta"', page)
                parsed = Links(page)
                links = parsed.downloads
                self.assertEqual(len(links), 3)
                self.assertTrue(all(link["href"].startswith("#")
                                    and link["href"][1:] in parsed.ids
                                    and link["aria-disabled"] == "true"
                                    and link["tabindex"] == "-1" for link in links))
                if state == "ready":
                    self.assertIn("data-release-upload", page)
                    self.assertIn("UTC", page)
                elif state == "unavailable":
                    self.assertIn("Downloads are temporarily unavailable", page)
                    self.assertNotIn("data-release-upload", page)
                else:
                    self.assertIn("community stable release is not available yet", page)
                    self.assertNotIn("data-release-upload", page)
                beta_page = (output / "beta/index.html").read_text(encoding="utf-8")
                self.assertIn('href="https://sm64trainer.app/beta/"', beta_page)
                self.assertIn("Try the Ghost<br>Overlay", beta_page)
                self.assertEqual(len(Links(beta_page).downloads), 3)
                self.assertTrue(all(link.get("aria-disabled") == "true" for link in Links(beta_page).downloads))
                self.assertEqual(json.loads((output / "beta/release.json").read_bytes())["status"], "preview")
                self.assertEqual((output / "assets/logo.png").read_bytes(),
                                 (self.source / "assets/logo.png").read_bytes())
                self.assertFalse((output / "README.md").exists())
                self.assertFalse((output / "AGENTS.md").exists())
                self.assertFalse((output / "release-policy.json").exists())
                with self.assertRaises(builder.LaunchHold):
                    builder.select_release(metadata, builder.load_policy(
                        self.source / "release-policy.json"))

    def test_loopback_preview_sends_actual_csp_and_revalidation_headers(self):
        preview.build_preview(self.source, self.output)
        with preview.make_server(self.output, port=0) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                for path in ("/", "/index.html", "/release.json", "/beta/", "/beta/index.html", "/beta/release.json"):
                    with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("connect-src 'none'",
                                      response.headers["Content-Security-Policy"])
                        self.assertEqual(response.headers["Cache-Control"],
                                         "public, max-age=0, must-revalidate, no-transform")
                        if path.endswith("/release.json"):
                            self.assertEqual(json.load(response)["status"], "preview")
            finally:
                server.shutdown()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

    def test_real_template_hook_damage_fails_before_output(self):
        damaged = Path(self.temp.name) / "damaged-source"
        shutil.copytree(self.source, damaged)
        page = (damaged / "index.html").read_bytes()
        self.assertEqual(page.count(b'id="release-meta"'), 1)
        (damaged / "index.html").write_bytes(
            page.replace(b'id="release-meta"', b'id="broken-release-meta"'))
        with self.assertRaises(builder.BuildError):
            preview.build_preview(damaged, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_output_is_not_overwritten(self):
        preview.build_preview(self.source, self.output)
        before = (self.output / "index.html").read_bytes()
        with self.assertRaises(builder.BuildError):
            preview.build_preview(self.source, self.output)
        self.assertEqual((self.output / "index.html").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
