"""Failure evidence crosses real client/worker/session boundaries, CPU only.

Native functions, DLL loading, channel/helper processes and all device boundaries
are inert. Wire replies still pass the existing controller's real validation.
"""
import ctypes as C
from collections import deque
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay import gpuencoder_abi as abi
from sm64_events.replay.gpuencoder import EncoderClient, EncoderFailure, Result, _LIVE
from sm64_events.replay.gpucapture_session import CaptureSession, _RETRY_OTHER_CODEC
from sm64_events.replay.gpudiagnostics import failure_snapshot
from sm64_events.replay.gpuprocess import process_worker
from sm64_events.replay.gpuprocess.process_controller import Controller, Entry
from sm64_events.replay.gpusettings import GpuSettings, forget_adapter_codecs
from sm64_events.replay.config import ReplayConfig
from tests.test_gpuencoder_client import Fake, options


@pytest.fixture(autouse=True)
def isolate_fake_owners():
    before = set(_LIVE)
    forget_adapter_codecs()
    yield
    # These are inert fake-DLL instances, never real native session retirement.
    _LIVE.difference_update(_LIVE - before)
    forget_adapter_codecs()


class FailedOpen(Fake):
    def __init__(self, result, *, token=False, query="ok"):
        super().__init__()
        self.result, self.token, self.query = result, token, query
    def loader(self, *args, **kwargs):
        assert kwargs == {"winmode": 0x1100}
        return self
    def _open(self, *args):
        self.sink = args[0]._obj.sink
        args[1]._obj.value = 17 if self.token else 0
        return self.result
    def call(self, name, *args):
        if name != "Status":
            return super().call(name, *args)
        self.calls.append(name)
        if self.query == "throws":
            raise OSError("inert status query refused")
        if self.query == "refuses":
            return Result.HANDLE
        out = args[1]._obj
        out.state, out.failure = 5, int(self.result)
        out.hresult, out.encoder_state = 0x887A0005, 5
        out.encoder_error, out.nv_status = 7, 10
        out.owner_thread, out.adapter_high, out.adapter_low = 123, 0, 42
        return Result.OK


def failed_client(tmp_path, fake):
    path = tmp_path / "SM64GpuEncoderV1.dll"
    path.write_bytes(b"MZ")  # existence check only; loader is the fake above
    client = EncoderClient.__new__(EncoderClient)
    client._initialize(dll_path=path.resolve(), adapter_luid=(0, 42), names=("a", "b"),
                       options=options(), on_packet=lambda _: True, loader=fake.loader)
    return client


@pytest.mark.parametrize("code", [Result.ADAPTER, Result.IMPORT, Result.ENCODER, Result.MEMORY])
def test_clean_failed_open_keeps_numeric_result_and_does_not_invent_status(tmp_path, code):
    fake = FailedOpen(code)
    before = set(_LIVE)
    with pytest.raises(EncoderFailure) as caught:
        failed_client(tmp_path, fake)
    failure = caught.value
    assert failure.result == code and str(failure) == "encoder open failed"
    assert failure.status is None and failure.status_error is None
    assert not failure.worker_disposal_required and _LIVE == before
    assert "Status" not in fake.calls, "token zero does not allow a native status query"


@pytest.mark.parametrize("query", ["throws", "refuses"])
def test_optional_status_failure_preserves_primary_result_and_uncertain_custody(tmp_path, query):
    fake = FailedOpen(Result.ENCODER, token=True, query=query)
    before = set(_LIVE)
    with pytest.raises(EncoderFailure) as caught:
        failed_client(tmp_path, fake)
    failure = caught.value
    assert failure.result == Result.ENCODER, "Status must not replace the original Open result"
    assert failure.status is None and failure.status_error
    assert str(failure).startswith("encoder open failed; native status unavailable:")
    assert failure.worker_disposal_required and len(_LIVE - before) == 1
    held = next(iter(_LIVE - before))
    assert held._token.value == 17 and held._uncertain and held._dll is fake
    assert fake.calls.count("Open") == 1 and fake.calls.count("Status") == 1
    assert not {"Close", "Finish"}.intersection(fake.calls)


def test_optional_status_failure_does_not_replace_existing_callback_error(tmp_path):
    fake = FailedOpen(Result.OK, token=True, query="throws")
    client = failed_client(tmp_path, fake)
    client.failure = "existing packet consumer refusal"
    with pytest.raises(EncoderFailure) as caught:
        client._raise(Result.ENCODER, "later generic message")
    assert str(caught.value).startswith("existing packet consumer refusal; native status unavailable:")
    assert caught.value.result == Result.ENCODER and caught.value.worker_disposal_required
    assert client in _LIVE and not {"Close", "Finish"}.intersection(fake.calls)


