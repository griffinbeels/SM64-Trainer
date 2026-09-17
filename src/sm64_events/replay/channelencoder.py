"""Bounded media-worker channel -> isolated encoder -> GpuMedia connection.

This owner never touches GPU objects, pipes, audio pacing or a recorder lease.
The runtime supplies source capture control, explicit options and its worker tick.
"""

from collections import deque
from dataclasses import dataclass
import math
from sm64_events.replay.gpuprocess.process_protocol import canonical_command
import threading
import time


@dataclass
class Pending:
    decision: object
    created: float
    bridge: object = None
    admitted: bool = False
    completed: bool = False


class ChannelEncoder:
    def __init__(self, selection, controller, *, max_pending, max_age,
                 defer_disposal=False):
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError("bounded pending count required")
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("positive pending age required")
        self.owner = threading.current_thread()
        self.selection, self.control = selection, controller
        self.channel, self.media = selection.client, selection.media
        self.max_pending, self.max_age = max_pending, max_age
        self.defer_disposal = defer_disposal
        self._offers = deque()
        self._queue = deque()
        self._pending = {}
        self._slots = {}
        self._inflight = None
        self._retained = None
        self._last_serial = 0
        self._last_now = None
        self._helper_status = None
        self.started = False
        self.opened = False
        self.fault = None
        self.failure_custody = None
        self.frontier = None

    def _check(self):
        if threading.current_thread() is not self.owner:
            raise RuntimeError("channel encoder belongs to its media worker")
        if self.fault:
            raise RuntimeError(self.fault)

    def abort(self, reason):
        if threading.current_thread() is not self.owner:
            raise RuntimeError("channel encoder abort belongs to its media worker")
        if self.fault:
            return
        self.failure_custody = dict(self._custody_status(), observed_unix_s=time.time())
        self.fault = str(reason)[:512] or "channel encoder failed"
        try:
            if not self.defer_disposal:
                self.control.stop(self.fault)
        finally:
            # No receipts are invented when an epoch dies; the outer source owner
            # must retire its matching capture/channel epoch as well.
            # The session owner revokes capture and asks native Close to return
            # GPU keys before destroying the helper or doing slow mux cleanup.
            # Controller protocol/watchdog failures still dispose independently.
            if not self.defer_disposal:
                self.media.abort(self.fault)
            self._retained = None
            self._offers.clear()
            self._queue.clear()
            self._pending.clear()
            self._slots.clear()

    def _enqueue(self, command, pending=None):
        if self._inflight is not None:
            raise RuntimeError("helper command already pending")
        command = dict(command)
        result = self.control.enqueue(command)
        if type(result) is not int:
            return False
        if not 0 < result < 2**64:
            raise RuntimeError("invalid helper request identity")
        self._inflight = (result, command["op"], pending)
        return True

    def _validate_open(self, open_command):
        h = self.selection.decoder.header
        options = open_command.get("options", {})
        if (
            open_command.get("op") != "Open"
            or tuple(open_command.get("adapter_luid", ())) != (h.luid_high, h.luid_low)
            or (options.get("width"), options.get("height"))
            != (h.even_width, h.even_height)
            or options.get("input_format") != h.format
        ):
            raise ValueError("Open must match pinned source shape/adapter")
        names = []
        for raw in h.texture_names:
            values = list(raw)
            end = values.index(0)
            names.append(
                b"".join(int(n).to_bytes(2, "little") for n in values[:end]).decode(
                    "utf-16-le"
                )
            )
        if open_command.get("names") != names:
            raise ValueError("Open texture names differ from pinned channel")
        if options.get("max_packet_bytes") != self.media.packet_limit:
            raise ValueError("encoder and media packet bounds differ")

    def start(self, open_command):
        self._check()
        if self.started:
            raise RuntimeError("encoder already started")
        self._validate_open(open_command)
        if not self._enqueue(open_command):
            return False
        self.started = True
        return True

    def attach_open(self, open_command, request_id, reply, *, initial_offers, now):
        # Runtime may Open from the immutable header before a first image/UTC
        # origin exists, publish encoder_ready, then create GpuMedia from the first
        # real offer. Adopt that exact completed Open; never initialize twice.
        self._check()
        if self.started:
            raise RuntimeError("encoder already started")
        try:
            self._validate_open(open_command)
            state = self._helper_status = self.control.status()
            metadata = reply.metadata
            native = metadata.get("status") or {}
            retained = metadata.get("retained") or {}
            expected = canonical_command(open_command)
            h = self.selection.decoder.header
            if (
                getattr(reply, "command_json", None) != expected
                or type(request_id) is not int
                or request_id != 1
                or reply.request_id != request_id
                or metadata.get("request_id") != request_id
                or metadata.get("nonce") != self.control.nonce
                or state.get("next_request_id") != 2
                or state.get("pending_count") != 0
                or state.get("inflight") is not None
                or state.get("fault")
                or state.get("done")
                or metadata.get("result") != 0
                or metadata.get("error") is not None
                or metadata.get("worker_disposal_required") is not False
                or metadata.get("packet") is not None
                or reply.payload
                or metadata.get("key_returns")
                or (native.get("adapter_high"), native.get("adapter_low"))
                != (h.luid_high, h.luid_low)
                or native.get("state") != 1
                or any(
                    native.get(n) != 0
                    for n in (
                        "submitted",
                        "completed",
                        "delivered",
                        "acquired",
                        "released",
                        "held_mask",
                        "pending_mask",
                    )
                )
                or retained.get("valid") != 0
                or retained.get("selected_serial") != 0
            ):
                raise RuntimeError(
                    "attachment requires sole completed initial Open from this helper"
                )
            offers = tuple(initial_offers)
            if len(offers) > 8 or not math.isfinite(now):
                raise ValueError("bounded initial offers/age clock required")
            self._offers.extend((offer, now) for offer in offers)
            self._last_now = now
            self._retained = retained.copy()
            self.started = self.opened = True
        except Exception as exc:
            self.abort(str(exc))
            raise

    def _remember(self, decision, now):
        if decision.kind != "selected":
            return
        request = decision.request
        if (
            request is None
            or request.ticket != decision.ticket
            or request.ticket.serial <= self._last_serial
            or request.source.occurrence != decision.occurrence
        ):
            raise RuntimeError("selected request identity mismatch")
        if len(self._pending) >= self.max_pending:
            raise RuntimeError("pending encode bound exceeded")
        self._last_serial = request.ticket.serial
        item = Pending(decision, now)
        self._pending[request.ticket.serial] = item
        self._queue.append(item)

    def _scan_bridges(self):
        for bridge in self.channel.bridges():
            item = self._pending.get(bridge.encode_serial)
            if item is None:
                raise RuntimeError("bridge has no exact pending request")
            oldest = next(
                (
                    p
                    for p in self._queue
                    if not p.decision.request.repeat and p.bridge is None
                ),
                None,
            )
            if oldest is not item:
                raise RuntimeError("bridge passed oldest unmatched selected request")
            request = item.decision.request
            if (
                request.repeat
                or item.bridge is not None
                or bridge.texture_index not in (0, 1)
                or bridge.texture_index in self._slots
                or (
                    bridge.occurrence,
                    bridge.encode_serial,
                    bridge.pts,
                    bridge.nominal_duration,
                    bridge.force_idr,
                )
                != (
                    request.source.occurrence,
                    request.ticket.serial,
                    request.pts,
                    request.encoder_duration,
                    item.decision.force_idr,
                )
            ):
                raise RuntimeError("bridge differs from exact selected request")
            item.bridge = bridge
            self._slots[bridge.texture_index] = item

    def _packet(self, item, metadata, payload):
        request = item.decision.request
        packet = metadata.get("packet")
        if (
            not isinstance(packet, dict)
            or not payload
            or (
                packet.get("serial"),
                packet.get("pts"),
                packet.get("encoder_duration"),
                packet.get("bytes"),
            )
            != (
                request.ticket.serial,
                request.pts,
                request.encoder_duration,
                len(payload),
            )
            or type(packet.get("keyframe")) is not bool
        ):
            raise RuntimeError("helper packet differs from pending request")
        if not self._queue or self._queue[0] is not item:
            raise RuntimeError("encoded packet passed earlier pending request")
        self.media.complete(
            request.ticket,
            payload,
            pts=request.pts,
            duration=request.encoder_duration,
            keyframe=packet["keyframe"],
        )
        item.admitted = item.completed = True
        self._queue.popleft()
        if request.repeat:
            del self._pending[request.ticket.serial]

    def _custody(self, receipts):
        for receipt in receipts:
            slot = receipt.get("slot")
            item = self._slots.get(slot)
            if item is None or not item.admitted or not item.completed:
                raise RuntimeError("custody receipt lacks admitted completed bridge")
            bridge = item.bridge
            if receipt != {
                "slot": bridge.texture_index,
                "bridge_token": bridge.token,
                "serial": bridge.encode_serial,
            }:
                raise RuntimeError("custody receipt differs from exact bridge")
            # Metadata publication follows both accepted compressed output and the
            # separately proven GPU key return. Never acknowledge on TIMEOUT alone.
            self.channel.acknowledge_metadata(bridge)
            del self._slots[slot]
            del self._pending[bridge.encode_serial]

    def _reply(self):
        reply = self.control.take_result()
        if reply is None:
            return
        if self._inflight is None or reply.request_id != self._inflight[0]:
            raise RuntimeError("helper reply does not match owned command")
        _, op, item = self._inflight
        self._inflight = None
        metadata = reply.metadata
        code = metadata["result"]
        if metadata["error"] or metadata["worker_disposal_required"]:
            raise RuntimeError(metadata["error"] or "native encoder custody uncertain")
        if (
            code not in (0, 9, 10)
            or (op in ("Open", "Repeat") and code != 0)
            or (op == "Poll" and code == 9)
        ):
            raise RuntimeError("unexpected helper operation result")
        self._retained = metadata["retained"]
        if op in ("Submit", "Repeat") and code == 0:
            expected = (
                item.decision.retained.serial
                if item.decision.request.repeat
                else item.decision.ticket.serial
            )
            if (
                not self._retained
                or not self._retained["valid"]
                or self._retained["selected_serial"] != expected
            ):
                raise RuntimeError(
                    "encoder retained source differs from selected request"
                )
        if op == "Open":
            self.opened = True
        elif op in ("Submit", "Repeat") and code == 0:
            self._packet(item, metadata, reply.payload)
        self._custody(metadata["key_returns"])

    def _dispatch(self):
        if self._inflight or not self.opened:
            return
        if self._queue:
            item = self._queue[0]
            request = item.decision.request
            common = dict(
                serial=request.ticket.serial,
                pts=request.pts,
                encoder_duration=request.encoder_duration,
                force_idr=item.decision.force_idr,
            )
            if request.repeat:
                if (
                    not self._retained
                    or not self._retained["valid"]
                    or self._retained["selected_serial"]
                    != item.decision.retained.serial
                ):
                    raise RuntimeError("held encoder image unavailable for heartbeat")
                self._enqueue(
                    dict(
                        op="Repeat", generation=self._retained["generation"], **common
                    ),
                    item,
                )
                return
            if item.bridge is not None:
                self._enqueue(
                    dict(
                        op="Submit",
                        slot=item.bridge.texture_index,
                        bridge_token=item.bridge.token,
                        **common,
                    ),
                    item,
                )
                return
        if any(item.admitted for item in self._slots.values()):
            self._enqueue(dict(op="Poll"))

    def pump(self, *, now):
        self._check()
        try:
            if not math.isfinite(now) or (
                self._last_now is not None and now < self._last_now
            ):
                raise ValueError("monotone worker age clock required")
            self._last_now = now
            state = self._helper_status = self.control.status()
            if state["fault"]:
                raise RuntimeError(state["fault"])
            # Expired records cannot be erased by a late packet/key receipt.
            for item in self._pending.values():
                if now - item.created > self.max_age:
                    raise RuntimeError("selected bridge/packet age deadline")
            if self._offers and now - self._offers[0][1] > self.max_age:
                raise RuntimeError("unresolved source offer age deadline")
            self._reply()
            if self.opened:
                if not self._offers:
                    offers = self.channel.offers()
                    if len(offers) > 8:
                        raise RuntimeError("native offer count bound exceeded")
                    self._offers.extend((offer, now) for offer in offers)
                while (
                    self._offers
                    and len(self._pending) < self.max_pending
                    and self.media.can_offer
                ):
                    offer, created = self._offers.popleft()
                    self._remember(self.selection.handle(offer, now=now), created)
                self._scan_bridges()
                if not self._offers:
                    self.frontier = self.selection.advance_frontier()
            self._dispatch()
            return self.status(refresh=False)
        except Exception as exc:
            self.abort(str(exc))
            raise

    def heartbeat(self, capture_ts, *, now):
        self._check()
        if (
            not self.opened
            or self._offers
            or self._pending
            or self._inflight
            or self.frontier is None
            or capture_ts > self.frontier
        ):
            return None
        try:
            if (
                not self._retained
                or not self._retained["valid"]
                or self.media.held_ticket is None
            ):
                return None
            if self._retained["selected_serial"] != self.media.held_ticket.serial:
                raise RuntimeError("heartbeat retained selection mismatch")
            decision = self.media.heartbeat(capture_ts, now=now)
            if getattr(decision, "kind", None) != "selected":
                return None
            self._remember(decision, now)
            self._dispatch()
            return decision
        except Exception as exc:
            self.abort(str(exc))
            raise

    def _custody_status(self):
        """Only owned scalars: safe to latch before abort clears these queues."""
        return dict(pending=len(self._pending), offers=len(self._offers),
                    queued=len(self._queue), bridges=len(self._slots),
                    inflight=None if self._inflight is None else self._inflight[0],
                    operation=None if self._inflight is None else self._inflight[1])

    def status(self, *, refresh=True):
        """Reuse a pump's helper sample for reporting, never for disposal proof."""
        if threading.current_thread() is not self.owner:
            raise RuntimeError("channel encoder status belongs to its media worker")
        if refresh or self._helper_status is None:
            self._helper_status = self.control.status()
        helper = self._helper_status
        disposal = helper.get("disposal") or {}
        disposed = bool(
            helper.get("done")
            and disposal.get("empty")
            and disposal.get("handle_closed")
            and disposal.get("active_processes") == 0
        )
        return dict(
            opened=self.opened,
            **self._custody_status(),
            failure_custody=None if self.failure_custody is None else dict(self.failure_custody),
            fault=self.fault,
            helper_disposed=disposed,
        )
