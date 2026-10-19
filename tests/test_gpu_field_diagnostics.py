"""Field evidence crosses failure, cleanup, fallback, API export and privacy.

All device/encoder boundaries are inert. No graphics, processes or Sentry I/O.
"""
import json
import struct
import ctypes
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from sm64_events.core.error_reporting_privacy import safe_event
from sm64_events.replay import gpufielddiagnostics as fields
from sm64_events.replay import capturecontrol as control
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpudemand import DemandSnapshot
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.gpuretry import RetryGate
from sm64_events.replay.gpuadapter import AdapterFacts
from sm64_events.replay.gpuencoder_abi import CODEC_H264
from sm64_events.replay.sourcefactory import DesktopForUnsupportedGpu
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


def startup_raw(**overrides):
    values = dict(magic=b"SM64GPU1", version=1, size=fields.STARTUP.size, epoch=7, error=5,
                  width=640, height=480, available=1, high=0xFFFFFFFF, low=42, generation=3,
                  nonce_lo=5, nonce_hi=6, vendor=b"ATI Technologies Inc.", renderer=b"AMD Radeon",
                  gl_version=b"4.6 AMD driver", missing=b"glAcquireKeyedMutexWin32EXT")
    values.update(overrides)
    return fields.STARTUP.pack(*values.values())


def test_optional_startup_receipt_preserves_identity_and_rejects_unknown_versions():
    receipt = fields.startup_receipt(startup_raw())
    assert receipt["adapter_luid"] == [-1, 42]
    assert receipt["nonce"] == struct.pack("<QQ", 5, 6).hex()
    assert receipt["missing_entry_point"] == "glAcquireKeyedMutexWin32EXT"
    for raw in (b"", bytes(fields.STARTUP.size), startup_raw(version=2), startup_raw(size=0), startup_raw(available=2)):
        assert fields.startup_receipt(raw) is None
    assert fields.startup_receipt(startup_raw(available=0))["adapter_luid"] is None


def test_control_reader_copies_optional_receipt_inside_the_existing_publication(monkeypatch):
    page = ctypes.create_string_buffer(control.PAGE_BYTES)
    control._STATUS.pack_into(page, 0, control.MAGIC, 1, control.PAGE_BYTES, 2,
                              42, 3, control.PASSIVE, 0, 4, 1, control.CAP_GPU, 1, 7, 0, 0)
    ctypes.memmove(ctypes.addressof(page) + 256, b"fixture-native\0", 15)
    ctypes.memmove(ctypes.addressof(page) + fields.STARTUP_OFFSET, startup_raw(), fields.STARTUP.size)
    client = control.CaptureControl.__new__(control.CaptureControl)
    client._view, client._k = ctypes.addressof(page), NS()
    monkeypatch.setattr(control, "_process_creation", lambda *args: (7, 0))
    receipt = client.status()
    assert receipt.build_id == "fixture-native"
    assert receipt.gpu_startup["gl_renderer"] == "AMD Radeon"
    ctypes.memmove(client._view + fields.STARTUP_OFFSET, startup_raw(version=2), fields.STARTUP.size)
    assert client.status().gpu_startup is None


def native_lines():
    return (b"untrusted helper content with private path/ROM ignored\n"
            b"SM64_GPU_MF operation=p.mft->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER,ULONG_PTR(p.manager.Get())) hresult=c00d36b4\n"
            b"event=encoder_open_failure native_result=8 stage=encoder_prepare encoder_state=5 encoder_error=7 nv_status=3222091444 hresult=unknown\n")


def test_only_bounded_known_receipts_are_parsed_without_erasing_the_mf_hresult():
    result = fields.native_failure_receipt(native_lines())
    assert result["native_hresult"] == 0xC00D36B4
    assert result["native_result"] == 8 and result["native_stage"] == "encoder_prepare"
    assert result["driver_error"] == 3222091444
    assert "untrusted" not in json.dumps(result)
    assert fields.native_failure_receipt(b"prefix " + native_lines().splitlines()[-1]) == {}
    assert fields.native_failure_receipt(native_lines().replace(b"native_result=8", b"native_result=999999999999")).get("native_result") is None
    assert fields.native_failure_receipt(native_lines() + b"x" * 8192) == {}


def test_codec_negotiation_does_not_supply_a_later_open_failure_operation():
    prior = (b"SM64_GPU_MF operation=AV1Probe() hresult=c00d36b4\n"
             b"event=encoder_open_failure native_result=16 stage=encoder_prepare encoder_state=5 encoder_error=11 nv_status=3222091444 hresult=unknown\n")
    assert fields.native_failure_receipt(prior) == {}
    terminal = (b"event=encoder_open_failure native_result=8 stage=encoder_prepare encoder_state=5 encoder_error=7 nv_status=2147942402 hresult=unknown\n")
    receipt = fields.native_failure_receipt(prior + terminal)
    assert receipt["native_result"] == 8 and receipt["driver_error"] == 2147942402
    assert "native_operation" not in receipt and "native_hresult" not in receipt


def test_actual_namespaced_media_type_call_keeps_its_operation_and_hresult():
    operation = "mf_gpu::media_type(&encoded,subtype,s,true)"
    receipt = fields.native_failure_receipt(
        f"SM64_GPU_MF operation={operation} hresult=c00d36b4\n".encode()
        + native_lines().splitlines()[-1])
    assert receipt["native_operation"] == operation and receipt["native_hresult"] == 0xC00D36B4
    assert fields.native_failure_receipt(b"SM64_GPU_MF operation=C:\\Users\\private\\code.cpp hresult=c00d36b4") == {}


