"""Bounded ordered picture metadata and compressed packet coordination.

Single media-worker owner. Native admission must reserve this bounded capacity
BEFORE dispatching capture/encode work. Logical packet reservations are not a
claim about driver buffers or actual Python RSS. Native GPU ownership remains
with the native pool until its own completion/release protocol succeeds.
"""

from collections import deque
from dataclasses import dataclass
import json, math, threading
from sm64_events.replay.media import next_picture_pts


class ExistingTickAllocator:
    """Own sequential state; use the same pure tick allocator as the old sink."""

    def __init__(self, run):
        self.run = run
        self.last_pts = None

    def __call__(self, stamp):
        self.last_pts = next_picture_pts(self.run, stamp, self.last_pts)
        return self.last_pts


@dataclass(frozen=True)
class Ticket:
    run: str
    serial: int


@dataclass(frozen=True)
class Source:
    occurrence: int
    capture_ts: float
    frame: int | None
    stamps_json: bytes

    def stamps(self):
        return json.loads(self.stamps_json)


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class EncodeRequest:
    ticket: Ticket
    source: Source
    pts: int
    repeat: bool
    encoder_duration: int  # Nominal codec bookkeeping, NOT presentation coverage.


@dataclass(frozen=True)
class PacketReady:
    ticket: Ticket
    source: Source
    pts: int
    duration: int
    repeat: bool
    payload: bytes
    keyframe: bool


@dataclass(frozen=True)
class Outcome:
    kind: str  # muxed, coalesced, suppressed, failed
    ticket: Ticket
    source: Source
    pts: int | None
    retained: Ticket | None = None
    reason: str = ""


@dataclass
class _Node:
    ticket: Ticket
    source: Source
    packet_limit: int
    reservation: int
    offered_at: float
    event_ts: float
    state: str = "offered"
    pts: int | None = None
    end: int | None = None
    retained: Ticket | None = None
    reason: str = ""
    payload: bytes | None = None
    keyframe: bool = False
    repeat: bool = False


