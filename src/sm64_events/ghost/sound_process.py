"""Own the ghost sound child and bounded diagnostics without polling its pipe."""
from collections import deque
import json
import logging
import os
import subprocess
import sys
import threading
import time

SOUND_PLAYER_FLAG = "--ghost-sound"
MODULE = "sm64_events.ghost.sound_player"
log = logging.getLogger("sm64.ghost.sound")
DIAGNOSTIC_PREFIX = "ghost_sound:"
MAX_DIAGNOSTIC_BYTES = 8192


class StageNotes:
    """The child reports each startup/failure stage once; no per-tick logs."""

    def __init__(self, stream=None, clock=time.monotonic):
        self.stream = sys.stderr if stream is None else stream
        self.clock, self.started = clock, clock()
        self._seen = set()

    def report(self, stage, **details):
        if stage in self._seen or len(self._seen) >= 16:
            return
        self._seen.add(stage)
        details = {key: value[:2048] if isinstance(value, str) else value
                   for key, value in details.items()}
        document = dict(stage=stage, elapsed_ms=round((self.clock() - self.started) * 1000, 1),
                        pid=os.getpid(), **details)
        print(DIAGNOSTIC_PREFIX + json.dumps(document, ensure_ascii=True), file=self.stream, flush=True)


class _Diagnostics:
    """Drain sparse child stderr, keep an 8 KiB tail, forward at most 16 stages.

    The reader blocks on the pipe between messages; it never polls or feeds
    the recorder. Unexpected stderr stays available when the child exits.
    """

    def __init__(self, stream, pid):
        self.stream, self.pid = stream, pid
        self._tail, self._seen = deque(maxlen=2), set()
        self._reader = threading.Thread(target=self._read, name="ghost-sound-diagnostics", daemon=True)
        self._reader.start()

    def _read(self):
        try:
            while line := self.stream.readline(4096):
                self._tail.append(line[-4096:])
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith(DIAGNOSTIC_PREFIX):
                    continue
                try:
                    stage = json.loads(text[len(DIAGNOSTIC_PREFIX):]).get("stage")
                except (ValueError, AttributeError):
                    continue
                if isinstance(stage, str) and stage not in self._seen and len(self._seen) < 16:
                    self._seen.add(stage)
                    log.info("ghost sound child %s: %s", self.pid, text[:4096])
        except (OSError, ValueError):
            pass  # process retirement may close the pipe

    def finish(self):
        self._reader.join(timeout=2)
        if not self._reader.is_alive():
            self.stream.close()
        return b"".join(tuple(self._tail))[-MAX_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace")


def child_command(pj64_pid: int):
    """Run this checkout's player directly, preserving frozen-entry dispatch."""
    from sm64_events.core.childproc import python_child
    return python_child(MODULE, SOUND_PLAYER_FLAG,
                        ("--pj64", str(pj64_pid), "--parent", str(os.getpid())), site_packages=True)


class SoundPlayerProcess:
    """One child per Project64, preserving its existing five-second retry bound."""

    RESPAWN_S = 5.0

    def __init__(self, spawn=subprocess.Popen, clock=time.monotonic):
        self._spawn, self._clock, self._child, self.pj64_pid = spawn, clock, None, None
        self._started_at = None
        self._diagnostics = None
        self._attempts = 0

    @property
    def pid(self) -> int | None:
        child = self._child
        return child.pid if child is not None and child.poll() is None else None

    def start(self, pj64_pid: int) -> None:
        if self.pid is not None and self.pj64_pid == pj64_pid:
            return
        if (self.pj64_pid == pj64_pid and self._started_at is not None
                and self._clock() - self._started_at < self.RESPAWN_S):
            return
        if self._child is not None and self._child.poll() is not None:
            details = self._finish_diagnostics()
            log.warning("ghost sound player for Project64 %s ended (exit %s); starting another "
                        "after at least %.1fs; diagnostics=%s", self.pj64_pid, self._child.returncode,
                        self.RESPAWN_S, details or "unavailable")
        self.stop()
        self._started_at = self._clock()
        self._attempts += 1
        self.pj64_pid = pj64_pid
        from sm64_events.core.childproc import quiet_spawn_kwargs
        command, env = child_command(pj64_pid)
        try:
            self._child = self._spawn(command, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      **quiet_spawn_kwargs())
        except Exception as error:
            log.warning("ghost sound spawn failed for Project64 %s attempt=%s: %s: %s",
                        pj64_pid, self._attempts, type(error).__name__, str(error)[:2048])
            raise
        stream = getattr(self._child, "stderr", None)
        if stream is not None:
            self._diagnostics = _Diagnostics(stream, self._child.pid)
        log.info("ghost sound player spawned: pj64=%s player=%s attempt=%s spawn_ms=%.1f",
                 pj64_pid, self._child.pid, self._attempts, (self._clock() - self._started_at) * 1000)

    def _finish_diagnostics(self):
        diagnostics, self._diagnostics = self._diagnostics, None
        return diagnostics.finish() if diagnostics is not None else ""

    def stop(self, timeout_s: float = 2.0) -> None:
        child, self._child, self.pj64_pid = self._child, None, None
        try:
            if child is None or child.poll() is not None:
                return
            child.terminate()
            try:
                child.wait(timeout_s)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout_s)
        finally:
            self._finish_diagnostics()
