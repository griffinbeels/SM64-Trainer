"""Explicit budgets for the renderer GPU path; quality stays in replay.config."""

from dataclasses import dataclass
import logging

from sm64_events.core.timefmt import GAME_FPS
from sm64_events.memory.addresses import RDRAM_FULL_SIZE
from sm64_events.replay.gpuencoder_abi import CODEC_AV1, CODEC_H264
from sm64_events.replay.gpurequest import RequestLimits
from sm64_events.replay.gpuencoder_options import from_replay_config
from sm64_events.replay.gpuprocess.process_controller import Limits

log = logging.getLogger("sm64.replay")

# WHICH CODEC THE RECORDER WRITES, per graphics adapter, and the one owner of
# that question. AV1 is worth 32% of H.264's bytes at equal quality and encodes
# FASTER (measured 2026-09-20), so Save publishes already-small bytes and the
# compression pass never runs for that replay -- but only RTX 40-series and
# newer have an AV1 encoder at all.
#
# The answer is not guessed from a model name and not borrowed from ffmpeg's
# NVENC: it is asked of the exact adapter, through the exact encoder the
# recorder will use, by opening an AV1 session. An adapter without one refuses
# by codec (`Result.CODEC`), which is typed apart from every real fault, and
# the refusal is remembered so the extra open happens once per adapter per
# process rather than once per capture.
_ADAPTER_CODECS: dict[tuple[int, int], int] = {}


def recording_codec(luid: tuple[int, int]) -> int:
    """The codec to ask this adapter for. AV1 until it says otherwise."""
    return _ADAPTER_CODECS.get(tuple(luid), CODEC_AV1)


def note_codec_unavailable(luid: tuple[int, int], codec: int) -> bool:
    """Record a by-codec refusal. True when another codec is worth trying."""
    if codec != CODEC_AV1:
        return False
    _ADAPTER_CODECS[tuple(luid)] = CODEC_H264
    log.info("replay: this GPU has no AV1 encoder; recording H.264 and the "
             "compression pass keeps shrinking saved replays")
    return True


def forget_adapter_codecs() -> None:
    """Tests only: the memo is a per-process cache, never persisted state."""
    _ADAPTER_CODECS.clear()


@dataclass(frozen=True)
class GpuSettings:
    poll_s: float = 0.004
    startup_s: float = 5.0
    close_s: float = 4.0
    max_age: float = 2.0
    slots: int = 8
    packet_bytes: int = 1 << 20
    # Every native slot can be offered and awaiting its encode at once;
    # each holds a packet_bytes seat until its packet returns. Four MiB
    # admitted three of the eight slots (round 48). The native request page
    # caps this at slots x packet_bytes exactly (gpurequest.validate_limits),
    # and the shipped values must satisfy that cap: 9 MiB refused every
    # request live on 2026-09-16.
    pending_bytes: int = 8 << 20
    pcm_bytes: int = 1 << 20
    pcm_blocks: int = 256
    lead_ticks: int = 9000
    heartbeat_s: float = 1.0

    def request(self):
        return RequestLimits(
            self.slots,
            256 << 20,
            RDRAM_FULL_SIZE,
            64 << 20,
            self.slots,
            self.packet_bytes,
            self.pending_bytes,
            self.pcm_bytes,
            self.pcm_blocks,
            int(self.max_age * 1000),
            90000 // GAME_FPS,
        )

    def helper(self):
        return Limits(
            max_count=3,
            max_bytes=4 * self.packet_bytes,
            max_json=32768,
            max_packet=self.packet_bytes,
            max_age=self.startup_s,
            call_timeout=2.0,
            startup_timeout=self.startup_s,
            watchdog_interval=0.05,
            stderr_bytes=8192,
            shutdown_timeout=1.0,
        )

    def encoder(self, header, cfg, *, nominal_rate, codec=CODEC_H264):
        # Structural values mirror the picture feed: no reordering, closed GOP
        # plus time-forced IDRs. Nominal rate is separate from actual VFR PTS.
        return from_replay_config(
            codec=codec,
            width=header.even_width,
            height=header.even_height,
            nominal_fps_num=nominal_rate,
            nominal_fps_den=1,
            b_frames=0,
            gop_frames=int(GAME_FPS * cfg.segment_s),
            initial_qp_p=26,
            initial_qp_i=21,
            initial_qp_b=34,
            idr_interval_ticks=round(cfg.segment_s * 90000),
            input_format=header.format,
            signal_color=1,
            full_range=0,
            matrix=5,
            primaries=2,
            transfer=2,
            max_packet_bytes=self.packet_bytes,
        )
