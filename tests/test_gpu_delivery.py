"""Bounded hidden real-GPU delivery witness; never touches a live capture run."""
import importlib.util
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import gpuchannel

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "tools/build_plugin.py").is_file())
SOURCE = Path(__file__).resolve().parents[1] / "plugin/gfxwrap"


def _build_host(out, *, lose_busy=False, resample_busy=False):
    spec = importlib.util.spec_from_file_location("delivery_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = next(p for p in build.KNOWN_VCVARS if p.exists())
    def quiet_run(*args, **kwargs):
        kwargs.update(quiet_spawn_kwargs())
        return subprocess.run(*args, **kwargs)
    build.subprocess = SimpleNamespace(run=quiet_run)
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    native = ROOT / "plugin/gfxwrap"
    delivery = SOURCE / "gpu_delivery.cpp"
    if lose_busy or resample_busy:
        original = delivery.read_text(encoding="utf-8")
        # Restore the discarded-busy behavior only in a test-owned build. This
        # must strand a real channel offer rather than merely fail a source scan.
        if lose_busy:
            assert original.count("if(result==ChannelResult::busy)return true;") == 2
            changed = original.replace("if(result==ChannelResult::busy)return true;", "")
        else:
            assert original.count("if(!p.sampled){") == 1
            changed = original.replace("if(!p.sampled){", "if(true){")
        delivery = out / "gpu_delivery.cpp"
        delivery.write_text(changed, encoding="utf-8")
    files = [delivery, SOURCE / "gpu_channel.cpp", SOURCE / "gpu_delivery_host.cpp"]
    files += [native / name for name in ("gpu_delivery_context.cpp", "gpu_bridge.cpp")]
    files += [native / name for name in ("source_snapshot.cpp", "gl_snapshot.cpp", "gpu_selection.cpp", "renderer_boundary.cpp", "context_lifetime.cpp", "runtime_control.cpp", "runtime_delivery.cpp", "gpu_request.cpp")]
    exe = out / "delivery.exe"
    build._cl(vc, flags + ["/std:c++17", "/EHsc", "/DGD_TEST_HOST", f"/I{SOURCE}", f"/I{native}",
        *map(str, files), f"/Fe:{exe}", f"/Fo{out}\\", "/link", *build.LIBS, "d3d11.lib", "dxgi.lib"], out)
    return exe


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    return _build_host(tmp_path_factory.mktemp("gpu-delivery"))


@pytest.fixture(scope="module")
def lost_busy_host(tmp_path_factory):
    return _build_host(tmp_path_factory.mktemp("gpu-delivery-lost-busy"), lose_busy=True)


@pytest.fixture(scope="module")
def resampled_busy_host(tmp_path_factory):
    return _build_host(tmp_path_factory.mktemp("gpu-delivery-resampled-busy"), resample_busy=True)


@pytest.mark.parametrize("busy_count", [1, 64, 4096])
def test_busy_publication_samples_immutable_head_once_cpu(host, busy_count):
    result = subprocess.run(
        [str(host), "--sample-retry-cpu", str(busy_count)], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    # No offer during contention. Published bytes remain the first sample of
    # texture73; its exact stamp survives, and slot reuse samples texture74 anew.
    assert result.stdout.strip() == f"sample retry 1 0 0 1 1 73 2 2 2 {busy_count} {busy_count}"


def test_resampling_negative_repeats_gpu_boundary_and_changes_published_bytes_cpu(resampled_busy_host):
    result = subprocess.run(
        [str(resampled_busy_host), "--sample-retry-cpu", "64"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    assert result.stdout.strip() == "sample retry 64 0 0 65 65 73 66 66 66 0 64"


def test_build_delivery(host):
    assert host.is_file()


def test_worker_failure_survives_disarm_and_resets_on_accepted_request_cpu(host):
    result = subprocess.run(
        [str(host), "--diagnostic-cpu"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    # The supervisor's later owner-gone cancellation still happened. Only the
    # diagnostic retains the first worker failure; a fresh accepted request
    # clears it and reports its normal cancellation without a stale failure.
    assert result.stdout.strip() == "diagnostic 10010 17 10011 10000 0"


def test_omission_frontier_uses_identity_before_source_record_release_cpu(host):
    result = subprocess.run(
        [str(host), "--omission-custody-cpu"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    # The release callback recycled occurrence 42 into 99 before returning.
    # Only the borrowed occurrence was observed; 99 cannot advance the frontier.
    assert result.stdout.strip() == "omission custody 42 99"


def test_pressure_refusals_are_counted_omissions_not_run_failures_cpu(host):
    """Eight source slots hold ~266 ms at 30 fps. Before round 48 one refused
    stage (a Python hiccup) ended the whole recording for 10-60 s. Now a
    full pool and refused stages are counted missing pictures."""
    result = subprocess.run(
        [str(host), "--pressure-omissions-cpu"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    # Occurrence 42 (pool full) advanced the frontier and counted once; the
    # frontier's three refused stages added three more. No failure.
    assert result.stdout.strip() == "pressure omissions 42 4"


def test_closed_channel_cannot_leave_released_source_record_pending_cpu(host):
    result = subprocess.run(
        [str(host), "--retirement-custody-cpu"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    # Source release succeeded and recycled the record, but the channel closed
    # before its offer could retire. Cleanup must not retain/release it again.
    assert result.stdout.strip() == "retirement custody 0 99"


@pytest.mark.parametrize(("scenario", "expected"), [
    (0, "channel retry 0 1 1 0 1 0"),
    (1, "channel retry 1 0 0 1 1 1"),
    (2, "channel retry 1 1 1 0 0 1"),
])
def test_busy_real_channel_retains_exact_custody_for_retry_cpu(host, scenario, expected):
    result = subprocess.run(
        [str(host), "--channel-retry-cpu", str(scenario)], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    assert result.stdout.strip() == expected


def test_discarded_busy_negative_strands_real_channel_offer_cpu(lost_busy_host):
    result = subprocess.run(
        [str(lost_busy_host), "--channel-retry-cpu", "0"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    # Source custody was returned exactly once, but the old offer still occupies
    # the channel slot. Publishing the next picture returns Result::busy (2).
    assert result.stdout.strip() == "channel retry 0 0 1 0 1 2"


def test_native_first_failure_snapshot_precedes_revoke_and_survives_callback_error_cpu(host):
    result = subprocess.run(
        [str(host), "--failure-snapshot-cpu"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    assert result.stdout.strip() == "failure snapshot 1 17 17 10022 7 4 1 1 1 1"


def test_healthy_native_windows_keep_recent_cadence_and_cumulative_failure_cpu(host):
    result = subprocess.run(
        [str(host), "--healthy-snapshot-cpu"], capture_output=True, text=True,
        timeout=5, check=True, **quiet_spawn_kwargs(),
    )
    assert result.stdout.strip() == (
        "healthy snapshot 2 windows 1 failure cadence 16 50 14 cumulative 80 "
        "recent-max 3 cumulative-max 50"
    )


def test_healthy_diagnostic_production_log_fields_cpu(tmp_path):
    spec = importlib.util.spec_from_file_location("diagnostic_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    exe = tmp_path / "diagnostic_log.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        str(SOURCE / "gpu_diagnostic_log_host.c"), f"/I{SOURCE}", f"/Fe:{exe}",
        f"/Fo{tmp_path}\\", "/link", "kernel32.lib"], tmp_path)
    result = subprocess.run([str(exe)], capture_output=True, text=True,
                            timeout=5, check=True, **quiet_spawn_kwargs())
    lines = result.stdout.splitlines()
    assert len(lines) == 10 and max(map(len, lines)) < 1024
    assert "event=gpu_summary " in lines[0]
    assert "frequency=1000 window_started_qpc=1000 previous_emit_ticks=1" in lines[0]
    assert "snapshot_bytes=12345678 bridge_bytes=8765432 sample_calls=151 sample_reuses=9 publish_busy=9" in lines[0]
    assert sum("event=gpu_summary_phase " in line for line in lines) == 8
    assert all("observed_qpc=6000" in line for line in lines)
    assert "event=gpu_summary_cadence epoch=17 records=3 pictures=1 same_origin=2" in lines[-1]
    assert "b0=0 b1=1 b2=0 b3=1 b4=0 b5=0" in lines[-1]
    assert not any("event=gpu_failure" in line or "event=gpu_source_refusal" in line for line in lines)
    report_spec = importlib.util.spec_from_file_location("native_report", ROOT / "tools/native_capture_report.py")
    report = importlib.util.module_from_spec(report_spec)
    report_spec.loader.exec_module(report)
    windows = report.parse(lines)["windows"]
    assert len(windows) == 1 and windows[0]["missing_phases"] == []
    assert windows[0]["phases"]["sample"]["max_ms"] == 3
    assert windows[0]["cadence"]["max_ms"] == 50
    assert windows[0]["previous_logging_ms"] == 1
    print(result.stdout, end="")  # JUnit stdout is the exact parser-integration fixture.


def test_build_healthy_wrapper_candidate_cpu(tmp_path):
    spec = importlib.util.spec_from_file_location("healthy_wrapper_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    artifact = build.build_wrapper(tmp_path / "wrapper")
    assert artifact.is_file() and artifact.stat().st_size > 100000
    print(f"GPU_WRAPPER_ARTIFACT={artifact}")


class Running:
    def __init__(self, exe):
        self.process = subprocess.Popen(
            [str(exe), str(os.getpid()), str(gpuchannel.process_birth(os.getpid()))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **quiet_spawn_kwargs())
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        assert self.receive() == "ready"

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line.strip())
        self.lines.put(None)

    def receive(self):
        line = self.lines.get(timeout=8)
        if line is None:
            self.process.wait(timeout=2)
            raise AssertionError(f"exit={self.process.returncode}: " + self.process.stderr.read())
        return line

    def command(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()
        return self.receive()

    def start(self, nonce, *, encoder_ready=True):
        fields = self.command(f"start {nonce}").split()
        assert fields[0] == "prepared", fields
        epoch, pid, lo, hi = map(int, fields[1:])
        client = gpuchannel.Client(nonce=(lo, hi), epoch=epoch, generation=1,
            producer_pid=pid, producer_birth=gpuchannel.process_birth(pid))
        if encoder_ready:
            client.encoder_ready()
            assert self.command("active") == "active"
        return client

    def close(self):
        if self.process.poll() is None:
            self.process.kill()  # Exact owned hidden fixture process only.
        self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        self.reader.join(timeout=1)


def eventually(fn, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.003)
    raise AssertionError("bounded worker witness did not progress")


def expected_sample(marker):
    result = bytearray()
    for y in range(0, 11, 8):
        native_y = 10-y
        for x in range(0, 17, 8):
            red = (marker*29+x*11+native_y*3+17) & 255
            green = (marker*13+x*7+native_y*23+41) & 255
            blue = (marker*19+x*31+native_y*5+71) & 255
            result.extend((blue, green, red, 255))
    return bytes(result)


def offer_for(run, client, marker):
    assert run.command(f"frame {marker}") == f"frame {marker}"
    offers = eventually(client.offers)
    assert len(offers) == 1
    offer = offers[0]
    assert (offer.width, offer.height) == (17, 11)
    assert offer.sample == expected_sample(marker)
    assert offer.lengths[:2] == (4, 8)
    assert offer.stamp_bytes == bytes((marker, 0, 0, 0, 255-marker, 0, 0, 0, 0xA5, 0, 0, 0))
    return offer


def select(client, offer, serial):
    client.reply(offer, gpuchannel.SELECTED, encode_serial=serial, pts=serial-1,
                 nominal_duration=3000, force_idr=serial == 1)
    bridges = eventually(client.bridges)
    assert len(bridges) == 1
    bridge = bridges[0]
    assert (bridge.occurrence, bridge.encode_serial, bridge.pts) == (offer.occurrence, serial, serial-1)
    return bridge


def test_real_delivery_selection_actual_key_custody_and_restart(host):
    run = Running(host)
    client = None
    try:
        client = run.start(101)
        first = offer_for(run, client, 7)
        bridge = select(client, first, 1)
        assert run.command(f"take {bridge.texture_index} 7") == "take 1"
        client.acknowledge_metadata(bridge)
        time.sleep(0.025)
        state = run.command("status").split()
        assert int(state[5]) == 1, "metadata acknowledgment falsely released key ownership"
        duplicate = offer_for(run, client, 11)
        client.reply(duplicate, gpuchannel.COALESCED, retained_occurrence=first.occurrence, retained_serial=1)
        assert run.command(f"give {bridge.texture_index}") == "give"
        reset = offer_for(run, client, 2)
        assert reset.occurrence > duplicate.occurrence > first.occurrence
        next_bridge = select(client, reset, 2)
        assert run.command(f"take {next_bridge.texture_index} 2") == "take 1"
        client.acknowledge_metadata(next_bridge)
        assert run.command("stop") == "stopped"
        time.sleep(0.025)
        assert run.command("status").split()[1] == "4", "stop freed consumer-held GPU texture"
        assert run.command(f"give {next_bridge.texture_index}") == "give"
        assert run.command("settled") == "settled"
        client.close()
        client = run.start(102)
        third = offer_for(run, client, 9)
        pending = select(client, third, 1)
        # Close before Submit: stop must reclaim actual unconsumed key1.
        assert run.command("stop") == "stopped"
        eventually(lambda: run.command("status").split()[1] == "0")
        assert run.command(f"take {pending.texture_index} 9") == "take 0"
        assert run.command("settled") == "settled"
        client.close()
        client = None
        assert run.command("exit") == "exit"
        assert run.process.wait(timeout=3) == 0
    finally:
        if client:
            client.close()
        run.close()


def test_prepare_cancellation_and_invalid_requests_never_activate(host):
    run = Running(host)
    client = None
    try:
        assert run.command("invalid") == "invalid refused"
        assert run.command("cancelprep") == "cancelled preparation"
        client = run.start(334)
        first = offer_for(run, client, 5)
        client.reply(first, gpuchannel.SUPPRESSED, reason=1)
        assert run.command("stop") == "stopped"
        assert run.command("settled") == "settled"
        client.close()
        client = None
        assert run.command("exit") == "exit"
    finally:
        if client:
            client.close()
        run.close()


def test_real_supervisor_accepts_before_worker_runs_and_after_prior_fault(host):
    run = Running(host)
    try:
        assert run.command("startuprace") == "startup race 2"
        assert run.command("exit") == "exit"
    finally:
        run.close()


def test_pending_head_blocks_later_samples_and_frontier(host):
    run = Running(host)
    client = None
    try:
        client = run.start(201)
        eventually(client.frontier)
        assert run.command("hold 1") == "hold"
        assert run.command("frame 6") == "frame 6"
        first_stamp = int(run.command("laststamp").split()[1])
        assert run.command("frame 3") == "frame 3"
        time.sleep(0.03)
        assert not client.offers()
        frontier = client.frontier()
        assert frontier is None or frontier[0] <= first_stamp
        assert run.command("hold 0") == "hold"
        ordered = []
        for _ in range(2):
            offers = eventually(client.offers)
            ordered.extend(offers)
            for offer in offers:
                client.reply(offer, gpuchannel.SUPPRESSED, reason=1)
            if len(ordered) == 2:
                break
        assert [o.sample for o in ordered] == [expected_sample(6), expected_sample(3)]
        assert ordered[0].occurrence < ordered[1].occurrence
        assert run.command("stop") == "stopped"
        assert run.command("settled") == "settled"
        client.close()
        client = None
        assert run.command("exit") == "exit"
    finally:
        if client:
            client.close()
        run.close()


def test_prepared_channel_waits_for_explicit_encoder_readiness(host):
    run = Running(host)
    client = None
    try:
        client = run.start(301, encoder_ready=False)
        for marker in range(12):
            assert run.command(f"frame {marker}") == f"frame {marker}"
        assert run.command("status").split()[1] == "2"
        assert not client.offers(), "captures began before the encoder was ready"
        client.encoder_ready()
        assert run.command("active") == "active"
        offer = offer_for(run, client, 12)
        client.reply(offer, gpuchannel.SUPPRESSED, reason=1)
        assert run.command("stop") == "stopped"
        assert run.command("settled") == "settled"
        client.close()
        client = None
        assert run.command("exit") == "exit"
    finally:
        if client:
            client.close()
        run.close()


def test_abandoned_key_quarantines_fixed_pool_and_refuses_reallocation(host):
    run = Running(host)
    client = None
    try:
        client = run.start(501)
        select(client, offer_for(run, client, 8), 1)
        assert run.command("abandon") == "abandoned injected"
        assert run.command("exhausted") == "exhausted 128"
        client.close()
        client = None
        assert run.command("exitquarantined") == "exit"
    finally:
        if client:
            client.close()
        run.close()
