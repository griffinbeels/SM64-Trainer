"""The overlay child: what it does with a control line, and how it ends.

The real child process is exercised here with `--backend none`, a host that
records what a window would have been told instead of making one, and with
`--fixed-rect`, which stands in for Project64 so the suite never queries the
emulator's window. Everything else -- argument parsing, the control loop, the
follow loop, the exit paths -- is the code that ships.
"""
import io
import json
import os
import subprocess
import sys
import threading

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.window import diag
from sm64_events.ghost.window.child import (
    PAGE_SAMPLE_EVERY,
    PAGE_STALE_S,
    ControlPlane,
    FollowLoop,
    OverlayState,
    PageWatch,
    build_parser,
    record_geometry,
    run_control_loop,
)
from sm64_events.ghost.window.geometry import (
    ChildWindow,
    GameWindow,
    Placement,
    Rect,
)
from sm64_events.ghost.window.host import (
    NOTHING,
    NO_WINDOW,
    RAISED,
    STATUS,
    NullHost,
    PageAnswer,
    WebviewHost,
)

GHOST_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=star&entity_key=bob-1"
OTHER_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=segment&entity_key=s1"
PICTURE = Rect(x=10, y=20, width=640, height=480)


def make_plane(url=GHOST_URL, opacity=0.5, visible=False):
    host = NullHost()
    events = []
    plane = ControlPlane(host=host,
                         state=OverlayState(url=url, opacity=opacity, visible=visible),
                         emit=events.append)
    return plane, host, events


def test_a_control_line_reaches_the_window_and_the_wanted_state():
    plane, host, _ = make_plane()
    keep_going = plane.apply_line(json.dumps(
        {"url": OTHER_URL, "opacity": 0.25, "visible": True}))
    assert keep_going is True
    assert plane.state.url == OTHER_URL
    assert plane.state.opacity == pytest.approx(0.25)
    assert plane.state.visible is True
    assert ("load_url", OTHER_URL) in host.calls


def test_the_control_pipe_never_touches_the_window_itself():
    """DPI awareness is per thread, so the thread that reads where Project64
    is has to be the thread that moves the overlay. A control line records
    what the player asked for and wakes the follower; loading a page is the
    one thing it does directly, because a URL carries no coordinates."""
    plane, host, _ = make_plane()
    plane.apply_line(json.dumps({"opacity": 0.25, "visible": True}))
    assert [call[0] for call in host.calls] == [], (
        "opacity and visibility belong to the follower thread")
    assert not hasattr(host, "place") and not hasattr(host, "set_alpha"), (
        "a host no longer has a way to move or fade a window at all")


def test_visibility_is_wanted_state_not_a_second_way_to_show_a_window():
    """One place shows and hides the overlay: the follow loop's decision. The
    control plane only records what the player asked for."""
    plane, host, _ = make_plane()
    plane.apply_line('{"visible": true}')
    assert plane.state.visible is True
    assert not [call for call in host.calls if call[0] in ("show", "hide")]


def test_quit_stops_the_loop():
    plane, _, _ = make_plane()
    assert plane.apply_line('{"quit": true}') is False


def test_a_line_we_cannot_read_is_reported_and_survived():
    plane, host, events = make_plane()
    assert plane.apply_line("}{ not json") is True
    assert any(event.get("event") == "rejected" for event in events)
    assert host.calls == []


def test_the_control_loop_runs_to_the_quit_line():
    plane, _host, _ = make_plane()
    stream = io.StringIO('{"opacity": 1.0}\n\n{"quit": true}\n{"opacity": 0.0}\n')
    assert run_control_loop(stream, plane) == "quit"
    assert plane.state.opacity == pytest.approx(1.0), (
        "nothing after the quit line is read")


def test_the_child_ends_when_its_parent_goes_away():
    """The trainer holds the write end of this pipe. If the trainer dies the
    pipe closes, the read returns nothing, and the overlay must go with it --
    that is what keeps a crash from leaving a window on the player's screen."""
    plane, _, _ = make_plane()
    assert run_control_loop(io.StringIO(""), plane) == "parent_gone"


def test_the_follow_loop_places_the_overlay_once_per_change():
    seen = []
    game = [GameWindow(found=True, minimized=False, client=PICTURE)]
    loop = FollowLoop(observe=lambda: game[0], apply=lambda placement: bool(seen.append(placement)) or True,
                      wanted=lambda: True)

    first = loop.tick()
    assert first == Placement(show=True, rect=PICTURE, reason="follow")
    assert loop.tick() is None, "a window that has not moved is not re-placed"
    assert len(seen) == 1

    moved = Rect(x=300, y=20, width=640, height=480)
    game[0] = GameWindow(found=True, minimized=False, client=moved)
    assert loop.tick().rect == moved
    assert len(seen) == 2


