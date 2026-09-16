"""CPU-only ownership fault injection: no Win32 APIs, devices or native hosts.

Production close/command/supervisor/session code executes against an inert
kernel adapter. Nonzero release results are the independent disposal witness.
"""
from types import SimpleNamespace as NS
import threading

import pytest

from sm64_events.replay import capturecontrol as C
from sm64_events.replay import gpuchannel as CH
from sm64_events.replay import gpurequest as R
from sm64_events.replay.gpucapture import GpuCapture, discover
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.ownedclose import CleanupPending, CleanupRetrier, ProcessHandle
from sm64_events.memory.layout import US
from test_gpudemand import Fixture, eventual


class Kernel:
    def __init__(self):
        self.failures = {}
        self.calls = []
        self.dead = False
        self.busy = False
        self.wait_error = False

    def release(self, operation, handle):
        self.calls.append((operation, handle, threading.get_ident()))
        key = operation, handle
        if self.failures.get(key, 0):
            self.failures[key] -= 1
            return 0
        return 1

    def UnmapViewOfFile(self, handle):
        return self.release('unmap', handle)

    def CloseHandle(self, handle):
        return self.release('close', handle)

    def ReleaseMutex(self, handle):
        return self.release('release_mutex', handle)

    def SetEvent(self, handle):
        return self.release('wake', handle)

    def WaitForSingleObject(self, handle, timeout):
        self.calls.append(('wait', handle, threading.get_ident()))
        if handle == 900:
            return 0xFFFFFFFF if self.wait_error else (0 if self.dead else 258)
        return 258 if self.busy else 0

    def OpenProcess(self, access, inherit, pid):
        return 900

    def GetProcessTimes(self, handle, created, *unused):
        created._obj.value = 0x100000037
        return 1


def control(kernel):
    owner = C.CaptureControl.__new__(C.CaptureControl)
    owner._k = kernel
    owner._map, owner._view, owner._wake, owner._mutex = 101, 102, 103, 104
    owner._producer = 900
    owner._lease = None
    owner._write_locked, owner._write_thread = False, None
    owner.name = 'inert control'
    status = C.CaptureStatus(77, 4, C.ACTIVE, 0, 123, 1, C.CAP_GPU, True, 55, 0, 'fixture')
    owner.status = lambda: status
    page = [(0, 123, 88, 99, 0, 4, 1, 1)]
    owner._request = lambda: page[0]
    owner._publish = lambda value: page.__setitem__(0, value)
    owner._lease = C.CaptureLease(owner, 77, 4, 123)
    return owner, page


def request(kernel, lease):
    owner = R.GpuRequest.__new__(R.GpuRequest)
    owner._k, owner.lease = kernel, lease
    owner._view, owner._map = 201, 202
    return owner


def channel(kernel, *, opened=True):
    owner = CH.Client.__new__(CH.Client)
    owner._thread, owner._k = threading.current_thread(), kernel
    owner._map, owner._view, owner._mutex, owner._process = 301, 302, 303, 900
    owner.wake_event, owner.result_event = 304, 305
    owner._owns_mutex, owner._client_open = True, opened
    owner._pending, owner._bridge_pending = {1: 'retained'}, {2: 'retained'}
    owner._header = NS(client_offset=0, nonce_lo=1, nonce_hi=2, epoch=3,
                       generation=4, owner_pid=88, owner_birth=99)
    owner._write = lambda *args: None
    return owner


def test_failed_disable_retains_entire_request_control_chain_until_retry():
    kernel = Kernel()
    ctl, page = control(kernel)
    req = request(kernel, ctl._lease)
    lease = req.lease
    kernel.busy = True
    with pytest.raises(TimeoutError):
        req.close()
    with pytest.raises(TimeoutError):
        ctl.close()
    assert req.lease is ctl._lease is lease and lease._owned
    assert req._view == 201 and ctl._view == 102 and ctl._producer == 900
    assert not any(name in {'close', 'unmap'} for name, _, _ in kernel.calls)
    kernel.busy = False
    req.close()
    ctl.close()
    assert page[0][-1] == 0 and not lease._owned
    assert req.lease is req._view is req._map is ctl._lease is ctl._view is ctl._producer is None
    completed = list(kernel.calls)
    req.close()
    ctl.close()
    assert kernel.calls == completed, 'idempotent close must not release a reused numeric handle'


