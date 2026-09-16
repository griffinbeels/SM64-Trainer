from pathlib import Path
import ctypes as C
import threading
import pytest

ROOT = Path(__file__).resolve().parents[1] / "src/sm64_events/data/plugin"
from sm64_events.replay import gpuencoder_abi as a
from sm64_events.replay.gpuencoder import EncoderClient, EncoderFailure, Result, _LIVE
from sm64_events.replay.gpuencoder_options import from_replay_config, pod_options


def options():
    return from_replay_config(
        width=320,
        height=240,
        nominal_fps_num=30,
        nominal_fps_den=1,
        b_frames=0,
        gop_frames=60,
        initial_qp_p=26,
        initial_qp_i=21,
        initial_qp_b=34,
        idr_interval_ticks=180000,
        input_format=1,
        signal_color=1,
        full_range=0,
        matrix=5,
        primaries=2,
        transfer=2,
        max_packet_bytes=1024,
    )


class Fn:
    def __init__(self, f):
        self.f = f

    def __call__(self, *args):
        return self.f(*args)


class Fake:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []
        self.sink = None
        self.count = 0
        self.closed = False
        self.pending = False
        self.rejected = False
        for name in a.SIGNATURES:
            setattr(
                self,
                "SM64GpuEncoder" + name + "V1",
                Fn(lambda *args, n=name: self.call(n, *args)),
            )

    def loader(self, *args, **kwargs):
        assert args == (str(ROOT / "SM64GpuEncoderV1.dll"),) and kwargs == {
            "winmode": 0x1100
        }
        return self

    def call(self, name, *args):
        self.calls.append(name)
        if name == "Abi":
            out = args[0]._obj
            out.pointer_bits = 64
            out.config_bytes = 688
            out.picture_bytes = 40
            out.status_bytes = 104
            out.options_bytes = 128
            out.packet_bytes = 48
            out.sink_bytes = 24
            if self.mode == "abi":
                out.packet_bytes += 8
        elif name == "RetainedAbi":
            args[0]._obj.query_bytes = 56
            args[0]._obj.repeat_bytes = 48
        elif name == "Open":
            return self._open(*args)
        elif name in ("Submit", "Repeat"):
            return self._submit(args[1]._obj)
        elif name == "Status":
            out = args[1]._obj
            out.state = (
                5
                if self.mode in ("quarantine", "open-quarantine", "unknown")
                else 2
                if self.rejected
                else 1
            )
            out.submitted = out.completed = self.count
            out.delivered = self.count - int(self.rejected)
            out.held_mask = int(self.pending)
            out.pending_mask = int(self.pending)
        elif name == "Retained":
            out = args[1]._obj
            out.valid = int(self.count > 0 and not self.rejected)
            out.generation = 1
            out.last_serial = self.count
        elif name == "Poll":
            if self.mode == "quarantine":
                return 13
            if self.mode == "abandoned":
                return 11
            self.pending = False
        elif name == "Finish":
            return 10 if self.pending else 0
        elif name == "Close":
            if self.mode in ("quarantine", "open-quarantine", "unknown"):
                return 13
            if self.pending:
                return 10
            args[1]._obj.state = 4
            self.closed = True
        return 0

    def _open(self, *args):
        self.sink = args[0]._obj.sink
        if self.mode == "open-throw":
            raise OSError("retained callback before token")
        if self.mode == "open-unknown":
            return 999
        if self.mode == "open-clean-refusal":
            return 6
        args[1]._obj.value = 17
        if self.mode == "open-quarantine":
            return 13
        return 0

    def _submit(self, p):
        if self.mode in ("timeout", "pending"):
            return 9 if self.mode == "timeout" else 10
        if self.mode == "unknown":
            return 999
        data = (C.c_uint8 * 8)(0, 0, 0, 1, 0x65, 0xAA, 0xBB, 0xCC)
        packet = a.Packet(48, 1, data, 8, 1, p.occurrence, p.pts, p.duration)
        if self.mode == "null":
            packet.data = C.POINTER(C.c_uint8)()
        if self.mode == "oversize":
            packet.bytes = 1025
        if self.mode == "version":
            packet.version = 99
        if self.mode == "duration":
            packet.duration += 1
        if self.mode == "serial":
            packet.occurrence += 1
        if self.mode == "pts":
            packet.pts += 1
        if self.mode == "flags":
            packet.flags = 2
        if self.mode == "no-packet":
            return 0
        self.count += 1
        code = self.sink.on_packet(None, C.pointer(packet))
        C.memset(data, 0xEE, 8)
        if self.mode == "duplicate":
            code = self.sink.on_packet(None, C.pointer(packet))
        self.rejected = bool(code)
        return 12 if code else 0