def test_the_follow_loop_hides_when_project64_is_minimized_and_returns_after():
    game = [GameWindow(found=True, minimized=False, client=PICTURE)]
    loop = FollowLoop(observe=lambda: game[0], apply=lambda _p: True,
                      wanted=lambda: True)
    loop.tick()
    game[0] = GameWindow(found=True, minimized=True, client=PICTURE)
    assert loop.tick().show is False
    game[0] = GameWindow(found=True, minimized=False, client=PICTURE)
    assert loop.tick().show is True


def test_the_follow_loop_obeys_the_wanted_switch():
    wanted = [False]
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: True, wanted=lambda: wanted[0])
    assert loop.tick().reason == "hidden_by_request"
    wanted[0] = True
    assert loop.tick().show is True


def test_the_follow_loop_places_exactly_what_it_read():
    """No factor, no correction, no guess. Reading PJ64's picture and placing
    the overlay happen on this one thread in one DPI context, so the rectangle
    needs nothing done to it -- the old `GetDpiForWindow` ratio was 1.5 at his
    display scaling whether or not anything was wrong, and that is what put a
    window half again as big on his screen."""
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: True, wanted=lambda: True)
    assert loop.tick().rect == PICTURE


def test_the_loop_prepares_the_window_every_tick():
    """A control line only records the opacity; this thread is what applies
    it. Preparing has to happen whether or not the placement changed, or an
    opacity change made while the window has not moved never lands -- and the
    window's styles would wait for the next decision, which is what left his
    second try unstyled for 4.1 seconds."""
    ticks = []
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: True, wanted=lambda: True,
        prepare=lambda _p: bool(ticks.append(1)) or True)
    loop.tick()
    loop.tick()
    assert len(ticks) == 2, "the second tick changed no placement but must prepare"


def test_a_decision_taken_before_the_window_was_ready_is_not_remembered():
    """The structural fault of his second try: the loop stored the placement
    and THEN tried to apply it, so a tick that found no window handle counted
    as done and nothing ran again until the decision changed."""
    applied = []
    window_ready = [False]
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda placement: bool(applied.append(placement)) or True,
        wanted=lambda: True, prepare=lambda _p: window_ready[0])
    assert loop.tick() is None
    assert loop.tick() is None
    assert applied == []

    window_ready[0] = True
    assert loop.tick().show is True, "the same decision is applied once ready"
    assert len(applied) == 1


def test_an_apply_that_did_not_happen_is_retried():
    done = [False]
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: done[0], wanted=lambda: True)
    assert loop.tick() is None
    done[0] = True
    assert loop.tick() is not None


def test_the_follow_loop_does_not_move_when_he_clicks_another_app():
    """Clicking away is not a decision change, so the loop does not even have
    anything to apply -- which is what "it should just stay always on" is,
    from in here."""
    away = GameWindow(found=True, minimized=False, client=PICTURE,
                      foreground=False)
    game = [away]
    loop = FollowLoop(observe=lambda: game[0], apply=lambda _p: True,
                      wanted=lambda: True)
    assert loop.tick().show is True
    game[0] = GameWindow(found=True, minimized=False, client=PICTURE)
    assert loop.tick() is None, "same placement, so there is nothing to redo"


def test_the_command_line_the_trainer_starts_the_child_with():
    args = build_parser().parse_args(
        ["--url", GHOST_URL, "--opacity", "0.35", "--visible",
         "--backend", "none", "--fixed-rect", "1,2,640,480"])
    assert args.url == GHOST_URL
    assert args.opacity == pytest.approx(0.35)
    assert args.visible is True
    assert args.backend == "none"
    assert args.fixed_rect == Rect(x=1, y=2, width=640, height=480)
    assert args.anchor_hwnd == 0, "no stand-in to sit above unless one is given"
    assert args.no_reorder_watch is False, "the prompt re-assert ships on"


# --- what the window actually looked like ------------------------------------

