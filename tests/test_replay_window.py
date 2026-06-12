from sm64_events.replay.window import WindowInfo, pick_window


def w(hwnd, title, pid=42, visible=True):
    return WindowInfo(hwnd=hwnd, title=title, pid=pid, visible=visible)


def test_picks_first_visible_title_match_case_insensitive():
    wins = [w(1, "Notepad"), w(2, "project64 Version 1.6", pid=7),
            w(3, "Project64 Version 1.6", pid=8)]
    got = pick_window(wins, "Project64")
    assert got is not None and got.hwnd == 2 and got.pid == 7


def test_skips_invisible_and_empty_titles():
    wins = [w(1, "Project64", visible=False), w(2, "")]
    assert pick_window(wins, "Project64") is None


def test_no_match_returns_none():
    assert pick_window([w(1, "Notepad")], "Project64") is None
