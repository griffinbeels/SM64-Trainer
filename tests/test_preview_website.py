"""Offline review contracts against the actual checked-in website source."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
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
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "download-link" in attrs.get("class", "").split():
            self.downloads.append(attrs)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "review"
        self.source = builder.ROOT / "website"

    def test_real_ready_and_unavailable_pages_are_inert_and_offline(self):
        for state in ("ready", "unavailable"):
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
                links = Links(page).downloads
                self.assertEqual(len(links), 3)
                self.assertTrue(all(link["href"] == preview.DISABLED_URL
                                    and link["aria-disabled"] == "true"
                                    and link["tabindex"] == "-1" for link in links))
                if state == "ready":
                    self.assertIn("data-release-upload", page)
                    self.assertIn("UTC", page)
                else:
                    self.assertIn("Downloads are temporarily unavailable", page)
                    self.assertNotIn("data-release-upload", page)
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
                for path in ("/", "/index.html", "/release.json"):
                    with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("connect-src 'none'",
                                      response.headers["Content-Security-Policy"])
                        self.assertEqual(response.headers["Cache-Control"],
                                         "public, max-age=0, must-revalidate")
                        if path == "/release.json":
                            self.assertEqual(json.load(response)["status"], "preview")
            finally:
                server.shutdown()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

    def test_existing_output_is_not_overwritten(self):
        preview.build_preview(self.source, self.output)
        before = (self.output / "index.html").read_bytes()
        with self.assertRaises(builder.BuildError):
            preview.build_preview(self.source, self.output)
        self.assertEqual((self.output / "index.html").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
