"""Closing the app asks first only while replays are compressing, and every
doubt resolves to closing: the guard must never trap a window open."""
import threading

from sm64_events.desktop.closeguard import ASK_PAGE_JS, CloseGuard, compression_active


class Shell:
    def __init__(self, *, active=True, page_answers=True):
        self.active, self.page_answers = active, page_answers
        self.asked, self.quits = 0, 0
        self.settled = threading.Event()
        self.now = 100.0

    def guard(self):
        return CloseGuard(is_active=lambda: self.active, ask_page=self._ask,
                          quit_all=self._quit, clock=lambda: self.now)

    def _ask(self):
        self.asked += 1
        if self.page_answers is True:
            self.settled.set()
        if isinstance(self.page_answers, Exception):
            raise self.page_answers
        return self.page_answers

    def _quit(self):
        self.quits += 1
        self.settled.set()


def test_nothing_compressing_closes_without_asking():
    shell = Shell(active=False)
    assert shell.guard().closing() is True
    assert (shell.asked, shell.quits) == (0, 0)   # the window's own close runs the quit


def test_compressing_keeps_the_window_and_asks_the_page():
    shell = Shell()
    assert shell.guard().closing() is False
    assert shell.settled.wait(5) and (shell.asked, shell.quits) == (1, 0)


def test_a_page_that_cannot_show_the_warning_never_traps_the_window():
    for answer in (False, None, RuntimeError("page gone")):
        shell = Shell(page_answers=answer)
        assert shell.guard().closing() is False
        assert shell.settled.wait(5) and shell.quits == 1, answer


def test_closing_again_with_the_warning_up_means_it():
    shell = Shell()
    guard = shell.guard()
    assert guard.closing() is False
    shell.now += 3
    assert guard.closing() is True
    shell.now += 60            # much later, a fresh close asks again
    assert guard.closing() is False


def test_the_quit_itself_is_never_asked():
    shell = Shell()
    guard = shell.guard()
    guard.quitting = True
    assert guard.closing() is True and shell.asked == 0


def test_tray_quit_asks_the_same_question_and_otherwise_quits():
    busy = Shell()
    busy.guard().quit_requested()
    assert busy.settled.wait(5) and (busy.asked, busy.quits) == (1, 0)
    idle = Shell(active=False)
    idle.guard().quit_requested()
    assert (idle.asked, idle.quits) == (0, 1)


def test_a_server_that_does_not_answer_counts_as_idle():
    assert compression_active(9, timeout_s=0.2) is False   # nothing listens on port 9


def test_the_page_script_refuses_without_the_pages_flag():
    assert "__sm64CloseWarning" in ASK_PAGE_JS and "sm64-close-requested" in ASK_PAGE_JS