def test_failed_command_mutex_release_does_not_recursively_acquire_again():
    kernel = Kernel()
    ctl, page = control(kernel)
    req = request(kernel, ctl._lease)
    kernel.failures['release_mutex', 104] = 1
    with pytest.raises(OSError, match='mutex remains owned'):
        req.close()
    assert ctl._write_locked and req.lease._owned
    assert ctl._write_thread is threading.current_thread()
    assert page[0][-1] == 0  # Publication happened; mutex release has not.
    req.close()
    ctl.close()
    assert not ctl._write_locked and ctl._write_thread is None
    assert sum(name == 'wait' and handle == 104 for name, handle, _ in kernel.calls) == 1


def test_exact_producer_exit_releases_lease_without_touching_broken_old_page():
    kernel = Kernel()
    ctl, _ = control(kernel)
    req = request(kernel, ctl._lease)
    kernel.busy = kernel.dead = True
    ctl.status = lambda: pytest.fail('retired process page must not be read')
    req.close()
    ctl.close()
    assert not any(name == 'wait' and handle == 104 for name, handle, _ in kernel.calls)
    assert req._view is ctl._view is None


def test_failed_process_wait_is_not_permission_to_discard_lease():
    kernel = Kernel()
    ctl, _ = control(kernel)
    kernel.wait_error = kernel.busy = True
    with pytest.raises(TimeoutError):
        ctl.close()
    assert ctl._lease._owned and ctl._producer == 900
    kernel.wait_error = kernel.busy = False
    ctl.close()


@pytest.mark.parametrize('kind,operation,handle', [
    ('control', 'unmap', 102), ('control', 'close', 101),
    ('request', 'unmap', 201), ('request', 'close', 202),
])
def test_zero_release_result_retains_only_failed_handle_and_retries(kind, operation, handle):
    kernel = Kernel()
    owner = control(kernel)[0] if kind == 'control' else request(kernel, None)
    kernel.failures[operation, handle] = 1
    with pytest.raises(OSError, match='remains owned'):
        owner.close()
    assert getattr(owner, '_view' if operation == 'unmap' else '_map') == handle
    owner.close()
    assert owner._map is owner._view is None
    assert sum(name == operation and value == handle for name, value, _ in kernel.calls) == 2


def test_channel_failed_notification_retains_view_until_retry_or_exact_process_exit():
    kernel = Kernel()
    owner = channel(kernel)

    def fail(*args):
        raise OSError('notification failed')

    owner._write = fail
    with pytest.raises(OSError, match='notification failed'):
        owner.close()
    assert owner._client_open and owner._view == 302 and owner._owns_mutex
    kernel.dead = True
    owner.close()
    assert owner._view is owner._map is owner._mutex is owner._process is None
    assert not owner._pending and not owner._bridge_pending


def test_channel_reader_mutex_is_not_closed_while_release_is_unproved():
    kernel = Kernel()
    owner = channel(kernel, opened=False)
    kernel.failures['release_mutex', 303] = 1
    with pytest.raises(OSError, match='mutex remains owned'):
        owner.close()
    assert owner._owns_mutex and owner._mutex == 303
    assert not any(name == 'close' and value == 303 for name, value, _ in kernel.calls)
    assert owner._pending and owner._bridge_pending
    owner.close()
    assert not owner._owns_mutex and owner._mutex is None
    assert not owner._pending and not owner._bridge_pending


def test_supervisor_retries_same_request_before_releasing_control_and_done():
    fixture = Fixture()
    demand = fixture.demand().start()
    assert demand.ready.wait(1)
    req = fixture.request
    allowed = threading.Event()
    closes = []
    original = req.close

    def close():
        closes.append(threading.get_ident())
        if not allowed.is_set():
            raise OSError('lease command temporarily busy')
        original()

    req.close = close
    demand.request_stop('server shutdown', expected=True)
    try:
        eventual(lambda: demand.snapshot.cleanup_error)
        assert not demand.done.is_set() and fixture.closed == 0
        assert demand._request is req and demand._control is fixture
        with pytest.raises(TimeoutError, match='still owns cleanup'):
            demand.close(timeout=0.01)
        with pytest.raises(RuntimeError, match='restarted'):
            demand.start()
        allowed.set()
        assert demand.done.wait(2)
        demand.close()
        assert demand.snapshot.cleanup_error == '' and demand.snapshot.cleanup_attempts == 1
        assert demand._request is demand._control is None and fixture.closed == 1
        assert len(set(closes)) == 1 and closes[0] != threading.get_ident()
        assert fixture.requests == 1
    finally:
        allowed.set()
        demand.close(timeout=2)