def test_every_number_the_picture_came_from_is_written_down():
    """Round 3's instrument. He reported the ghost "almost" overlaid and
    nothing on disk could say what rectangle it had been handed or what was
    inside Project64's client area when it was."""
    rows = []
    game = GameWindow(
        found=True, minimized=False, foreground=False,
        client=Rect(x=18, y=81, width=2400, height=1836),
        children=(ChildWindow(hwnd=9, cls="msctls_statusbar32",
                              rect=Rect(x=18, y=1883, width=2400, height=35),
                              visible=True),))
    record_geometry(lambda event, **fields: rows.append((event, fields)), game)

    (event, fields), = rows
    assert event == "game_geometry"
    assert fields["client"].as_list() == [18, 81, 2400, 1836]
    assert fields["picture"].as_list() == [18, 81, 2400, 1802]
    assert fields["why"] == "client_minus_msctls_statusbar32"
    assert fields["foreground"] is False, (
        "still read and still logged -- it just does not decide anything")
    child, = fields["children"]
    assert child["class"] == "msctls_statusbar32"
    assert child["rect"].as_list() == [18, 1883, 2400, 35]
    assert child["visible"] is True


def test_an_unchanged_window_costs_one_row_and_a_moved_one_costs_another():
    """Ten looks a second, so only a CHANGE may reach the file."""
    written = []
    collapsing = diag.Collapsing(
        record=lambda event, **fields: written.append(fields))
    first = GameWindow(found=True, minimized=False,
                       client=Rect(x=0, y=0, width=640, height=480))
    for _ in range(20):
        record_geometry(collapsing, first)
    assert len(written) == 1, "twenty identical looks, one row"
    assert written[0]["why"] == "whole_client"

    record_geometry(collapsing, GameWindow(
        found=True, minimized=False,
        client=Rect(x=0, y=0, width=800, height=600)))
    assert len(written) == 3
    assert written[1]["repeated"] == 19, (
        "the run that just ended is closed with its count, so the file says "
        "how long the window sat where it was")
    assert written[-1]["client"].as_list() == [0, 0, 800, 600]
    assert "repeated" not in written[-1]


def test_a_missing_emulator_is_recorded_rather_than_skipped():
    rows = []
    record_geometry(lambda event, **fields: rows.append(fields), None)
    assert rows and rows[0]["why"] == "no_client"
    assert rows[0]["picture"] is None


@pytest.mark.parametrize("bad", ["1,2,3", "a,b,c,d", "1,2,3,4,5", ""])
def test_a_malformed_fixed_rect_is_refused_at_the_command_line(bad):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--url", GHOST_URL, "--fixed-rect", bad])


def test_the_follow_loop_keeps_the_ghost_off_until_the_page_has_a_picture():
    """The ghost dims everything under it, so an empty window is a window
    making his game harder to see."""
    has_picture = [False]
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: True, wanted=lambda: True,
        presentable=lambda: has_picture[0])
    assert loop.tick().reason == "no_footage"
    has_picture[0] = True
    assert loop.tick().show is True


# --- asking the page --------------------------------------------------------

class StubPage:
    """A host whose page answers whatever a test wants, including nothing."""

    def __init__(self, answer=None):
        self.answer = answer if answer is not None else PageAnswer(state=NOTHING)
        self.asked = 0

    def page_status(self):
        self.asked += 1
        return self.answer


def a_status(**fields):
    return PageAnswer(state=STATUS, status=fields)


def a_watch(answer=None, clock=None):
    records = []
    watch = PageWatch(StubPage(answer), clock=clock or (lambda: 0.0),
                      record=lambda event, **fields: records.append(
                          {"event": event, **fields}))
    return watch, records


def test_a_page_with_a_picture_is_presentable():
    watch, records = a_watch(a_status(presentable=True, video_time=1.25))
    watch.poll()
    assert watch.presentable() is True
    said = next(entry for entry in records if entry["event"] == "page_poll")
    assert said["presentable"] is True and said["state"] == STATUS
    assert said["said"]["video_time"] == 1.25, "the page's own answer is kept"


@pytest.mark.parametrize("answer,why", [
    (PageAnswer(state=NO_WINDOW), "there is no window to ask yet"),
    (PageAnswer(state=RAISED, detail="WebViewException: timeout"),
     "evaluate_js raised"),
    (PageAnswer(state=NOTHING), "the page answered nothing"),
    (a_status(), "an older build of the page with no such field"),
    (a_status(presentable=False), "the page says it has nothing yet"),
    (a_status(presentable=None), "the page says it does not know"),
])
def test_anything_short_of_a_clear_yes_keeps_the_ghost_off(answer, why):
    """"I do not know yet" has to mean "stay off": the alternative is a dark
    sheet over his game while the page works out whether it has footage."""
    watch, _records = a_watch(answer)
    watch.poll()
    assert watch.presentable() is False, why