def make(fake=None, sink=None):
    fake = fake or Fake()
    packets = []
    client = EncoderClient.__new__(EncoderClient)
    client._initialize(
        dll_path=ROOT / "SM64GpuEncoderV1.dll",
        adapter_luid=(0, 84637),
        names=("fixture-A", "fixture-B"),
        options=options(),
        on_packet=sink or (lambda p: packets.append(p) or True),
        loader=fake.loader,
    )
    return client, fake, packets


def test_copy_metadata_repeat_and_close():
    c, f, p = make()
    assert c.submit(0, serial=1, pts=0, encoder_duration=3001) == Result.OK
    assert p[0].payload == bytes([0, 0, 0, 1, 0x65, 0xAA, 0xBB, 0xCC])
    assert (p[0].serial, p[0].pts, p[0].encoder_duration, p[0].keyframe) == (
        1,
        0,
        3001,
        True,
    )
    assert c.repeat(1, serial=2, pts=90000, encoder_duration=1) == Result.OK
    assert (
        c.close() == Result.OK
        and c.closed
        and c not in _LIVE
        and c.close() == Result.OK
    )


@pytest.mark.parametrize(
    "mode",
    [
        "null",
        "oversize",
        "version",
        "duration",
        "serial",
        "pts",
        "flags",
        "duplicate",
        "no-packet",
    ],
)
def test_bad_callback_fails_run(mode):
    c, f, p = make(Fake(mode))
    with pytest.raises(EncoderFailure):
        c.submit(0, serial=1, pts=0, encoder_duration=1)
    assert c.failed
    with pytest.raises(EncoderFailure):
        c.submit(0, serial=2, pts=1, encoder_duration=1)
    assert c.close() == Result.OK


@pytest.mark.parametrize("mode", ["timeout", "pending"])
def test_no_admission_can_retry_same_ticket(mode):
    c, f, p = make(Fake(mode))
    assert c.submit(0, serial=1, pts=0, encoder_duration=1) in (
        Result.TIMEOUT,
        Result.PENDING,
    )
    assert not c.failed and not p
    f.mode = "ok"
    assert c.submit(0, serial=1, pts=0, encoder_duration=1) == Result.OK
    c.close()


def test_pending_close_keeps_callback_alive_and_stops_admission():
    c, f, p = make()
    f.pending = True
    assert c.close() == Result.PENDING and c in _LIVE and c._token.value
    with pytest.raises(EncoderFailure):
        c.submit(0, serial=1, pts=0, encoder_duration=1)
    assert c.poll() == Result.OK and c.close() == Result.OK and c not in _LIVE


@pytest.mark.parametrize("mode", ["quarantine", "abandoned", "unknown"])
def test_uncertain_ownership_is_actionable_and_pinned(mode):
    c, f, p = make(Fake(mode))
    with pytest.raises(EncoderFailure) as err:
        if mode == "unknown":
            c.submit(0, serial=1, pts=0, encoder_duration=1)
        else:
            c.poll()
    assert err.value.worker_disposal_required and c in _LIVE and c.failed
    # Fake session only: erase test reference, never emulate native release success.
    _LIVE.discard(c)


def test_open_quarantine_pins_lifetime():
    before = set(_LIVE)
    with pytest.raises(EncoderFailure) as err:
        make(Fake("open-quarantine"))
    assert err.value.worker_disposal_required and len(_LIVE - before) == 1
    _LIVE.difference_update(_LIVE - before)