def test_discovery_constructor_hands_failed_cleanup_to_same_supervisor():
    kernel = Kernel()
    owner, _ = control(kernel)
    owner._lease = None
    kernel.failures['unmap', 102] = 1
    fixture = Fixture()

    def factory(name):
        raise CleanupPending(owner, 'partial constructor cleanup')

    demand = fixture.demand()
    demand._control_factory = factory
    delays = []
    demand._cleanup_wait.wait = lambda delay: delays.append(delay)
    demand.start()
    assert demand.done.wait(1)
    demand.close()
    assert delays == [1] and owner._view is None and owner._map is None
    assert demand._control is demand._request is None and fixture.requests == 0
    assert len({tid for _, _, tid in kernel.calls}) == 1


def test_channel_constructor_owner_reaches_session_cleanup():
    kernel = Kernel()
    owner = channel(kernel, opened=False)
    kernel.failures['unmap', 302] = 1

    def factory(**kwargs):
        raise CleanupPending(owner, 'channel constructor interrupted')

    demand = NS(snapshot=NS(lifecycle=False, state='active'),
                identity=R.RequestIdentity(77, 55, 4, 88, 99, 123, bytes(range(16))),
                request_stop=lambda *args: None)
    capture = NS(settings=GpuSettings(), want_capture=lambda: True,
                 end_audio=lambda *args: None)
    session = CaptureSession(capture, demand, channel_factory=factory)
    with pytest.raises(CleanupPending):
        session._open_channel()
    assert session.channel is owner
    with pytest.raises(OSError):
        session.close()
    session.close()
    assert owner._view is owner._mutex is None


def test_discovery_partial_control_is_owned_even_when_capture_never_starts():
    kernel = Kernel()
    owner, _ = control(kernel)
    owner._lease = None
    kernel.failures['unmap', 102] = 1

    def factory():
        raise CleanupPending(owner, 'read-only discovery cleanup')

    video = discover(77, US, nominal_rate=30, control_factory=factory)
    assert isinstance(video, GpuCapture) and video._pending_control is owner
    with pytest.raises(OSError):
        video.stop()
    assert video._pending_control is owner
    video.stop()
    assert video._pending_control is None and owner._view is None


def test_setup_probe_retries_partial_owner_before_reading_and_can_close():
    from sm64_events.core.setup_gpu import GpuSetupProbe

    kernel = Kernel()
    owner, _ = control(kernel)
    owner._lease = None
    kernel.failures['unmap', 102] = 1
    now, reads = [0.0], []

    def read():
        reads.append(True)
        if len(reads) == 1:
            raise CleanupPending(owner, 'observer constructor interrupted')
        return None

    probe = GpuSetupProbe(lambda: {'frame_source_health': {'kind': 'gpu'}}, read,
                          clock=lambda: now[0])
    assert not probe().alive and probe.cleanup_status()['pending']
    assert not probe().alive and len(reads) == 1
    now[0] = 1
    assert not probe().alive and probe.cleanup_status()['pending']
    assert len(reads) == 1 and probe.cleanup_status()['retry_in_s'] == 2
    now[0] = 3
    assert not probe().alive and len(reads) == 2
    assert not probe.cleanup_status()['pending'] and owner._view is None
    probe.close()
    assert not probe().alive and len(reads) == 2


def test_setup_shutdown_retains_failed_owner_until_later_close():
    from sm64_events.core.setup_gpu import GpuSetupProbe

    kernel = Kernel()
    owner, _ = control(kernel)
    owner._lease = None
    kernel.failures['unmap', 102] = 1

    def read():
        raise CleanupPending(owner, 'observer close failed')

    probe = GpuSetupProbe(lambda: {}, read)
    probe()
    with pytest.raises(OSError):
        probe.close()
    assert probe.cleanup_status()['pending'] and probe.cleanup_status()['closed']
    probe.close()
    assert not probe.cleanup_status()['pending'] and owner._view is None


@pytest.mark.parametrize('probe', ['control', 'channel'])
def test_temporary_process_probe_retains_failed_close_handle(monkeypatch, probe):
    kernel = Kernel()
    kernel.failures['close', 900] = 1
    monkeypatch.setattr(CH, '_kernel', lambda: kernel)
    with pytest.raises(CleanupPending) as error:
        if probe == 'control':
            C._process_creation(kernel, 77)
        else:
            CH.process_birth(77)
    owner = error.value.owner
    assert owner.handle == 900
    owner.close()
    owner.close()
    assert owner.handle is None
    assert sum(name == 'close' for name, _, _ in kernel.calls) == 2


def test_process_probe_unknown_access_or_wait_failure_is_not_death(monkeypatch):
    kernel = Kernel()
    kernel.wait_error = True
    with pytest.raises(OSError, match='liveness'):
        C._process_creation(kernel, 77)
    assert kernel.calls[-1][:2] == ('close', 900)
    kernel.OpenProcess = lambda *args: 0
    monkeypatch.setattr(C.C, 'get_last_error', lambda: 5)
    with pytest.raises(OSError, match='identity'):
        C._process_creation(kernel, 77)
    monkeypatch.setattr(C.C, 'get_last_error', lambda: 87)
    assert C._process_creation(kernel, 77) is None


