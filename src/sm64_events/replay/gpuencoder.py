"""Single-worker synchronous adapter; no process creation, raw pixels or file sink.

PENDING requires a later worker poll, not a spin here. Close must succeed before
releasing ownership. Quarantine pins callbacks/DLL until owned process disposal.
"""

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
import ctypes as C
import threading
from . import gpuencoder_abi as abi
from .gpuencoder_options import pod_options, uint


class Result(IntEnum):
    OK = 0
    ARGUMENT = 1
    ABI = 2
    BUSY = 3
    THREAD = 4
    HANDLE = 5
    ADAPTER = 6
    IMPORT = 7
    FORMAT = 8
    TIMEOUT = 9
    PENDING = 10
    ABANDONED = 11
    ENCODER = 12
    QUARANTINE = 13
    STATE = 14
    MEMORY = 15


@dataclass(frozen=True)
class EncodedPacket:
    serial: int
    pts: int
    encoder_duration: int
    keyframe: bool
    payload: bytes


class EncoderFailure(RuntimeError):
    def __init__(self, message, *, result=None, status=None):
        super().__init__(message)
        self.result = result
        self.status = status
        self.worker_disposal_required = result in (
            Result.ABANDONED,
            Result.QUARANTINE,
        ) or bool(status and status.get("state") == 5)


# No destructor teardown: retain any native session whose Close did not succeed.
_LIVE = set()


def _native_config(adapter_luid, names, options):
    config = abi.initialize(abi.Config)
    config.encoder = pod_options(options)
    if len(adapter_luid) != 2 or len(names) != 2 or names[0] == names[1]:
        raise ValueError("exact LUID and two distinct names required")
    high, low = adapter_luid
    if type(high) is not int or not -(1 << 31) <= high < (1 << 31):
        raise ValueError("signed high LUID required")
    config.adapter_high = high
    config.adapter_low = uint(low, 32, "adapter low")
    config.slot_count = 2
    for index, name in enumerate(names):
        if not isinstance(name, str) or not name or chr(0) in name:
            raise ValueError("nonempty shared resource name required")
        raw = name.encode("utf-16-le")
        units = len(raw) // 2
        if units >= 128:
            raise ValueError("shared resource name exceeds ABI bound")
        for pos in range(units):
            config.names[index][pos] = int.from_bytes(
                raw[2 * pos : 2 * pos + 2], "little"
            )
    return config