def test_every_kind_of_failure_is_told_apart_in_the_log():
    """His second try carried ONE page row, `said: null`, and "never asked",
    "raised every time" and "answered no" were indistinguishable in it.

    Driven through the REAL collapsing recorder, and through ONE watch, so
    this proves what the file will actually contain: four kinds in a row must
    survive the bound, not be folded into each other.
    """
    written = []
    fold = diag.Collapsing(record=lambda event, **fields: written.append(
        {"event": event, **fields}))
    page = StubPage(PageAnswer(state=NO_WINDOW))
    watch = PageWatch(page, clock=lambda: 0.0, record=fold)

    for answer in (PageAnswer(state=NO_WINDOW),
                   PageAnswer(state=RAISED, detail="WebViewException: timeout"),
                   PageAnswer(state=NOTHING),
                   a_status(presentable=False),
                   a_status(presentable=True)):
        page.answer = answer
        watch.poll()
    fold.flush()

    rows = [entry for entry in written if "repeated" not in entry]
    assert [entry["state"] for entry in rows] == [
        NO_WINDOW, RAISED, NOTHING, STATUS, STATUS], written
    assert [entry["presentable"] for entry in rows][-2:] == [False, True], (
        "two page answers that differ only in the flag are still two rows")
    raised = rows[1]
    assert raised["level"] == "error"
    assert "WebViewException" in raised["detail"], (
        "the exception text is kept; a swallowed one is why this was unreadable")


def test_a_page_that_stopped_answering_is_not_presentable():
    """pywebview's evaluate_js waits forever on a wedged page, so the answer
    simply stops arriving rather than turning into a no. A stale one is a no."""
    now = [100.0]
    watch, _records = a_watch(a_status(presentable=True), clock=lambda: now[0])
    watch.poll()
    assert watch.presentable() is True
    now[0] += PAGE_STALE_S / 2
    assert watch.presentable() is True
    now[0] += PAGE_STALE_S
    assert watch.presentable() is False, "a wedged page takes the ghost off"


def test_the_ghost_is_never_asked_before_the_page_has_answered_once():
    watch, _records = a_watch(a_status(presentable=True))
    assert watch.presentable() is False, "nothing has been asked yet"


def test_a_steady_page_still_leaves_periodic_proof_it_is_running():
    """Every poll is recorded, but the raw status carries a moving video time,
    so repeating it ten times a second would defeat the collapsing. It rides
    the first row of a run and one sample per PAGE_SAMPLE_EVERY after that."""
    watch, records = a_watch(a_status(presentable=True, video_time=2.0))
    for _ in range(PAGE_SAMPLE_EVERY + 1):
        watch.poll()
    polls = [entry for entry in records if entry["event"] == "page_poll"]
    assert len(polls) == PAGE_SAMPLE_EVERY + 1, "every poll is offered"
    with_status = [entry for entry in polls if entry["said"] is not None]
    assert len(with_status) == 2, "the first, and one periodic sample"


def test_a_page_that_throws_does_not_end_the_watch():
    """A dead watch is worse than a slow one: the last answer would go stale
    and the ghost would come off, but nothing would ever bring it back."""
    class Exploding:
        def page_status(self):
            raise RuntimeError("the webview is gone")

    records = []
    stop = threading.Event()

    def record(event, **_fields):
        records.append(event)
        stop.set()  # one round is enough to prove it survived

    watch = PageWatch(Exploding(), clock=lambda: 0.0, record=record)
    watch.run(stop, 0.01)
    assert records == ["page_status_failed"]
    assert watch.presentable() is False


# --- the page background ----------------------------------------------------

def test_the_background_handover_keeps_trying_until_it_works():
    """It was one-shot, and in his second try it fired a millisecond before
    the window handle existed, found no control and gave up for the session.
    pywebview's `transparent=True` is not the alternative: it shows and
    focuses the window on every navigation (edgechromium.py)."""
    host = WebviewHost()
    assert host.use_transparent_page_background() == "unreachable"
    assert host.use_transparent_page_background() == "unreachable", (
        "a window that did not exist yet is asked again, not given up on")


def test_the_null_backend_answers_both_page_questions():
    host = NullHost()
    answer = host.page_status()
    assert answer.state == STATUS and answer.presentable is True
    assert host.use_transparent_page_background() == "no_window"
    assert host.hosts_window is False, (
        "there is no window to make ready, so the gate must not wait for one")


# --- the real child process, with no window anywhere ------------------------

def read_event(proc, waiting_for):
    line = proc.stdout.readline()
    assert line, f"child said nothing while we waited for {waiting_for}"
    return json.loads(line)


