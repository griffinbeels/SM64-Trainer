"""Keep first-use beta practice reachable when a Windows browser runtime is absent.

The browser displays the existing server UI. Recording, setup, account flow and
the tray keep their existing owners; no second UI or installer is downloaded.
"""
import importlib
import logging
import sys
import webbrowser

log = logging.getLogger("sm64.desktop")
_WEBVIEW_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _registry_value(hive, path, field):
    import winreg
    with winreg.OpenKey(getattr(winreg, hive), path) as key:
        return winreg.QueryValueEx(key, field)[0]


def native_engine_ready(*, read=_registry_value, importer=importlib.import_module):
    """Read Microsoft runtime presence before importing Python.NET/WinForms.

    Official detection uses both per-user and per-machine WebView2 `pv` keys.
    The packaged pywebview backend also requires .NET Framework 4.6.2+.
    """
    if sys.platform != "win32":
        return True
    try:
        release = read("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", "Release")
        if type(release) is not int or release < 394802:
            return False
    except (OSError, AttributeError):
        return False
    found = False
    for hive, path in (
            ("HKEY_CURRENT_USER", "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + _WEBVIEW_ID),
            ("HKEY_LOCAL_MACHINE", "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\" + _WEBVIEW_ID),
            ("HKEY_LOCAL_MACHINE", "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + _WEBVIEW_ID)):
        try:
            value = read(hive, path, "pv")
            found = isinstance(value, str) and any(part.isdigit() and int(part) > 0 for part in value.split("."))
        except (OSError, AttributeError):
            continue
        if found:
            break
    if not found:
        return False
    try:
        importer("webview.platforms.winforms")
    except (ImportError, OSError, RuntimeError):
        # This catch is confined to dependency initialization. Application,
        # recording and UI errors never acquire a misleading browser fallback.
        return False
    return True


def run_browser(*, url, tray, state, stopped, quit_all, error, opener=webbrowser.open):
    """The beta's tray owns Show/Quit while the same UI runs in a real browser."""
    def show():
        if not opener(url, new=2):
            error("Open this address in your browser to use SM64 Trainer Beta:\n" + url)

    state["tray"] = tray.create(on_show=show, on_quit=quit_all)
    tray.start(state["tray"])
    log.info("beta desktop runtime unavailable; using the shared UI in the default browser")
    show()
    stopped.wait()


def run_if_needed(**options):
    """Stable keeps its native desktop; beta can use the shared browser UI."""
    from sm64_events.core.build_channel import current_build
    if current_build().channel != "beta" or native_engine_ready():
        return False
    run_browser(**options)
    return True
