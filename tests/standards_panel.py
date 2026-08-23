"""Driving the practice card's STANDARDS PANEL in a headless page -- the
snippets and the little HTTP helpers the panel's driven tests share
(test_ui_overall_owner_note / test_ui_sheet_best_row /
test_ui_standards_you_marker). One home, so a change to how the panel opens
(its toggle's selector, the settle it needs) is one edit rather than three
files that never call each other.
"""
import json
import urllib.request
from urllib.parse import quote

SETTLE = "new Promise(r => setTimeout(r, 2500))"
BEAT = "new Promise(r => setTimeout(r, 900))"

# Open the panel on the first card that has one; the card's entity key comes
# back so a test can ask the API about the SAME entity the screen is showing.
OPEN_PANEL = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.standards-toggle'));
    if (!card) return null;
    card.querySelector('.standards-toggle').click();
    return card.getAttribute('data-feed-key');
  })()
"""


def api(base, path, payload=None, method=None):
    """One JSON round-trip to the fixture server. `method` defaults to GET
    with no payload and POST with one."""
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base}{path}", data=data,
        method=method or ("POST" if payload is not None else "GET"),
        headers={"Content-Type": "application/json"})
    body = urllib.request.urlopen(request, timeout=10).read()
    return json.loads(body) if body else None


def standards_payload(base, entity):
    return api(base, f"/api/ranks/standards?entity={quote(entity)}")