def test_owner_and_reentry():
    c, f, p = make()
    calls = len(f.calls)
    errors = []

    def foreign():
        try:
            c.status()
        except RuntimeError:
            errors.append(True)

    t = threading.Thread(target=foreign)
    t.start()
    t.join()
    assert errors == [True] and len(f.calls) == calls
    c._sink = lambda packet: c.status()
    with pytest.raises(EncoderFailure):
        c.submit(0, serial=1, pts=0, encoder_duration=1)
    assert c.failed
    c.close()


@pytest.mark.parametrize("refusal", [False, None, "exception", "unprintable"])
def test_callback_refusal_never_escapes_c(refusal):
    class Bad(BaseException):
        def __str__(self):
            raise RuntimeError("cannot print")

    def sink(p):
        if refusal == "exception":
            raise KeyboardInterrupt("fixture")
        if refusal == "unprintable":
            raise Bad()
        return refusal

    c, f, p = make(sink=sink)
    with pytest.raises(EncoderFailure):
        c.submit(0, serial=1, pts=0, encoder_duration=1)
    assert f.rejected and c.failed
    c.close()


def test_bad_abi_and_explicit_options(monkeypatch):
    with pytest.raises(ValueError):
        make(Fake("abi"))
    data = options()
    data.pop("cq")
    with pytest.raises(ValueError):
        pod_options(data)
    from sm64_events.replay import config

    monkeypatch.setattr(config, "VIDEO_CQ", 19)
    monkeypatch.setattr(config, "RING_MAXRATE", "17M")
    resolved = options()
    assert (
        resolved["cq"] == 19
        and resolved["max_bitrate"] == resolved["vbv_buffer_bits"] == 17000000
    )
    monkeypatch.setitem(config._NVENC_PRESET, "realtime", "p6")
    with pytest.raises(ValueError):
        options()


def test_metadata_and_path_validation_before_native():
    c, f, p = make()
    calls = len(f.calls)
    for args in [
        dict(serial=-1, pts=0, encoder_duration=1),
        dict(serial=1, pts=0, encoder_duration=0),
        dict(serial=1, pts=(1 << 64) - 1, encoder_duration=1),
    ]:
        with pytest.raises(ValueError):
            c.submit(0, **args)
    assert len(f.calls) == calls
    c.close()
    with pytest.raises(ValueError):
        EncoderClient(
            dll_path=Path("SM64GpuEncoderV1.dll"),
            adapter_luid=(0, 0),
            names=("A", "B"),
            options=options(),
            on_packet=lambda p: True,
        )


@pytest.mark.parametrize("mode", ["open-throw", "open-unknown"])
def test_uncertain_open_without_token_still_pins_callback(mode):
    before = set(_LIVE)
    fake = Fake(mode)
    with pytest.raises(EncoderFailure) as err:
        make(fake)
    added = _LIVE - before
    assert err.value.worker_disposal_required and len(added) == 1
    held = next(iter(added))
    assert held._token.value == 0 and held._callback and held._dll is fake
    _LIVE.difference_update(added)


def test_known_clean_open_refusal_does_not_pin():
    before = set(_LIVE)
    with pytest.raises(EncoderFailure) as err:
        make(Fake("open-clean-refusal"))
    assert not err.value.worker_disposal_required and _LIVE == before


@pytest.mark.parametrize("operation", ["submit", "poll"])
def test_uncertainty_refuses_all_teardown_even_if_native_would_close(operation):
    c, f, p = make(Fake("unknown" if operation == "submit" else "quarantine"))
    with pytest.raises(EncoderFailure):
        if operation == "submit":
            c.submit(0, serial=1, pts=0, encoder_duration=1)
        else:
            c.poll()
    f.mode = "ok"
    before = len(f.calls)
    for action in (c.close, c.finish, c.poll, c.retained):
        with pytest.raises(EncoderFailure) as err:
            action()
        assert err.value.worker_disposal_required
    assert len(f.calls) == before and c in _LIVE and not f.closed
    _LIVE.discard(c)