class OrderedPackets:
    def __init__(
        self,
        run,
        allocate,
        *,
        encoder_duration,
        max_count=16,
        max_bytes=4 * 1024 * 1024,
        max_stamp_bytes=4096,
        max_age=2.0,
    ):
        if type(encoder_duration) is not int or not 0 < encoder_duration < (1 << 63):
            raise ValueError("positive nominal encoder duration required")
        self.encoder_duration = encoder_duration
        self.owner = threading.get_ident()
        if (
            not math.isfinite(max_age)
            or min(max_count, max_bytes, max_stamp_bytes, max_age) <= 0
        ):
            raise ValueError("positive bounds required")
        self.run = run
        self.allocate = allocate
        self.max_count = max_count
        self.max_bytes = max_bytes
        self.max_stamp_bytes = max_stamp_bytes
        self.max_age = max_age
        self._nodes = {}
        self._order = deque()
        self._selection = deque()
        self._encode = deque()
        self._dispositions = deque()
        self._bytes = 0
        self._serial = 0
        self._last_occurrence = 0
        self._last_capture = None
        self._last_selected = None
        self._held = None
        self.frontier = None
        self.closed = False
        self.fault = None

    def _check_owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("packet coordinator belongs to its media worker")

    @property
    def last_selected_pts(self):
        return None if self._last_selected is None else self._last_selected.pts

    @property
    def pending_count(self):
        return len(self._nodes)

    @property
    def pending_bytes(self):
        return self._bytes

    def can_offer(self, packet_limit):
        self._check_owner()
        return (not self.closed and not self.fault and packet_limit > 0
                and self._capacity(packet_limit + self.max_stamp_bytes))

    def _capacity(self, size):
        return (
            len(self._nodes) < self.max_count and self._bytes + size <= self.max_bytes
        )

    def _append(self, source, packet_limit, now, repeat=False, event_ts=None):
        if packet_limit <= 0 or not math.isfinite(now):
            return Refused("invalid admission")
        reservation = packet_limit + len(source.stamps_json)
        if not self._capacity(reservation):
            return Refused("capacity")
        self._serial += 1
        ticket = Ticket(self.run.id, self._serial)
        node = _Node(
            ticket,
            source,
            packet_limit,
            reservation,
            now,
            source.capture_ts if event_ts is None else event_ts,
            repeat=repeat,
        )
        self._nodes[ticket] = node
        self._order.append(ticket)
        self._selection.append(ticket)
        self._bytes += reservation
        return ticket

    def offer(self, occurrence, capture_ts, frame, stamps, *, packet_limit, now):
        self._check_owner()
        if self.closed:
            return Refused("run closed")
        if not all(map(math.isfinite, [capture_ts, now])) or packet_limit <= 0:
            return Refused("invalid admission")
        if occurrence <= self._last_occurrence:
            return Refused("nonmonotonic occurrence")
        if self._last_capture is not None and capture_ts < self._last_capture:
            return Refused("nonmonotonic capture time")
        if self.frontier is not None and capture_ts <= self.frontier:
            return Refused("offer predates declared frontier")
        try:
            encoded = json.dumps(
                stamps, separators=(",", ":"), allow_nan=False
            ).encode()
        except (TypeError, ValueError):
            return Refused("invalid stamp")
        if len(encoded) > self.max_stamp_bytes:
            return Refused("stamp byte limit")
        source = Source(occurrence, float(capture_ts), frame, encoded)
        result = self._append(source, packet_limit, now)
        if isinstance(result, Ticket):
            self._last_occurrence = occurrence
            self._last_capture = capture_ts
        return result

    def resolve(self, ticket, kind, *, retained=None, reason=""):
        self._check_owner()
        node = self._nodes.get(ticket)
        if node is None or node.state != "offered":
            return False
        if kind not in ("selected", "coalesced", "suppressed"):
            raise ValueError("explicit selection disposition required")
        if kind != "selected" and not reason:
            raise ValueError("intentional selection reason required")
        if kind == "coalesced" and retained is None:
            raise ValueError("retained picture association required")
        node.state = kind
        node.retained = retained
        node.reason = reason
        self._advance_selection()
        return True

    def _advance_selection(self):
        while self._selection:
            node = self._nodes[self._selection[0]]
            if node.state == "offered":
                return
            self._selection.popleft()
            if node.state == "selected":
                node.pts = self.allocate(node.event_ts)
                if self._last_selected is not None:
                    self._last_selected.end = node.pts
                node.state = "ready"
                self._last_selected = node
                if not node.repeat:
                    self._held = node
                self._encode.append(node.ticket)
            elif node.state == "coalesced":
                if self._held is None or node.retained != self._held.ticket:
                    self.fail(node.ticket, "invalid retained-picture association")
                    return
            if node.state in ("coalesced", "suppressed"):
                self._dispositions.append(node.ticket)

    def take_disposition(self):
        self._check_owner()
        # These receipts say no packet is coming. They neither publish pictures
        # nor move the media clock, and may release unused reservations while
        # an older retained image waits for its next duration boundary.
        if not self._dispositions:
            return None
        node = self._nodes[self._dispositions.popleft()]
        result = Outcome(
            node.state, node.ticket, node.source, node.pts, node.retained, node.reason
        )
        self._remove(node, head=False)
        return result

    def take_encode(self):
        self._check_owner()
        if self.fault or not self._encode:
            return None
        node = self._nodes[self._encode.popleft()]
        node.state = "encoding"
        return EncodeRequest(
            node.ticket, node.source, node.pts, node.repeat, self.encoder_duration
        )

    def complete(self, ticket, payload, *, pts, duration, keyframe):
        self._check_owner()
        node = self._nodes.get(ticket)
        if node is None or node.state != "encoding":
            return False
        if pts != node.pts:
            self.fail(ticket, "encoder PTS mismatch")
            return False
        if duration != self.encoder_duration:
            self.fail(ticket, "encoder nominal duration mismatch")
            return False
        if type(keyframe) is not bool:
            self.fail(ticket, "invalid encoder keyframe flag")
            return False
        if (
            not isinstance(payload, bytes)
            or not payload
            or len(payload) > node.packet_limit
        ):
            self.fail(ticket, "encoded packet reservation exceeded")
            return False
        node.payload = payload
        node.keyframe = keyframe
        node.state = "encoded"
        # The reservation covered the encoder's worst case (packet_limit).
        # The real packet is 30-150 KB, so return the difference now: a
        # picture waiting for the mux must not keep a 1 MiB seat that
        # refuses the next offer (round 48: three pictures in flight).
        shrunk = len(payload) + len(node.source.stamps_json)
        if shrunk < node.reservation:
            self._bytes -= node.reservation - shrunk
            node.reservation = shrunk
        return True

    def advance_frontier(self, stamp):
        self._check_owner()
        if not math.isfinite(stamp):
            raise ValueError("invalid frontier")
        if self.frontier is not None and stamp < self.frontier:
            raise ValueError("frontier moved backward")
        if self._last_capture is not None and stamp < self._last_capture:
            raise ValueError("frontier precedes admitted offer")
        if self.closed:
            raise RuntimeError("run already closed")
        self.frontier = stamp

    def heartbeat(self, stamp, *, packet_limit, now):
        self._check_owner()
        if self.closed:
            return Refused("run closed")
        if not math.isfinite(stamp):
            return Refused("invalid heartbeat time")
        if self.frontier is None or stamp > self.frontier:
            return Refused("capture frontier unproven")
        if self._held is None:
            return Refused("no retained picture")
        if any(
            n.state in ("offered", "selected", "ready", "encoding", "delivering")
            for n in self._nodes.values()
        ):
            return Refused("earlier offer unresolved")
        if (
            self._last_selected is not None
            and self.run.ticks_at(stamp) <= self._last_selected.pts
        ):
            return Refused("heartbeat does not advance")
        # Request a NEW encode of a retained GPU image. Never repeat encoded bytes.
        held = self._held.source
        result = self._append(held, packet_limit, now, True, event_ts=stamp)
        if isinstance(result, Ticket):
            self.resolve(result, "selected", retained=self._held.ticket)
        return result

    def seal(self, stamp):
        self._check_owner()
        if self.closed or not math.isfinite(stamp):
            return False
        if self.frontier is None or stamp > self.frontier:
            return False
        if self._selection:
            return False
        if self._last_selected is None:
            self.closed = True
            return True
        tick = self.run.ticks_at(stamp)
        if tick <= self._last_selected.pts:
            return False
        self._last_selected.end = tick
        self.closed = True
        return True

    def take_packet(self):
        self._check_owner()
        if not self._order:
            return None
        node = self._nodes[self._order[0]]
        if node.state == "failed":
            result = Outcome(
                node.state,
                node.ticket,
                node.source,
                node.pts,
                node.retained,
                node.reason,
            )
            self._remove(node)
            return result
        if node.state != "encoded" or node.end is None:
            return None
        node.state = "delivering"
        return PacketReady(
            node.ticket,
            node.source,
            node.pts,
            node.end - node.pts,
            node.repeat,
            node.payload,
            node.keyframe,
        )

    def ack_muxed(self, ticket):
        self._check_owner()
        if not self._order or self._order[0] != ticket:
            raise ValueError("out-of-order mux acknowledgment")
        node = self._nodes[ticket]
        if node.state != "delivering":
            raise ValueError("packet was not offered to mux")
        result = Outcome("muxed", node.ticket, node.source, node.pts, node.retained)
        self._remove(node)
        return result

    def _remove(self, node, head=True):
        if head:
            assert self._order.popleft() == node.ticket
        else:
            self._order.remove(node.ticket)  # Bounded by max_count; no image traversal.
        del self._nodes[node.ticket]
        self._bytes -= node.reservation
        node.payload = None

    def fail(self, ticket, reason):
        self._check_owner()
        if ticket not in self._nodes:
            return False
        self.abort(reason)
        return True

    def abort(self, reason):
        """End a run even when failure occurs between native offers."""
        self._check_owner()
        if not reason:
            raise ValueError("failure reason required")
        self.fault = reason
        self.closed = True
        self._encode.clear()
        self._selection.clear()
        self._dispositions.clear()
        # A lost reference picture invalidates pending dependent packets. End this
        # run explicitly; do not bridge it with a fabricated last-picture hold.
        for node in self._nodes.values():
            node.state = "failed"
            node.reason = reason
            node.payload = None
        return True

    def expire(self, now):
        self._check_owner()
        for ticket in self._order:
            node = self._nodes[ticket]
            if (
                node.state in ("offered", "selected", "ready", "encoding", "delivering")
                and now - node.offered_at > self.max_age
            ):
                self.fail(ticket, "pending age limit")
                return True
        return False