def worker_failure(tmp_path, monkeypatch, code, *, token=False, query="ok"):
    fake = FailedOpen(code, token=token, query=query)
    monkeypatch.setattr(process_worker, "EncoderClient", lambda **_: failed_client(tmp_path, fake))
    worker = process_worker.Worker(1024)
    command = dict(op="Open", dll_path=str(tmp_path / "SM64GpuEncoderV1.dll"),
                   adapter_luid=[0, 42], names=["a", "b"], options=options())
    metadata, payload = worker.command(command)
    metadata.update(nonce="diagnostic-fixture", request_id=1)
    # Actual serialization and existing custody/schema validator, no I/O thread.
    metadata = json.loads(json.dumps(metadata))
    controller = Controller.__new__(Controller)
    controller.nonce, controller._native_identity = "diagnostic-fixture", None
    controller._slot_owners = {}
    controller._validate(NS(command=command, request_id=1), metadata, payload)
    assert worker.client is None and controller._native_identity is None
    assert not controller._slot_owners and metadata["key_returns"] == [] and payload == b""
    assert metadata["retained"] is None
    return fake, command, metadata


class ReplyQueue:
    def __init__(self, metadata, *, race=False, wrong_id=False, missing=False):
        self.metadata, self.race = metadata, race
        self.wrong_id, self.missing = wrong_id, missing
        self.calls, self.opens = 0, []
    def enqueue(self, command):
        self.opens.append(command)
        return 1
    def take_result(self):
        self.calls += 1
        if self.missing or (self.race and self.calls == 1):
            return None
        return NS(request_id=2 if self.wrong_id else 1, metadata=self.metadata)
    def status(self):
        return {"fault": "encoder open failed"}


def attempt(queue):
    owner = NS(settings=GpuSettings(), cfg=ReplayConfig(), nominal_rate=30,
               want_capture=lambda: True)
    session = CaptureSession(owner, NS(snapshot=NS(lifecycle=None, state="running")))
    session.channel = NS(header=NS(luid_high=0, luid_low=42, format=1,
                                  even_width=320, even_height=240), texture_names=("a", "b"))
    session.controller = queue
    return session._attempt_open("C:/inert/SM64GpuEncoderV1.dll", session.channel.header,
                                 (0, 42), abi.CODEC_AV1, None)


@pytest.mark.parametrize("race", [False, True])
def test_failed_open_status_survives_worker_wire_validation_and_session_snapshot(tmp_path, monkeypatch, race):
    fake, command, metadata = worker_failure(tmp_path, monkeypatch, Result.QUARANTINE, token=True)
    assert metadata["result"] == 13 and metadata["status"]["nv_status"] == 10
    queue = ReplyQueue(metadata, race=race)
    with pytest.raises(EncoderFailure) as caught:
        attempt(queue)
    failure = caught.value
    assert failure.result == Result.QUARANTINE and failure.status == metadata["status"]
    assert failure.worker_disposal_required and failure.operation == "Open"
    assert "encoder result=13:QUARANTINE" in str(failure)
    assert len(queue.opens) == 1 and fake.calls.count("Open") == 1
    assert queue.calls == (2 if race else 1)
    snapshot = json.loads(json.dumps(failure_snapshot(failure, frontier=None, adapter=None, output=None)))
    diagnostic = snapshot["encoder_failure"]
    assert diagnostic["result"] == 13 and diagnostic["result_name"] == "QUARANTINE"
    assert diagnostic["result_namespace"] == "encoder_protocol"
    assert diagnostic["native_status_state"] == "available"
    assert diagnostic["native_status"]["hresult"] == 0x887A0005
    assert diagnostic["native_status"]["encoder_error"] == 7
    assert diagnostic["native_status"]["nv_status"] == 10
    assert diagnostic["worker_disposal_required"] and diagnostic["operation"] == "Open"
    assert not {"Close", "Finish"}.intersection(fake.calls)


