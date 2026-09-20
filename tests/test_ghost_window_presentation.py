"""Crop packets cross into native rendering without changing the whole window."""
import base64

import pytest

from sm64_events.ghost.window.geometry import Rect
from sm64_events.ghost.window.presentation import CropBridge, Scene, read_frame
from sm64_events.ghost.window.child import browser_visibility
from sm64_events.ghost.window.host import WebviewHost
from sm64_events.ghost.window.visibility import Visibility


def packet(bridge, sequence=1, **changes):
    value = {"session": bridge.session, "sequence": sequence, "mode": "mario",
             "visible": True, "x": 20, "y": 30, "width": 2, "height": 1,
             "viewport_width": 800, "viewport_height": 600,
             "pixels": base64.b64encode(bytes([255, 0, 0, 255, 0, 0, 0, 0])).decode()}
    return value | changes


class Surface:
    hwnd = 0

    def __init__(self):
        self.uploads = []
        self.visible = False
        self.accept = True

    def present(self, rect, rgba, width, height, alpha, insert_after=None):
        self.uploads.append((rect, rgba, width, height, alpha))
        return self.accept

    def show(self):
        self.visible = True
        return True

    def hide(self):
        self.visible = False


def scene(alpha=255, **changes):
    return Scene(**({"rect": Rect(x=100, y=200, width=800, height=600),
                     "alpha": alpha, "ready": True} | changes))


def test_one_packet_places_its_own_pixels_and_only_its_rectangle():
    bridge, surface = CropBridge(), Surface()
    bridge.configure(scene())
    assert bridge.present(packet(bridge))["accepted"]
    assert bridge.render_once(surface)
    rect, rgba, width, height, alpha = surface.uploads[-1]
    assert rect == Rect(x=120, y=230, width=2, height=1)
    assert (width, height, alpha) == (2, 1, 255)
    assert rgba == bytes([255, 0, 0, 255, 0, 0, 0, 0])
    bridge.configure(scene(alpha=128))
    assert bridge.render_once(surface)
    assert surface.uploads[-1][:-1] == surface.uploads[0][:-1]
    assert surface.uploads[-1][-1] == 128


def test_resize_acknowledges_exact_native_pixels_before_accepting_new_geometry():
    bridge, surface = CropBridge(), Surface()
    bridge.configure(scene(rect=Rect(x=40, y=40, width=961, height=721)))
    reply = bridge.present(packet(bridge, viewport_width=962, viewport_height=722))
    assert reply["viewport"] == {"width": 961, "height": 721}
    assert not bridge.render_once(surface)
    bridge.present(packet(bridge, sequence=2, viewport_width=961, viewport_height=721,
                          x=959, y=720))
    assert bridge.render_once(surface)
    assert surface.uploads[-1][0] == Rect(x=999, y=760, width=2, height=1)


@pytest.mark.parametrize("change", [
    {"visible": False, "pixels": None}, {"mode": "whole", "pixels": None},
])
def test_empty_picture_and_whole_view_remove_the_crop(change):
    bridge, surface = CropBridge(), Surface()
    bridge.configure(scene())
    bridge.present(packet(bridge))
    bridge.render_once(surface)
    assert surface.visible
    bridge.present(packet(bridge, sequence=2, **change))
    assert not bridge.render_once(surface)
    assert bridge.hidden and not surface.visible


@pytest.mark.parametrize("config", [scene(alpha=0), scene(ready=False), scene(rect=None),
                                    scene(rect=Rect(x=0, y=0, width=801, height=600))])
def test_hidden_unproved_and_resized_browser_cannot_show_a_stale_crop(config):
    bridge, surface = CropBridge(), Surface()
    bridge.present(packet(bridge))
    bridge.configure(config)
    assert not bridge.render_once(surface)
    assert not surface.uploads


def test_navigation_and_late_frames_cannot_resurrect_old_mario():
    bridge, surface = CropBridge(), Surface()
    old = packet(bridge)
    bridge.present(old)
    assert not bridge.present(old)["accepted"]
    bridge.reset()
    assert not bridge.present(old)["accepted"]
    bridge.configure(scene())
    assert not bridge.render_once(surface)


def test_browser_stays_transparent_until_the_current_page_has_whole_footage():
    bridge = CropBridge()
    want = Visibility(shown=True, alpha=255, rect=scene().rect, reason="playing")
    assert browser_visibility(want, bridge).alpha == 0
    bridge.present(packet(bridge, mode="whole", visible=False, pixels=None))
    assert browser_visibility(want, bridge).alpha == 0
    bridge.present(packet(bridge, sequence=2, mode="whole", pixels=None))
    assert browser_visibility(want, bridge).alpha == 255
    bridge.present(packet(bridge, sequence=3))
    assert browser_visibility(want, bridge).alpha == 0
    bridge.reset()
    assert browser_visibility(want, bridge).alpha == 0


@pytest.mark.parametrize("url,field", [
    ("http://localhost/ui/ghost/index.html?kind=star", "query"),
    ("file:///C:/rig/ghost_rig_page.html#present=always", "fragment"),
])
def test_page_session_preserves_each_hosts_supported_url_transport(url, field):
    from urllib.parse import parse_qs, urlsplit

    host = WebviewHost()
    result = urlsplit(host._page_url(url))
    settings = parse_qs(getattr(result, field))
    assert settings["native_session"] == [host.crop.session]
    assert settings["kind" if field == "query" else "present"] == [
        "star" if field == "query" else "always"]
    if field == "fragment":
        assert not result.query


@pytest.mark.parametrize("stale", ["page", "follower"])
def test_either_dead_producer_removes_the_crop(stale):
    now = [1.0]
    bridge, surface = CropBridge(clock=lambda: now[0]), Surface()
    bridge.present(packet(bridge))
    bridge.configure(scene())
    assert bridge.render_once(surface)
    now[0] += 1.1
    if stale == "page":
        bridge.configure(scene())
    else:
        bridge.present(packet(bridge, sequence=2))
    assert not bridge.render_once(surface)
    assert not surface.visible


def test_failed_upload_hides_the_previous_picture():
    bridge, surface = CropBridge(), Surface()
    bridge.present(packet(bridge))
    bridge.configure(scene())
    bridge.render_once(surface)
    surface.accept = False
    assert not bridge.render_once(surface)
    assert not surface.visible


def test_navigation_during_upload_cannot_show_the_retired_frame():
    bridge, surface = CropBridge(), Surface()
    bridge.present(packet(bridge))
    bridge.configure(scene())
    upload = surface.present

    def changing_page(*args, **kwargs):
        result = upload(*args, **kwargs)
        bridge.reset()
        return result

    surface.present = changing_page
    assert not bridge.render_once(surface)
    assert not surface.visible


@pytest.mark.parametrize("changes", [
    {"width": True}, {"x": -1}, {"x": 800}, {"width": 4097},
    {"pixels": "AAAA"}, {"pixels": "!" * 12}, {"visible": 1}, {"mode": "bad"},
])
def test_malformed_pixels_never_reach_a_native_allocation(changes):
    with pytest.raises(ValueError):
        read_frame(packet(CropBridge(), **changes))