def test_successful_h264_fallback_does_not_retain_expected_av1_refusal_as_failure():
    gate = RetryGate()
    identity = gate.observe(NS(producer_pid=42, producer_created_lo=7, producer_created_hi=0,
                               generation=3, build_id="native", rom_open=True))
    owner = NS(settings=GpuSettings(), retry_gate=gate, producer_identity=identity)
    session = CaptureSession(owner, NS(snapshot=DemandSnapshot("active")))
    session.codec, session._stage = "h264", "recording"
    session._attempted_options = {"backend": 1, "codec": CODEC_H264}
    session.controller = NS(status=lambda: {"stderr_tail": native_lines().replace(b"native_result=8", b"native_result=16")})
    session._retain_diagnostic()
    result = gate.diagnostics()
    assert "first_failure" not in result
    assert result["latest"]["codec"] == "h264" and not result["latest"]["native_receipt_available"]


def test_failure_survives_cleanup_fallback_export_and_safe_automatic_reporting(tmp_path, monkeypatch):
    gate = RetryGate()
    identity = gate.observe(NS(producer_pid=42, producer_created_lo=7, producer_created_hi=0,
                               generation=3, build_id="candidate-native", rom_open=True))
    startup = fields.startup_receipt(startup_raw())
    demand = NS(snapshot=DemandSnapshot("fault", reason="native setup failed", native_startup=startup,
                                       capture_build="candidate-native"), reconcile_lifecycle=lambda: False)
    owner = NS(settings=GpuSettings(), retry_gate=gate, producer_identity=identity)
    session = CaptureSession(owner, demand)
    session._adapter_facts = AdapterFacts("AMD Radeon", 0x1002, False, "32.0.21045.5002")
    session._attempted_options = {"backend": 1, "codec": CODEC_H264}
    session._open_channel = lambda: True
    session._open_encoder = lambda: (_ for _ in ()).throw(RuntimeError("encoder result=8; native status unavailable"))
    drained = [False]
    session.controller = NS(status=lambda: {"stderr_tail": native_lines() if drained[0] else b""})
    session.close = lambda: drained.__setitem__(0, True)
    with pytest.raises(RuntimeError, match="encoder result=8"):
        session.run()
    first = gate.diagnostics()["first_failure"]
    assert first["native_hresult"] == 0xC00D36B4 and first["gpu_vendor_id"] == 0x1002
    assert first["codec"] is None and first["requested_codec"] == "h264"
    assert first["startup"]["missing_entry_point"] == "glAcquireKeyedMutexWin32EXT"
    gate.unsupported(identity, "required transport unavailable")
    gate.note_diagnostic(identity, {"stage": "later success"})
    assert gate.diagnostics()["first_failure"] == first

    recorder = ReplayRecorder.__new__(ReplayRecorder)
    recorder._capture_diagnostics = gate.diagnostics
    desktop = DesktopForUnsupportedGpu(NS(), "required transport unavailable")
    recorder.status = lambda **kwargs: {"recording": True, "frame_source": desktop.frame_source,
                                        "gpu_unsupported": desktop.gpu_unsupported}
    broadcaster = Broadcaster()
    poller = Poller(NS(attached=False), [], broadcaster)
    monkeypatch.setattr("sm64_events.server.app.diagnostics_dir", lambda: tmp_path)
    layer = NS(diagnostic_status=lambda: {"state": "needs_restart", "observed_unix_s": 1234},
               status=lambda: pytest.fail("debug export cannot run installer refresh or setup observation"))
    app = create_app(poller, broadcaster, replay=NS(recorder=recorder, cfg=NS(scratch_dir=tmp_path)), capture_layer=layer)
    response = TestClient(app).post("/api/diagnostics")
    assert response.status_code == 200
    report = Path(response.json()["path"]).read_text(encoding="utf-8")
    assert "desktop_time_estimate" in report and "first_failure" in report
    assert "3222091444" in report and "32.0.21045.5002" in report
    assert "glAcquireKeyedMutexWin32EXT" in report and "candidate-native" in report
    assert '"observed_unix_s": 1234' in report
    assert "untrusted helper content" not in report

    context = fields.automatic_context(first)
    context["gl_renderer"] += r" C:\Users\private\ROM.z64"
    safe = safe_event({"message": "GPU recording stopped", "extra": {**context, "raw_stderr": native_lines().decode()}})
    assert safe["extra"]["native_hresult"] == 0xC00D36B4
    assert safe["extra"]["gpu_driver"] == "32.0.21045.5002"
    assert "private" not in json.dumps(safe) and "ROM.z64" not in json.dumps(safe)
    assert "raw_stderr" not in safe["extra"]
    first["startup"]["gl_renderer"] = "changed by caller"
    assert gate.diagnostics()["first_failure"]["startup"]["gl_renderer"] == "AMD Radeon"


def test_a_new_producer_generation_does_not_inherit_prior_failure():
    gate = RetryGate()
    source = dict(producer_pid=42, producer_created_lo=7, producer_created_hi=0,
                  generation=3, build_id="candidate", rom_open=True)
    identity = gate.observe(NS(**source))
    gate.note_diagnostic(identity, {"reason": "old failure"}, failed=True)
    source["generation"] = 4
    gate.observe(NS(**source))
    gate.note_diagnostic(identity, {"reason": "late old failure"}, failed=True)
    assert "first_failure" not in gate.diagnostics()