class EncoderClient:
    def __init__(self, *, dll_path, adapter_luid, names, options, on_packet):
        self._initialize(
            dll_path=dll_path,
            adapter_luid=adapter_luid,
            names=names,
            options=options,
            on_packet=on_packet,
            loader=C.CDLL,
        )

    def _initialize(self, *, dll_path, adapter_luid, names, options, on_packet, loader):
        self._owner = threading.current_thread()
        self._busy = False
        self._expected = None
        self._seen = False
        self._uncertain = False
        self.failed = False
        self.closed = False
        self._finishing = False
        self.failure = None
        self.final_status = None
        self._last_serial = -1
        self._last_pts = -1
        self._token = abi.Q()
        self._sink = on_packet
        if not callable(on_packet):
            raise ValueError("packet callback required")
        path = Path(dll_path)
        if not path.is_absolute() or path.name != "SM64GpuEncoderV1.dll":
            raise ValueError("absolute SM64GpuEncoderV1.dll path required")
        if not path.is_file():
            raise FileNotFoundError(path)
        config = _native_config(adapter_luid, names, options)
        self.packet_limit = config.encoder.max_packet_bytes
        self._callback = abi.Callback(self._receive)
        config.sink = abi.Sink(C.sizeof(abi.Sink), 1, self._callback, None)
        self._dll = loader(str(path), winmode=0x1100)
        abi.bind(self._dll)
        _LIVE.add(self)
        try:
            result = self._call("Open", C.byref(config), C.byref(self._token))
            if result != Result.OK:
                self._raise(result, "encoder open failed")
            if not self._token.value:
                self._raise(
                    Result.QUARANTINE,
                    "native open accepted without publishing custody token",
                )
        except BaseException:
            if not self._token.value and not self._uncertain:
                _LIVE.discard(self)
            raise

    def _check_owner(self):
        if threading.current_thread() is not self._owner:
            raise RuntimeError("encoder belongs to its opening worker thread")
        if self._busy:
            raise RuntimeError("encoder calls may not reenter a native callback")

    def _call(self, name, *args):
        self._check_owner()
        self._busy = True
        try:
            return Result(getattr(self._dll, "SM64GpuEncoder" + name + "V1")(*args))
        except BaseException as exc:
            self._uncertain = True
            self.failed = True
            self.failure = "native call failed: " + type(exc).__name__
            raise EncoderFailure(self.failure, result=Result.QUARANTINE) from exc
        finally:
            self._busy = False

    def _check_custody(self):
        if self._uncertain:
            raise EncoderFailure(
                self.failure
                or "native custody is uncertain; dispose owned worker process",
                result=Result.QUARANTINE,
            )

    def _raise(self, result, message):
        self.failed = True
        status = None
        self._uncertain |= result in (Result.QUARANTINE, Result.ABANDONED)
        if self._token.value:
            value = abi.initialize(abi.Status)
            if self._call("Status", self._token, C.byref(value)) == Result.OK:
                status = abi.snapshot(value)
        self.failure = self.failure or message
        raise EncoderFailure(self.failure, result=result, status=status)

    def _receive(self, user, pointer):
        try:
            if (
                threading.current_thread() is not self._owner
                or not self._busy
                or self._expected is None
                or self._seen
            ):
                raise ValueError("unexpected or duplicate packet callback")
            self._seen = True
            if not pointer:
                raise ValueError("null packet")
            packet = pointer.contents
            if (
                packet.struct_size != C.sizeof(abi.Packet)
                or packet.version != 1
                or packet.flags & ~1
                or not packet.data
                or not 0 < packet.bytes <= self.packet_limit
            ):
                raise ValueError("invalid compressed packet header/bound")
            if (packet.occurrence, packet.pts, packet.duration) != self._expected:
                raise ValueError("packet serial/PTS/encoder-duration mismatch")
            copied = EncodedPacket(
                packet.occurrence,
                packet.pts,
                packet.duration,
                bool(packet.flags & 1),
                C.string_at(packet.data, packet.bytes),
            )
            if self._sink(copied) is not True:
                raise ValueError("packet consumer refused output")
            return 0
        except BaseException as exc:  # noqa: BLE001 - no Python exception may escape a C callback.
            self.failed = True
            try:
                self.failure = (type(exc).__name__ + ": " + str(exc))[:512]
            except BaseException:  # noqa: BLE001 - exception formatting must stay inside the C boundary.
                self.failure = "packet callback failed"
            return 1

    def _admit(self, serial, pts, encoder_duration):
        self._check_owner()
        if self.closed or self.failed or self._finishing:
            raise EncoderFailure(
                self.failure or "encoder run is closed/failed/finishing"
            )
        uint(serial, 64, "encoding serial")
        uint(pts, 64, "PTS")
        uint(encoder_duration, 64, "encoder duration", positive=True)
        if (
            serial <= self._last_serial
            or pts <= self._last_pts
            or pts + encoder_duration >= (1 << 64)
        ):
            raise ValueError(
                "fresh increasing serial/PTS and nonoverflowing duration required"
            )
        self._expected = (serial, pts, encoder_duration)
        self._seen = False

    def _picture_call(self, name, picture):
        try:
            result = self._call(name, self._token, C.byref(picture))
            if result == Result.OK:
                if not self._seen or self.failed:
                    self._raise(
                        result, "native accepted without exactly one accepted packet"
                    )
                self._last_serial, self._last_pts = picture.occurrence, picture.pts
            elif result not in (Result.TIMEOUT, Result.PENDING):
                self._raise(result, "native picture processing failed")
            elif self._seen:
                self._raise(
                    result, "native reported no admission after a packet callback"
                )
            return result
        finally:
            self._expected = None

    def submit(self, slot, *, serial, pts, encoder_duration, force_idr=False):
        self._check_owner()
        if type(slot) is not int or slot not in (0, 1) or type(force_idr) is not bool:
            raise ValueError("slot0/1 and Boolean force_idr required")
        self._admit(serial, pts, encoder_duration)
        return self._picture_call(
            "Submit",
            abi.Picture(
                C.sizeof(abi.Picture),
                1,
                slot,
                int(force_idr),
                serial,
                pts,
                encoder_duration,
            ),
        )

    def repeat(self, generation, *, serial, pts, encoder_duration, force_idr=False):
        self._check_owner()
        uint(generation, 64, "retained generation", positive=True)
        if type(force_idr) is not bool:
            raise ValueError("Boolean force_idr required")
        self._admit(serial, pts, encoder_duration)
        return self._picture_call(
            "Repeat",
            abi.Repeat(
                C.sizeof(abi.Repeat),
                1,
                int(force_idr),
                0,
                generation,
                serial,
                pts,
                encoder_duration,
            ),
        )

    def status(self):
        self._check_owner()
        if self.closed:
            return self.final_status.copy()
        value = abi.initialize(abi.Status)
        result = self._call("Status", self._token, C.byref(value))
        if result != Result.OK:
            self._raise(result, "native status unavailable")
        return abi.snapshot(value)

    def retained(self):
        self._check_owner()
        self._check_custody()
        if self.closed:
            raise EncoderFailure("encoder closed")
        value = abi.initialize(abi.Retained)
        result = self._call("Retained", self._token, C.byref(value))
        if result != Result.OK:
            self._raise(result, "retained input status unavailable")
        return abi.snapshot(value)

    def poll(self):
        self._check_owner()
        self._check_custody()
        if self.closed:
            return Result.OK
        result = self._call("Poll", self._token)
        if result not in (Result.OK, Result.PENDING):
            self._raise(result, "GPU key completion failed; inspect custody status")
        return result

    def close(self):
        self._check_owner()
        self._check_custody()
        if self.closed:
            return Result.OK
        self._finishing = True
        value = abi.initialize(abi.Status)
        result = self._call("Close", self._token, C.byref(value))
        if result == Result.OK:
            self.final_status = abi.snapshot(value)
            self.closed = True
            self._token.value = 0
            _LIVE.discard(self)
        elif result != Result.PENDING:
            self._raise(result, "encoder close failed; retain worker custody")
        return result