def test_token_zero_failed_open_reports_known_category_but_unavailable_details(tmp_path, monkeypatch):
    _, _, metadata = worker_failure(tmp_path, monkeypatch, Result.IMPORT)
    with pytest.raises(EncoderFailure) as caught:
        attempt(ReplyQueue(metadata))
    assert caught.value.result == Result.IMPORT and caught.value.status is None
    snapshot = failure_snapshot(caught.value, frontier=None, adapter=None, output=None)["encoder_failure"]
    assert snapshot["result"] == 7 and snapshot["result_name"] == "IMPORT"
    assert snapshot["native_status_state"] == "unavailable" and snapshot["native_status"] is None
    assert not snapshot["worker_disposal_required"]


def test_clean_codec_refusal_still_allows_existing_fallback_not_an_encoder_fault(tmp_path, monkeypatch):
    fake, _, metadata = worker_failure(tmp_path, monkeypatch, Result.CODEC)
    assert metadata["error"] is None and not metadata["worker_disposal_required"]
    queue = ReplyQueue(metadata)
    assert attempt(queue) is _RETRY_OTHER_CODEC
    assert len(queue.opens) == 1 and fake.calls.count("Open") == 1


def test_terminal_fault_without_a_reply_is_explicitly_unknown():
    queue = ReplyQueue({}, missing=True)
    with pytest.raises(EncoderFailure) as caught:
        attempt(queue)
    snapshot = failure_snapshot(caught.value, frontier=None, adapter=None, output=None)["encoder_failure"]
    assert snapshot["result"] is None and snapshot["result_name"] is None
    assert snapshot["native_status"] is None and snapshot["native_status_state"] == "unavailable"
    assert "encoder result=unknown" in str(caught.value)
    assert len(queue.opens) == 1 and queue.calls == 2
    assert "encoder_failure" not in failure_snapshot(RuntimeError("encoder open failed"),
                                                       frontier=None, adapter=None, output=None)


def test_mismatched_reply_is_protocol_error_without_misattributed_native_failure():
    queue = ReplyQueue(dict(result=13, error="other Open's error", status={"nv_status": 99},
                           worker_disposal_required=True), wrong_id=True)
    with pytest.raises(RuntimeError, match="reply request mismatch") as caught:
        attempt(queue)
    assert not isinstance(caught.value, EncoderFailure)
    assert len(queue.opens) == 1


def test_synthetic_controller_deadline_is_protocol_result_not_native_return():
    # Real failure publication, but no process/controller threads are started.
    controller = Controller.__new__(Controller)
    controller._fault = controller._job = None
    controller.nonce = "inert-controller"
    controller._stop, controller._wake = threading.Event(), threading.Event()
    controller._lock = threading.Lock()
    controller.limits = NS(max_age=10)
    entry = Entry(1, {"op": "Open"}, b"", 0, time.monotonic())
    controller._pending, controller._ready = {1: entry}, deque()
    controller._work, controller._bytes = deque([1]), 0
    assert controller._fail_locked("native/pipe request deadline") is None
    reply = controller.take_result()
    assert reply.metadata["result"] == 13 and reply.metadata["status"] is None
    queue = ReplyQueue(reply.metadata)
    with pytest.raises(EncoderFailure) as caught:
        attempt(queue)
    assert "encoder result=13:QUARANTINE" in str(caught.value)
    assert "native result=" not in str(caught.value)
    diagnostic = failure_snapshot(caught.value, frontier=None, adapter=None, output=None)["encoder_failure"]
    assert diagnostic["result_namespace"] == "encoder_protocol"
    assert diagnostic["native_status_state"] == "unavailable"
    assert len(queue.opens) == 1 and controller._pending == {}


def test_generic_worker_open_exception_is_protocol_state_not_native_return(monkeypatch):
    def failed_constructor(**_):
        raise OSError("inert loader unavailable before native Open")
    monkeypatch.setattr(process_worker, "EncoderClient", failed_constructor)
    worker = process_worker.Worker(1024)
    metadata, payload = worker.command(dict(op="Open", dll_path="C:/inert/dll",
                                           adapter_luid=[0, 42], names=["a", "b"], options=options()))
    assert metadata["result"] == 14 and metadata["status"] is None and not payload
    assert worker.client is None
    queue = ReplyQueue(metadata)
    with pytest.raises(EncoderFailure) as caught:
        attempt(queue)
    assert "encoder result=14:STATE" in str(caught.value)
    assert "native result=" not in str(caught.value)
    diagnostic = failure_snapshot(caught.value, frontier=None, adapter=None, output=None)["encoder_failure"]
    assert diagnostic["result_namespace"] == "encoder_protocol"
    assert diagnostic["native_status"] is None and len(queue.opens) == 1