def drain_until(proc, waiting_for, satisfied, limit=40):
    """Collect events until the ones we need have all arrived.

    Written against the accumulated list rather than event by event because
    the control thread and the follower both report independently: `applied`
    and `placed` can land in either order.
    """
    seen = []
    for _ in range(limit):
        seen.append(read_event(proc, waiting_for))
        if satisfied(seen):
            return seen
    raise AssertionError(f"never saw {waiting_for}; got {seen}")


@pytest.fixture
def child_log(tmp_path):
    return tmp_path / "data" / "ghost_window.jsonl"


@pytest.fixture
def child(child_log):
    proc = subprocess.Popen(
        [sys.executable, "-m", "sm64_events.ghost.window",
         "--url", GHOST_URL, "--backend", "none",
         "--fixed-rect", "10,20,640,480", "--follow-period", "0.02"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1,
        # The child is another process, so an env var is the only handle a
        # test has on where it writes its diagnostic log.
        env={**os.environ, diag.PATH_ENV: str(child_log)},
        **quiet_spawn_kwargs())
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None and not pipe.closed:
                pipe.close()


def send(proc, payload):
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def test_the_real_child_takes_control_lines_and_dies_with_its_parent(child):
    ready = read_event(child, "the ready event")
    assert ready["event"] == "ready"
    assert ready["backend"] == "none"

    send(child, {"opacity": 0.25, "visible": True})
    seen = drain_until(
        child, "the ghost landing on the picture",
        lambda events: (
            any(e["event"] == "applied" and e.get("opacity") == 0.25 for e in events)
            and any(e["event"] == "placed" and e["show"] for e in events)))
    placed = next(e for e in seen if e["event"] == "placed" and e["show"])
    assert placed["reason"] == "follow"
    assert placed["rect"] == [10, 20, 640, 480]

    send(child, {"visible": False})
    off = drain_until(
        child, "the overlay switching off",
        lambda events: any(e["event"] == "placed" and not e["show"] for e in events))
    hidden = next(e for e in off if e["event"] == "placed" and not e["show"])
    assert hidden["reason"] == "hidden_by_request"
    assert hidden["rect"] is None

    # Closing the pipe is what happens when the trainer dies: no quit line,
    # no signal, just the parent's handle going away.
    child.stdin.close()
    assert child.wait(timeout=20) == 0


def test_the_real_child_dies_when_the_trainer_takes_both_pipes_with_it(child):
    """A dead trainer closes BOTH ends at once, which the test above does not.

    Found 2026-09-19: two ghost windows were still over his game hours after
    the servers that started them had gone. The child read end-of-input, tried
    to say "stopping" down a pipe nobody held any more, and that write raised
    ahead of the line that stops the window.
    """
    assert read_event(child, "the ready event")["event"] == "ready"
    send(child, {"opacity": 0.25, "visible": True})
    drain_until(child, "the ghost landing on the picture",
                lambda events: any(e["event"] == "placed" and e["show"]
                                   for e in events))

    child.stdout.close()
    child.stdin.close()
    assert child.wait(timeout=20) == 0


def test_the_real_child_exits_on_a_quit_line(child):
    assert read_event(child, "the ready event")["event"] == "ready"
    send(child, {"quit": True})
    assert child.wait(timeout=20) == 0


def test_the_real_child_writes_a_log_a_failed_try_can_be_read_from(child,
                                                                   child_log):
    """The instrument lesson from his first try: a failure has to be readable
    off disk, not off two screenshots he had to take himself. This is the real
    child process writing the real file."""
    assert read_event(child, "the ready event")["event"] == "ready"
    send(child, {"opacity": 0.4, "visible": True})
    drain_until(child, "the ghost landing on the picture",
                lambda events: any(e["event"] == "placed" and e["show"]
                                   for e in events))
    send(child, {"quit": True})
    assert child.wait(timeout=20) == 0

    written = diag.read(child_log)
    by_event = {entry["event"]: entry for entry in written}
    assert "child_start" in by_event, written
    assert by_event["child_start"]["backend"] == "none"
    assert "process_pin_ok" in by_event["child_start"]

    context = by_event["dpi_context"]
    assert context["thread"] in {"unaware", "system", "per_monitor"}
    assert isinstance(context["thread_pin_ok"], bool)
    assert "process" in context, "the process context is recorded, not assumed"

    shown = [entry for entry in written
             if entry["event"] == "decision" and entry["show"]]
    assert shown and shown[-1]["rect"] == [10, 20, 640, 480]
    assert by_event["stopping"]["reason"] == "quit"
