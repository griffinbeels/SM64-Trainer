"""Channel samples/stamps enter the existing media and input interpretation.

This adapter only runs on the media worker. It never reads live emulator RAM,
downloads a full GPU image, or uses worker arrival time as picture time.
"""

from dataclasses import dataclass
import math
import threading

from sm64_events.replay import gpuchannel as G
from sm64_events.replay.pixels import SampledPicture
from sm64_events.replay.pluginsource import decode_stamp, table_for


@dataclass(frozen=True, slots=True)
class StampRows:
    table: tuple[bytes, ...]
    vi_origin: int
    list_qpc: int
    present_qpc: int
    lists_since: int


@dataclass(frozen=True, slots=True)
class CapturedOffer:
    picture: SampledPicture
    stamp: object
    capture_ts: float


class OfferDecoder:
    def __init__(self, header, layout, clock):
        # Channel already validates its wire. Pin another immutable copy here so
        # a caller cannot change the address interpretation after first decode.
        self.header = G.Header.from_buffer_copy(bytes(header))
        G.validate_header(self.header, self.header.total_bytes)
        self.table = tuple(table_for(layout))
        expected = tuple((offset, size) for _, offset, size in self.table)
        actual = tuple((row.offset, row.length) for row in self.header.table)[
            : self.header.table_count
        ]
        if actual != expected:
            raise G.ProtocolError("channel table differs from the requested ROM layout")
        self.layout, self.clock = layout, clock
        self.namespace = (
            f"gpu:{header.nonce_hi:016x}{header.nonce_lo:016x}:"
            f"{header.epoch}:{header.generation}"
        )

    def timestamp(self, boundary_qpc):
        if type(boundary_qpc) is not int or not 0 <= boundary_qpc < 2**63:
            raise G.ProtocolError("invalid source boundary QPC")
        ticks = boundary_qpc * 10_000_000 // self.header.qpc_frequency
        return self.clock.utc_of(ticks).timestamp()

    def decode(self, offer):
        if (
            offer.outcome != 1
        ):  # RB_OBSERVED; retired/failed sources cannot be pictures.
            raise G.ProtocolError("offer has no observed source picture")
        if (offer.width, offer.height) != (self.header.width, self.header.height):
            raise G.ProtocolError("source geometry changed within a channel")
        if not 0 <= offer.list_qpc <= offer.boundary_qpc:
            raise G.ProtocolError("source stamp follows its picture")
        lengths = offer.lengths
        if (
            len(lengths) != 16
            or any(type(n) is not int or n < 0 for n in lengths)
            or any(lengths[len(self.table) :])
            or sum(lengths) != len(offer.stamp_bytes)
            or type(offer.stamp_bytes) is not bytes
        ):
            raise G.ProtocolError("malformed packed source stamp")
        rows, cursor = [], 0
        for length, (_, _, expected) in zip(
            lengths[: len(self.table)], self.table, strict=True
        ):
            # Native guarded reads are complete or absent. Missing controller
            # or timer bytes stay unknown and do not shift later packed rows.
            if length not in (0, expected):
                raise G.ProtocolError("partial source stamp row")
            rows.append(offer.stamp_bytes[cursor : cursor + length])
            cursor += length
        value = StampRows(
            tuple(rows),
            offer.vi_origin,
            offer.list_qpc,
            offer.boundary_qpc,
            offer.lists_since,
        )
        return CapturedOffer(
            SampledPicture(offer.width, offer.height, offer.sample, 8),
            decode_stamp(value, self.table, self.layout),
            self.timestamp(offer.boundary_qpc),
        )


class ChannelSelection:
    """Translate each offer/disposition once; GPU custody belongs elsewhere."""

    def __init__(self, client, decoder, media):
        if media.namespace != decoder.namespace:
            raise ValueError("media belongs to another source session")
        self.owner = threading.current_thread()
        self.client, self.decoder, self.media = client, decoder, media
        self.token = self.occurrence = self.boundary = 0
        self.frontier_qpc = 0

    def _check(self):
        if threading.current_thread() is not self.owner:
            raise RuntimeError("channel selection belongs to its media worker")

    def handle(self, offer, *, now):
        self._check()
        try:
            if (
                offer.token != self.token + 1
                or offer.occurrence <= self.occurrence
                or offer.boundary_qpc < max(self.boundary, self.frontier_qpc)
            ):
                raise G.ProtocolError("source offers are not in certified order")
            captured = self.decoder.decode(offer)
            decision = self.media.offer(
                captured.picture,
                captured.stamp,
                occurrence=offer.occurrence,
                capture_ts=captured.capture_ts,
                now=now,
            )
            if decision.kind == "selected":
                request = decision.request
                self.client.reply(
                    offer,
                    G.SELECTED,
                    encode_serial=request.ticket.serial,
                    pts=request.pts,
                    nominal_duration=request.encoder_duration,
                    force_idr=decision.force_idr,
                )
            elif decision.kind == "coalesced":
                self.client.reply(
                    offer,
                    G.COALESCED,
                    retained_serial=decision.retained.serial,
                    retained_occurrence=decision.retained_occurrence,
                )
            else:
                self.client.reply(offer, G.FAILED, reason=1)
                raise RuntimeError(decision.reason)
            self.token, self.occurrence = offer.token, offer.occurrence
            self.boundary = offer.boundary_qpc
            return decision
        except Exception as exc:
            self.media.abort(str(exc))
            raise

    def advance_frontier(self):
        self._check()
        try:
            return self._advance_frontier()
        except Exception as exc:
            self.media.abort(str(exc))
            raise

    def _advance_frontier(self):
        # Client.frontier withholds a native certificate until every earlier
        # published offer is dispositioned. Arrival time is never a substitute.
        certificate = self.client.frontier()
        if certificate is None:
            return None
        qpc, occurrence, token = certificate
        if qpc < self.frontier_qpc or token > self.token or occurrence < 0:
            self.media.abort("invalid native frontier certificate")
            raise G.ProtocolError("invalid native frontier certificate")
        if qpc < self.boundary or occurrence < self.occurrence:
            return None  # A newer offer may arrive after the last status update.
        # CaptureClock intentionally keeps the established datetime microsecond
        # rounding. A later QPC can map to that SAME UTC value. Never certify the
        # rounded boundary inclusively or the next real picture will be rejected.
        stamp = math.nextafter(self.decoder.timestamp(qpc), -math.inf)
        if stamp < self.decoder.timestamp(self.boundary):
            return None
        self.media.frontier(stamp)
        self.frontier_qpc = qpc
        return stamp