def test_constructor_retains_both_nested_process_and_failed_mapping_owner(monkeypatch):
    kernel = Kernel()
    kernel.OpenFileMappingW = lambda *args: 101
    kernel.MapViewOfFile = lambda *args: 102
    kernel.failures['unmap', 102] = 1
    temporary = ProcessHandle(kernel, 900)
    controls = []

    def fail_status(self):
        controls.append(self)
        raise CleanupPending(temporary, 'temporary process CloseHandle failed')

    monkeypatch.setattr(C, '_kernel', lambda: kernel)
    monkeypatch.setattr(C.CaptureControl, 'status', fail_status)
    with pytest.raises(CleanupPending) as error:
        C.CaptureControl()
    owner = error.value.owner
    assert owner.owners == [temporary, controls[0]]
    owner.close()
    assert not owner.owners and temporary.handle is controls[0]._view is None
    assert [call[:2] for call in kernel.calls][-2:] == [('close', 900), ('unmap', 102)]


def test_cleanup_callback_retries_nested_owner_before_original_callback():
    calls = []

    def nested_close():
        calls.append('nested')
        if calls.count('nested') == 1:
            raise OSError('still pending')

    nested = NS(close=nested_close)

    def close():
        calls.append('outer')
        if calls.count('outer') == 1:
            raise CleanupPending(nested, 'inner handle close failed')

    retry = CleanupRetrier(close)
    with pytest.raises(CleanupPending):
        retry()
    with pytest.raises(OSError):
        retry()
    assert retry.pending is nested and calls == ['outer', 'nested']
    retry()
    assert retry.pending is None and calls == ['outer', 'nested', 'nested', 'outer']


def test_runtime_identity_probe_failure_is_retained_by_demand_supervisor():
    fixture = Fixture()
    kernel = Kernel()
    temporary = ProcessHandle(kernel, 900)
    kernel.failures['close', 900] = 1
    demand = fixture.demand()
    original = fixture.status

    def status():
        if demand.ready.is_set():
            raise CleanupPending(temporary, 'monitor process handle close failed')
        return original()

    fixture.status = status
    delays = []
    demand._cleanup_wait.wait = lambda delay: delays.append(delay)
    demand.start()
    assert demand.done.wait(1)
    demand.close()
    assert demand.snapshot.state == 'fault' and not demand.snapshot.lifecycle
    assert demand._extra_owner is demand._request is demand._control is None
    assert temporary.handle is None and delays == [1] and fixture.closed == 1
    assert len({tid for _, _, tid in kernel.calls}) == 1


def test_request_acquire_retains_lease_when_post_publication_wake_fails(monkeypatch):
    kernel = Kernel()
    ctl, page = control(kernel)
    ctl._lease = None
    kernel.failures['wake', 103] = 2
    monkeypatch.setattr(C, '_process_creation', lambda *args: (55, 0))
    monkeypatch.setattr(C.secrets, 'randbelow', lambda value: 122)
    ctl._hold_producer = lambda status: None
    acquire = ctl.acquire
    ctl.acquire = lambda **kwargs: acquire()  # Prebuilt inert config; no pointer writes.

    class PreparedRequest(R.GpuRequest):
        def __init__(self):
            self._k, self.lease = kernel, None
            self._view, self._map = 201, 202
            self.identity = R.RequestIdentity(77, 55, 4, 88, 99, 123, bytes(range(16)))

    with pytest.raises(CleanupPending) as error:
        PreparedRequest.acquire(ctl, (), None)
    owner = error.value.owner
    assert owner.lease is ctl._lease and owner.lease._owned
    assert owner._view == 201 and page[0][-1] == 0
    assert not any(name == 'unmap' for name, _, _ in kernel.calls)
    owner.close()
    ctl.close()
    assert owner._view is owner.lease is ctl._lease is None


def test_open_client_owes_close_even_when_publication_wake_fails():
    kernel = Kernel()
    owner = channel(kernel, opened=False)
    owner._word = lambda offset: 0
    written = []

    def write(offset, value):
        written.append(value.state)
        if value.state == 1:
            raise OSError('event notification failed after publication')

    owner._write = write
    with pytest.raises(OSError):
        owner._open_client()
    assert owner._client_open and owner._view == 302
    owner.close()
    assert written == [1, 2] and not owner._client_open and owner._view is None
