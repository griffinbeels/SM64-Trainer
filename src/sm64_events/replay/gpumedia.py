"""GPU recording media owner: selection, encoded order, PCM and exact feed IDs.

No OS/process/graphics calls belong here. The native worker owns images and
presents immutable samples; the helper owns NVENC. This worker reserves capacity
before selecting, sends explicit decisions, and publishes accepted compressed
packets through the existing mux. Runtime IPC and audio handoffs remain bounded
separately; none of these operations can execute in an emulator callback.
"""

from dataclasses import dataclass
import threading

from sm64_events.replay.packetorder import (
    ExistingTickAllocator,
    OrderedPackets,
    PacketReady,
    Refused,
    Ticket,
)
from sm64_events.replay.packetmux import EncodedPicture
from sm64_events.replay.pcmbuffer import PcmBuffer


@dataclass(frozen=True, slots=True)
class MediaDecision:
    kind: str
    occurrence: int
    ticket: Ticket
    request: object = None
    retained: Ticket | None = None
    retained_occurrence: int = 0
    reason: str = ""
    force_idr: bool = False


class GpuMedia:
    def __init__(
        self,
        run,
        ledger,
        mux,
        *,
        source_namespace,
        encoder_duration,
        packet_limit,
        pending_count,
        pending_bytes,
        max_age,
        pcm_bytes,
        pcm_blocks,
        lead_ticks,
        timings=None,
        publish_picture=None,
    ):
        if type(source_namespace) is not str or not 0 < len(source_namespace) <= 130:
            raise ValueError("bounded native session identity required")
        if type(packet_limit) is not int or packet_limit <= 0:
            raise ValueError("positive packet limit required")
        self.owner = threading.get_ident()
        self.run, self.ledger, self.mux = run, ledger, mux
        self.timings = timings
        self.publish_picture = publish_picture
        self.namespace, self.packet_limit = source_namespace, packet_limit
        self._first_requested = False
        self.lead_ticks = lead_ticks
        self.order = OrderedPackets(
            run,
            ExistingTickAllocator(run),
            encoder_duration=encoder_duration,
            max_count=pending_count,
            max_bytes=pending_bytes,
            max_age=max_age,
        )
        self.pcm = PcmBuffer(
            run,
            rate=mux.rate,
            max_bytes=pcm_bytes,
            max_blocks=pcm_blocks,
            max_age=max_age,
            lead_ticks=0,
        )
        self.held_id = self.held_ticket = None
        self.held_occurrence = 0
        self.video_end = None
        self.audio_end = 0
        self.pending_packet = None
        self._delivered = 0
        self._forced = {}
        self.fault = None
        self.closed = False
        ledger.restart_selection()

    @property
    def delivered(self):
        # A bounded handoff can release source ownership before the sink writes.
        # Health receipts count completed sink operations, never admissions.
        return self.mux.delivered if self.publish_picture else self._delivered

    def _open(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("GPU media belongs to its worker")
        if self.closed or self.fault:
            raise RuntimeError("GPU media run closed")

    @property
    def can_offer(self):
        self._open()
        return self.order.can_offer(self.packet_limit)

    def _request(self):
        request = self.order.take_encode()
        if request is None:
            raise RuntimeError("selected native picture has no encode request")
        # Native BridgeEncoder owns the exact periodic n_forced expression.
        # A second last-IDR+interval clock here creates extra IDRs after jitter.
        force = not self._first_requested
        self._first_requested = True
        self._forced[request.ticket] = force
        return request, force

    def offer(self, sample, stamp, *, occurrence, capture_ts, now):
        self._open()
        source_id = f"{self.namespace}:{occurrence}"
        extras = stamp.extras() if stamp is not None else {"exact": False}
        extras["source_id"] = source_id
        frame = stamp.frame if stamp is not None else None
        ticket = self.order.offer(
            occurrence,
            capture_ts,
            frame,
            extras,
            packet_limit=self.packet_limit,
            now=now,
        )
        if isinstance(ticket, Refused):
            self.abort(ticket.reason)
            raise RuntimeError(ticket.reason)
        selected = self.ledger.observe_result(sample, capture_ts, frame, extras)
        if selected.kind == "failed":
            self.abort(selected.reason)
            return MediaDecision("failed", occurrence, ticket, reason=selected.reason)
        if selected.kind == "coalesced":
            if self.held_id is None or selected.source_id != self.held_id:
                self.abort("selector retained source mismatch")
                return MediaDecision("failed", occurrence, ticket, reason=self.fault)
            self.order.resolve(
                ticket, "coalesced", retained=self.held_ticket, reason=selected.reason
            )
            receipt = self.order.take_disposition()
            if receipt is None or receipt.ticket != ticket:
                self.abort("selection disposition order mismatch")
                raise RuntimeError(self.fault)
            return MediaDecision(
                "coalesced",
                occurrence,
                ticket,
                retained=self.held_ticket,
                retained_occurrence=self.held_occurrence,
                reason=selected.reason,
            )
        if selected.source_id != source_id:
            self.abort("selector changed native source identity")
            return MediaDecision("failed", occurrence, ticket, reason=self.fault)
        self.order.resolve(ticket, "selected")
        request, force = self._request()
        self.held_id, self.held_ticket = source_id, ticket
        self.held_occurrence = occurrence
        return MediaDecision(
            "selected", occurrence, ticket, request=request, force_idr=force
        )

    def complete(self, ticket, data, *, pts, duration, keyframe):
        self._open()
        if self._forced.get(ticket) and not keyframe:
            self.abort("encoder omitted requested IDR")
            raise RuntimeError(self.fault)
        if not self.order.complete(
            ticket, data, pts=pts, duration=duration, keyframe=keyframe
        ):
            self.abort(self.order.fault or "stale native packet")
            raise RuntimeError(self.fault)
        self._forced.pop(ticket, None)
        self.drain()

    def frontier(self, capture_ts):
        """Only the native transaction/offer watermark may supply this time."""
        self._open()
        self.order.advance_frontier(capture_ts)

    def heartbeat(self, capture_ts, *, now):
        self._open()
        result = self.order.heartbeat(
            capture_ts, packet_limit=self.packet_limit, now=now
        )
        if isinstance(result, Refused):
            return result
        request, force = self._request()
        return MediaDecision(
            "selected",
            request.source.occurrence,
            result,
            request=request,
            retained=self.held_ticket,
            retained_occurrence=self.held_occurrence,
            force_idr=force,
        )

    def audio(self, data, first_us, *, now):
        self._open()
        try:
            self.pcm.append(data, first_us, now=now)
            self.drain()
        except Exception as exc:
            self.abort(str(exc))
            raise

    def _write_audio(self, data, first_us):
        self.mux.write_pcm(data, first_us)
        numerator = (first_us - self.pcm.origin_us) * self.pcm.rate + len(
            data
        ) // 4 * 1_000_000
        self.audio_end = max(
            self.audio_end, numerator * 90000 // (self.pcm.rate * 1_000_000)
        )

    def drain(self, *, final=False):
        self._open()
        try:
            while True:
                if self.video_end is not None:
                    self.pcm.drain(self.video_end, self._write_audio)
                if self.pending_packet is None:
                    self.pending_packet = self.order.take_packet()
                packet = self.pending_packet
                if packet is None:
                    break
                if not isinstance(packet, PacketReady):
                    raise RuntimeError(packet.reason)
                # Bound video lead too: absent PCM cannot make libav accumulate
                # an entire practice session. One long VFR hold is one packet.
                if (
                    not final
                    and self.video_end is not None
                    and packet.pts > self.audio_end + self.lead_ticks
                ):
                    break
                picture = EncodedPicture(
                        packet.source.occurrence,
                        packet.pts,
                        packet.duration,
                        packet.keyframe,
                        packet.payload,
                    )
                source_id = packet.source.stamps().get("source_id")
                if not source_id:
                    raise RuntimeError("encoded packet lost native source identity")
                row_ts = None if packet.repeat else packet.source.capture_ts
                wrote_at = self.run.origin_ts + packet.pts / 90000
                feed = dict(media_run=self.run, pts=packet.pts, source_id=source_id)
                if self.publish_picture is not None:
                    self.publish_picture(picture, row_ts, wrote_at,
                                         repeat=packet.repeat, **feed)
                else:
                    self.mux.write_video(picture)
                    self._mark_fed(row_ts, wrote_at, **feed)
                # Payload ownership moved atomically to the bounded sink. The
                # source no longer needs its GPU picture to finish this packet.
                self.order.ack_muxed(packet.ticket)
                if not packet.repeat and self.publish_picture is None:
                    self._delivered += 1
                self.video_end = packet.pts + packet.duration
                self.pending_packet = None
        except Exception as exc:
            self.abort(str(exc))
            raise

    def _mark_fed(self, *args, **kwargs):
        if self.timings is not None:
            return self.timings.measure("ledger_feed", self.ledger.mark_fed, *args, **kwargs)
        return self.ledger.mark_fed(*args, **kwargs)

    def check_age(self, now):
        self._open()
        try:
            self.pcm.check_age(now)
            if self.order.expire(now):
                raise RuntimeError(self.order.fault)
        except Exception as exc:
            self.abort(str(exc))
            raise

    def seal(self, capture_ts):
        self._open()
        if not self.order.seal(capture_ts):
            return False
        # Packets can still be completing. Caller must then call finish().
        return True

    def finish(self, *, audio_drained=False):
        """Finish only after the producer/pacer handoff drains through the seal.

        The runtime must stop audio production, drain queued in-bound PCM and
        complete its existing pacer through the chosen final frontier before
        supplying audio_drained=True. Video completion alone cannot certify it.
        """
        self._open()
        if audio_drained is not True:
            return False
        if not self.order.closed:
            raise RuntimeError("GPU media end has not been sealed")
        try:
            self.drain(final=True)
            if self.order.pending_count:
                return False
            if self.video_end is not None:
                self.pcm.drain(self.video_end, self._write_audio, final=True)
            else:
                self.pcm.abort()
            self.mux.close()
            self.closed = True
            return True
        except Exception as exc:
            self.abort(str(exc))
            raise

    def abort(self, reason):
        if threading.get_ident() != self.owner:
            raise RuntimeError("GPU media belongs to its worker")
        if self.closed or self.fault:
            return
        self.fault = str(reason)[:512]
        self.order.abort(self.fault)
        self._forced.clear()
        self.pcm.abort()
        self.pending_packet = None
        try:
            self.mux.abort()
        finally:
            self.closed = True
