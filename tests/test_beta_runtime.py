"""Missing desktop dependencies use the shared browser UI without real windows."""
import threading
from types import SimpleNamespace

import pytest

from sm64_events.desktop import beta_runtime


def read_registry(*, webview="123.0.0.0", net=528040):
    def read(_hive, _path, field):
        if field == "Release":
            return net
        if webview is None:
            raise FileNotFoundError()
        return webview
    return read


@pytest.mark.parametrize("webview,net", [(None, 528040), ("0.0.0.0", 528040), ("", 528040),
                                        ("123.0.0.0", 0)])
def test_missing_webview_or_net_does_not_import_native_engine(monkeypatch, webview, net):
    monkeypatch.setattr(beta_runtime.sys, "platform", "win32")
    assert not beta_runtime.native_engine_ready(read=read_registry(webview=webview, net=net),
        importer=lambda _module: pytest.fail("native dependency imported despite missing prerequisite"))


def test_existing_runtime_checks_native_dependency_and_specific_failures(monkeypatch):
    monkeypatch.setattr(beta_runtime.sys, "platform", "win32")
    calls = []
    assert beta_runtime.native_engine_ready(read=read_registry(), importer=lambda name: calls.append(name))
    assert calls == ["webview.platforms.winforms"]
    for failure in (ImportError("missing"), RuntimeError("could not load .NET"), OSError("missing DLL")):
        def fail(_name, failure=failure):
            raise failure
        assert not beta_runtime.native_engine_ready(read=read_registry(), importer=fail)
    with pytest.raises(ValueError, match="application"):
        beta_runtime.native_engine_ready(read=read_registry(), importer=lambda _name: (_ for _ in ()).throw(ValueError("application")))


def test_per_machine_registry_can_follow_missing_per_user_key(monkeypatch):
    monkeypatch.setattr(beta_runtime.sys, "platform", "win32")
    calls = []
    def read(hive, path, field):
        calls.append((hive, path, field))
        if field == "Release":
            return 528040
        if hive == "HKEY_CURRENT_USER":
            raise FileNotFoundError()
        return "123.0.0.0"
    assert beta_runtime.native_engine_ready(read=read, importer=lambda _name: None)
    assert any("WOW6432Node" in path for _, path, _ in calls)


def test_browser_uses_same_url_and_tray_owns_show_and_quit_without_native_windows():
    stopped = threading.Event()
    state, callbacks, opened = {}, {}, []
    tray = SimpleNamespace(create=lambda **kwargs: callbacks.update(kwargs) or object(),
                           start=lambda _icon: callbacks["on_quit"]())
    beta_runtime.run_browser(url="http://127.0.0.1/test", tray=tray, state=state, stopped=stopped,
        quit_all=stopped.set, error=lambda _message: pytest.fail("browser failed"),
        opener=lambda url, **_kwargs: opened.append(url) or True)
    callbacks["on_show"]()
    assert opened == ["http://127.0.0.1/test", "http://127.0.0.1/test"] and state["tray"] is not None


def test_failed_browser_open_gives_reachable_address():
    stopped = threading.Event()
    stopped.set()
    messages = []
    tray = SimpleNamespace(create=lambda **_kwargs: object(), start=lambda _icon: None)
    beta_runtime.run_browser(url="http://127.0.0.1/test", tray=tray, state={}, stopped=stopped,
        quit_all=lambda: None, error=messages.append, opener=lambda *args, **kwargs: False)
    assert "http://127.0.0.1/test" in messages[0]


@pytest.mark.parametrize("channel,ready,expected", [("stable", False, False),
                                                  ("beta", True, False), ("beta", False, True)])
def test_fallback_door_only_runs_for_beta_with_missing_engine(monkeypatch, channel, ready, expected):
    from sm64_events.core import build_channel
    monkeypatch.setattr(build_channel, "current_build", lambda: SimpleNamespace(channel=channel))
    called = []
    monkeypatch.setattr(beta_runtime, "native_engine_ready", lambda: ready)
    monkeypatch.setattr(beta_runtime, "run_browser", lambda **kwargs: called.append(kwargs))
    assert beta_runtime.run_if_needed(url="http://127.0.0.1/test") is expected
    assert called == ([{"url": "http://127.0.0.1/test"}] if expected else [])
