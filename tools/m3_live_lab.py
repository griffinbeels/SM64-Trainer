"""User-run simulated LOOPBACK lab. Never launch against a live Trainer.

Run from this checkout: python tools/m3_live_lab.py --port 8094
Close other Trainer instances first, then open the printed localhost URL.
There is no EOS identity, matchmaking, account restoration or network peer.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import os
import secrets
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sm64_events.core.qpcclock import qpc_ns  # noqa: E402
from sm64_events.multiplayer.native_inbox import NativeInbox  # noqa: E402
from sm64_events.multiplayer.protocol import decode_pose, encode_pose  # noqa: E402
from sm64_events.multiplayer.room import RoomError  # noqa: E402
from sm64_events.multiplayer.source_handoff import SourceHandoff  # noqa: E402
from sm64_events.replay.ownedclose import CleanupPending, retain  # noqa: E402
from sm64_events.server.multiplayer_api import unavailable_state  # noqa: E402
try:
    from tools.m3_live_lab_queue import FaultQueue, Faults
except ModuleNotFoundError:
    from m3_live_lab_queue import FaultQueue, Faults


class LabHandoff(SourceHandoff):
    """Capture callbacks only mark a gap; the lab worker owns all queue IO."""
    def __init__(self, incarnation, wake):
        super().__init__(incarnation, stale_after_ns=250_000_000)
        self._gap_lock, self._gap, self._wake = threading.Lock(), 0, wake

    def gap_epoch(self):
        with self._gap_lock:
            return self._gap

    def _mark(self):
        with self._gap_lock:
            self._gap += 1
        self._wake.set()

    def begin_capture(self):
        owner = super().begin_capture(); self._mark(); return owner

    def source_unavailable(self, owner):
        changed = super().source_unavailable(owner)
        if changed:
            self._mark()
        return changed

    def end_capture(self, owner):
        changed = super().end_capture(owner)
        if changed:
            self._mark()
        return changed

    def withdraw_capture(self, owner):
        changed = super().withdraw_capture(owner)
        if changed:
            self._mark()
        return changed

    def revoke(self):
        super().revoke(); self._mark()


class LabService:
    """One lab worker and the recorder's existing native source lease."""
    def __init__(self, recorder, *, appearance=None, channel_factory=None,
                 clock_ns=qpc_ns, spawn_worker=True):
        self._recorder, self._appearance = recorder, appearance
        self._channel_factory, self._clock, self._spawn_worker = channel_factory, clock_ns, spawn_worker
        self._lock, self._wake = threading.RLock(), threading.Event()
        self._thread = self._channel = self._writer = self._endpoint = None
        self._retiring, self._revoke_view = False, None
        self._active = self._closed = False
        self._run_id, self._source, self._inbox, self._queue = 0, None, None, None
        # Fault decisions are repeatable; admission namespaces must stay fresh
        # when a lab process restarts while the native renderer remains open.
        self._namespace = secrets.token_bytes(16)
        self._settings, self._source_incarnation, self._peers = Faults(), None, []
        self._epoch, self._last_publication = None, None
        self._error = self._last_pump_ns = self._last_capture_ns = self._last_submit_ns = None
        self._delivered = self._rejected = self._native_submissions = 0

    def snapshot(self):
        value = unavailable_state()
        value["unavailable_reason"] = "Simulated LOOPBACK developer lab. Use /m3-lab; Friends and EOS are disabled."
        return value

    def _unsupported(self, *_args):
        raise RoomError("transport_unavailable")

    identity = host = invite = join = decline = leave = kick = _unsupported

    def _incarnation(self, run_id, seed, label):
        return hashlib.sha256(self._namespace + f"m3-lab:{run_id}:{seed}:{label}".encode()).digest()[:16]

    def start(self, settings: Faults):
        self.stop()
        with self._lock:
            if self._closed:
                raise RuntimeError("The lab is closed.")
            self._run_id += 1
            self._settings = settings
            self._source_incarnation = self._incarnation(self._run_id, settings.seed, "source")
            self._source = LabHandoff(self._source_incarnation, self._wake)
            self._inbox = NativeInbox(self._incarnation(self._run_id, settings.seed, "room"))
            self._peers = [self._incarnation(self._run_id, settings.seed, str(i)) for i in range(settings.seats)]
            for index, incarnation in enumerate(self._peers):
                self._inbox.admit(str(index), incarnation, name=f"LAB {index + 1}", hue=index * 120)
            self._epoch = self._run_id, self._source.gap_epoch()
            self._queue = FaultQueue(settings, self._epoch)
            self._error = self._last_pump_ns = self._last_capture_ns = self._last_submit_ns = self._last_publication = None
            self._delivered = self._rejected = self._native_submissions = 0
            self._active = True
            self._recorder.set_live_session(self._source, self._inbox)
            if self._spawn_worker:
                self._start_worker()
        return self.lab_snapshot()

    def _start_worker(self):
        self._thread = threading.Thread(target=self._run, name="m3-loopback-lab", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            self._withdraw()
            raise RuntimeError("The lab worker could not start.") from None

    def _withdraw(self):
        self._active = False
        if self._source is not None:
            self._source.revoke()
        if self._inbox is not None:
            self._inbox.revoke()
            self._revoke_view = self._inbox.snapshot(now_ns=self._clock())
        if self._queue is not None:
            self._queue.reset((self._run_id, -1))
        self._recorder.set_live_session(None, None)
        self._wake.set()

    def stop(self):
        with self._lock:
            if self._active:
                self._withdraw()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(3)
        with self._lock:
            if not self._retire_channel() or thread is not None and thread.is_alive():
                raise RuntimeError("Lab native cleanup is pending. Retry Stop before starting again.")
            self._source = self._inbox = self._endpoint = None
        return self.lab_snapshot()

    def close(self):
        self._closed = True
        self.stop()

    def _retire_channel(self):
        if self._channel is None:
            self._retiring = False; self._revoke_view = None; return True
        try:
            if self._revoke_view is not None and self._writer is not None:
                try:
                    self._writer.write(self._revoke_view)
                except ProcessLookupError:
                    # This exact local source capability was already retired.
                    self._revoke_view = None
                else:
                    self._revoke_view = None
            elif self._writer is None:
                self._revoke_view = None
            self._retiring = True
            self._channel.close()
        except CleanupPending as error:
            self._channel = retain(self._channel, error)
            self._retiring = True; self._error = "Native cleanup is pending."; return False
        except (OSError, RuntimeError):
            self._retiring = True; self._error = "Native cleanup is pending."; return False
        self._channel = self._writer = None
        self._retiring = False
        if self._error in {"Native cleanup is pending.", "Native submission is unavailable."}:
            self._error = None
        return True

    def _clear_gap(self):
        epoch = self._run_id, self._source.gap_epoch()
        if epoch != self._epoch:
            self._epoch = epoch
            self._queue.reset(epoch)
            for index in range(self._settings.seats):
                self._inbox.clear(str(index))
            self._last_publication = None
        return epoch

    def _enqueue(self, publication, now_ns):
        epoch = self._clear_gap()
        if not self._source.current(publication, now_ns=now_ns):
            return
        pose = decode_pose(publication.packet, expected_incarnation=self._source_incarnation)
        if (self._run_id, self._source.gap_epoch()) != epoch:
            self._clear_gap(); return
        for index, incarnation in enumerate(self._peers):
            packet = encode_pose(pose.frame.raw, participant_incarnation=incarnation,
                source_generation=pose.source_generation, elapsed_ns=pose.elapsed_ns, continuity=pose.continuity,
                rom_layout=pose.rom_layout, rom_generation=pose.rom_generation)
            self._queue.put(index, packet, now_ns=now_ns, source_received_ns=publication.received_ns, epoch=epoch)
        self._last_pump_ns, self._last_publication = publication.received_ns, publication
        self._last_capture_ns = self._capture_stamp(pose, publication, now_ns)
        self._clear_gap()  # A capture callback may have retired this epoch during encoding.

    @staticmethod
    def _capture_stamp(pose, publication, now_ns):
        frequency = publication.source_frequency
        qpc = pose.frame.qpc
        if type(frequency) is not int or not 0 < frequency < 1 << 63 or type(qpc) is not int or qpc < 0:
            return None
        stamp = qpc * 1_000_000_000 // frequency
        return stamp if 0 <= stamp <= now_ns else None

    def _deliver(self, now_ns):
        epoch = self._clear_gap()
        arrivals = self._queue.take_due(now_ns=now_ns, epoch=epoch)
        for arrival in arrivals:
            if (self._run_id, self._source.gap_epoch()) != epoch:
                self._clear_gap(); return
            try:
                self._inbox.ingest(str(arrival.peer), arrival.packet, received_ns=now_ns)
                self._delivered += 1
            except ValueError:
                self._rejected += 1  # Includes actual duplicate/reorder HWM refusals.
        self._clear_gap()

    def _flush_native(self, now_ns):
        if self._retiring and not self._retire_channel():
            return
        capture = self._recorder.ghost_capture
        endpoint = capture.native_live_endpoint(self._inbox) if capture is not None else None
        if endpoint != self._endpoint:
            if not self._retire_channel():
                return
            self._endpoint = endpoint
        if endpoint is None:
            return
        try:
            if self._channel is None:
                if self._channel_factory is None:
                    from sm64_events.ghost.live_channel import LiveChannel
                    self._channel_factory = LiveChannel
                self._channel = self._channel_factory(endpoint)
                self._writer = self._channel
            if self._appearance is not None:
                appearance, hidden = self._appearance()
                self._inbox.appearance(appearance, hidden=hidden)
            view = self._inbox.snapshot(now_ns=now_ns)
            if self._inbox.current(view):
                self._channel.write(view)
                self._native_submissions += 1; self._last_submit_ns = self._clock()
        except CleanupPending as error:
            self._channel = retain(self._channel, error) if self._channel is not None else error.owner
            self._retiring = True; self._error = "Native cleanup is pending."
        except (OSError, RuntimeError, ValueError):
            self._error = "Native submission is unavailable."
            self._retire_channel()

    def step(self):
        """One bounded worker turn; tests inject the source and native boundary."""
        with self._lock:
            if not self._active:
                self._retire_channel(); return
            now_ns = self._clock()
            self._clear_gap()
            source = self._recorder.live_session_status()
            if source.get("paused") or not source.get("source_attached"):
                self._clear_display()
            else:
                publication = self._source.take_latest(now_ns=now_ns)
                if publication is not None:
                    self._enqueue(publication, now_ns)
                elif self._last_publication is not None and not self._source.current(self._last_publication, now_ns=now_ns):
                    self._clear_display()
                self._deliver(now_ns)
            self._flush_native(now_ns)

    def _clear_display(self):
        self._queue.reset(self._epoch)
        for index in range(self._settings.seats):
            self._inbox.clear(str(index))
        self._last_publication = None

    def _run(self):
        while True:
            with self._lock:
                if not self._active and self._retire_channel():
                    return
            try:
                self.step()
            except Exception:  # noqa: BLE001 -- developer failure withdraws all lab demand
                with self._lock:
                    self._error = "Lab processing failed. Stop and inspect the developer test."
                    self._withdraw()
            self._wake.wait(.01); self._wake.clear()

    def lab_snapshot(self):
        with self._lock:
            now = self._clock()
            queue = self._queue.snapshot(now) if self._queue is not None else {"queued": 0, "counts": {}, "trace": [], "oldest_queued_age_ms": 0}
            age = lambda stamp: None if stamp is None else max(0, now - stamp) / 1_000_000
            return {"mode": "SIMULATED LOOPBACK", "active": self._active, "settings": asdict(self._settings),
                "cleanup_pending": self._retiring, "error": self._error, "queue": queue,
                "capture_age_ms": age(self._last_capture_ns), "pump_receipt_age_ms": age(self._last_pump_ns),
                "since_native_submission_ms": age(self._last_submit_ns),
                "inbox_accepted": self._delivered, "inbox_rejected": self._rejected,
                "native_submissions": self._native_submissions}


def mount_lab(app, service):
    from fastapi import APIRouter, HTTPException, Response
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, ConfigDict, Field

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        seats: int = Field(default=1, ge=1, le=3)
        delay_ms: int = Field(default=0, ge=0, le=1500)
        jitter_ms: int = Field(default=0, ge=0, le=500)
        loss_percent: int = Field(default=0, ge=0, le=100)
        reorder_percent: int = Field(default=0, ge=0, le=100)
        duplicate_percent: int = Field(default=0, ge=0, le=100)
        seed: int = Field(default=42, ge=0, le=(1 << 32) - 1)
        outages: list[bool] = Field(default_factory=lambda: [False, False, False], min_length=3, max_length=3)

    router = APIRouter(prefix="/api/m3-lab")

    @router.get("")
    def state(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.lab_snapshot()

    @router.post("/start")
    async def start(body: Settings, response: Response):
        response.headers["Cache-Control"] = "no-store"
        fields = body.model_dump(); fields["outages"] = tuple(fields["outages"])
        try:
            return await asyncio.to_thread(service.start, Faults(**fields))
        except RuntimeError:
            raise HTTPException(409, "Finish stopping the prior lab before starting again.", headers={"Cache-Control": "no-store"}) from None

    @router.post("/stop")
    async def stop(response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            return await asyncio.to_thread(service.stop)
        except RuntimeError:
            raise HTTPException(409, "Native cleanup is pending. Retry Stop.", headers={"Cache-Control": "no-store"}) from None

    @app.get("/m3-lab", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse((Path(__file__).with_suffix(".html")).read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    app.include_router(router)
    app.state.m3_lab = service


def build_lab_app(*, main_module=None, service_factory=LabService, local_port=None):
    if main_module is None:
        from sm64_events import main as main_module
    original_room, original_population, original_create = main_module._build_multiplayer, main_module._community_population, main_module.create_app
    held = []

    def room(replay, community_account=None, *, appearance=None):
        service = service_factory(replay.recorder, appearance=appearance)
        held.append(service); return service

    def offline_app(*args, **kwargs):
        kwargs["refresh_library_on_start"] = False
        if local_port is not None:
            kwargs["local_port"] = local_port
        return original_create(*args, **kwargs)

    main_module._build_multiplayer = room
    main_module._community_population = lambda tracker: (main_module._local_ghost_population(tracker), None, None)
    main_module.create_app = offline_app
    try:
        app = main_module.build()
    finally:
        main_module._build_multiplayer, main_module._community_population, main_module.create_app = original_room, original_population, original_create
    mount_lab(app, held[0])
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8094)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use a local port from 1024 through 65535.")
    os.environ["SM64_PORT"] = str(args.port)
    print("Close every other SM64 Trainer before running this developer lab.")
    print(f"SIMULATED LOOPBACK ONLY: http://127.0.0.1:{args.port}/m3-lab")
    print("Open that URL yourself. Start requests the recorder's existing single native source lease.")
    import uvicorn
    uvicorn.run(build_lab_app(local_port=args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
