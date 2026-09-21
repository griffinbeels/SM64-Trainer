"""Compressed GPU video and clocked PCM into the existing fragment archive.

Synchronous media-worker component. Never call from renderer/audio callbacks.
The coordinator owns bounded pending media and ordering; this class retains no
raw video and never invokes a video encoder or decoder. A validated native
stream header describes the encoder; assigned packet intervals pass unchanged.

THE CODEC IS CARRIED, NEVER ASSUMED. The native encoder writes AV1 on a GPU
that has an AV1 encoder and H.264 everywhere else, so the mux is told which
one it is holding rather than inferring it. Both are ordered, reordering-free
and self-describing in band -- H.264 repeats SPS/PPS on every IDR, AV1 repeats
its sequence header -- which is what lets `delay_moov` build the sample
description from the first packet with no decoder in the recording path.
"""

from dataclasses import dataclass
from fractions import Fraction
import threading

import av

from sm64_events.replay.config import AUDIO_RESAMPLE_OPTIONS, fragment_mux_options
from sm64_events.replay.media import MEDIA_TIME_BASE, MediaRun
from sm64_events.replay.pcmclock import trim_to_origin


@dataclass(frozen=True)
class EncodedPicture:
    occurrence: int
    pts: int
    duration: int
    key: bool
    data: bytes


# The codecs the native encoder can produce, by their libav stream names. Both
# carry their own decoder configuration in the first key picture, so neither
# needs a probe or a decode to be muxed; anything else would.
NATIVE_CODECS = ("h264", "av1")


@dataclass(frozen=True)
class NativeFormat:
    """Pinned native format; its first key picture carries the decoder config.

    `codec` is the libav stream name ("h264" or "av1"), not the ffmpeg encoder
    name -- the encoder is a choice made once by the capture session, and what
    reaches the archive is the bitstream it produced.
    """

    codec: str
    width: int
    height: int
    nominal_rate: int

    def __post_init__(self):
        if (
            self.codec not in NATIVE_CODECS
            or any(
                type(v) is not int for v in (self.width, self.height, self.nominal_rate)
            )
            or not 2 <= self.width <= 8192
            or not 2 <= self.height <= 8192
            or self.width % 2
            or self.height % 2
            or not 1 <= self.nominal_rate <= 1000
        ):
            raise ValueError("invalid native video format")


class PacketFragmentMux:
    """One run, one calling worker, no recovery after a codec/output failure.

    Output ownership stays with the caller, which marks archive finish/abort.
    Limits apply before passing a block to libav; the media coordinator must also
    bound audio/video lead and total queued bytes, including the native encoder.
    """

    def __init__(
        self,
        output,
        template,
        run,
        *,
        audio_rate,
        audio_bitrate,
        packet_limit,
        pcm_limit,
        timings=None,
    ):
        if min(audio_rate, audio_bitrate, packet_limit, pcm_limit) <= 0:
            raise ValueError("positive packet mux limits required")
        if not isinstance(template, NativeFormat) and (
            template.codec_context.name not in NATIVE_CODECS
            or template.codec_context.has_b_frames
        ):
            raise ValueError("packet mux requires an ordered native stream "
                             "without B-frames")
        self.run, self.rate = run, audio_rate
        self.timings = timings
        self.packet_limit, self.pcm_limit = packet_limit, pcm_limit
        self.owner = threading.get_ident()
        self.last_pts = self.last_end = None
        self.cleanup_error = None
        self.video_count = self.audio_input_samples = self.audio_output_packets = 0
        self.closed = self.failed = False
        self.mux = av.open(output, "w", format="mp4", options=fragment_mux_options())
        try:
            if isinstance(template, NativeFormat):
                # No demux probe, video codec context, CPU decode or second encoder.
                # delay_moov obtains codec extradata from the first native key
                # picture: H.264's repeated SPS/PPS, or AV1's repeated sequence
                # header. Measured 2026-09-21 -- real av1_nvenc packets muxed
                # this way produce an `av01` sample entry and decode back with
                # every source tick intact.
                self.video = self.mux.add_mux_stream(
                    template.codec,
                    width=template.width,
                    height=template.height,
                    rate=template.nominal_rate,
                    time_base=MEDIA_TIME_BASE,
                )
            else:
                self.video = self.mux.add_stream_from_template(template)
            self.video.time_base = MEDIA_TIME_BASE
            self.audio = self.mux.add_stream("aac", rate=audio_rate)
            self.audio.layout = "stereo"
            self.audio.bit_rate = audio_bitrate
            self.audio.time_base = self.audio.codec_context.time_base = Fraction(
                1, audio_rate
            )
            self._prepare_audio()
        except Exception:
            self.failed = True
            self._release()
            raise

    def _prepare_audio(self):
        self.graph = av.filter.Graph()
        self.buffer = self.graph.add_abuffer(
            sample_rate=self.rate,
            format="s16",
            layout="stereo",
            time_base=Fraction(1, 1_000_000),
        )
        resample = self.graph.add("aresample", f"{self.rate}:{AUDIO_RESAMPLE_OPTIONS}")
        format_ = self.graph.add(
            "aformat",
            (f"sample_fmts=fltp:sample_rates={self.rate}:channel_layouts=stereo"),
        )
        sink = self.graph.add("abuffersink")
        self.buffer.link_to(resample)
        resample.link_to(format_)
        format_.link_to(sink)
        self.graph.configure()

    def prepare(self):
        """Open AAC without samples, header output, or choosing a media origin.

        add_stream already selects the codec's supported sample format and we
        configure rate/layout/bitrate/time base in construction. Opening only
        that context avoids start_encoding(), which also writes the mux header.
        """
        self._check_open()
        try:
            self.audio.codec_context.open(strict=False)
        except Exception:
            self.failed = True
            self._release()
            raise

    def bind_run(self, run):
        """Bind a prepared mux to the first actual source occurrence, once."""
        self._check_open()
        if self.run is not None:
            raise RuntimeError("packet mux media run is already bound")
        if not isinstance(run, MediaRun):
            raise TypeError("packet mux requires a media run")
        self.run = run

    def _check_owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("packet mux belongs to its media worker")

    def _check_open(self):
        self._check_owner()
        if self.closed or self.failed:
            raise RuntimeError("packet mux is closed or failed")

    def _check_bound(self):
        self._check_open()
        if self.run is None:
            raise RuntimeError("packet mux media run is not bound")

    def write_video(self, picture: EncodedPicture):
        if self.timings is not None:
            return self.timings.measure("mux_video", self._write_video, picture)
        return self._write_video(picture)

    def _write_video(self, picture):
        self._check_bound()
        if (
            type(picture.data) is not bytes
            or not picture.data
            or len(picture.data) > self.packet_limit
        ):
            raise ValueError("encoded picture byte limit")
        if any(
            type(value) is not int
            for value in (picture.occurrence, picture.pts, picture.duration)
        ):
            raise ValueError("integer picture identity and clock required")
        if type(picture.key) is not bool:
            raise ValueError("boolean random-access flag required")
        if (
            picture.occurrence <= 0
            or picture.pts < 0
            or picture.duration <= 0
            or picture.pts + picture.duration > (1 << 63) - 1
        ):
            raise ValueError("invalid picture interval or identity")
        if self.last_pts is not None and picture.pts <= self.last_pts:
            raise ValueError("picture PTS must be assigned distinctly upstream")
        if self.last_end is not None and picture.pts != self.last_end:
            raise ValueError("picture intervals must be contiguous within a run")
        if self.last_pts is None and not picture.key:
            raise ValueError("run must begin at an IDR")
        try:
            packet = av.Packet(picture.data)
            packet.stream = self.video
            packet.time_base = MEDIA_TIME_BASE
            packet.pts = packet.dts = picture.pts
            packet.duration = picture.duration
            packet.is_keyframe = picture.key
            self.mux.mux(packet)
        except Exception:
            self.failed = True
            raise
        self.last_pts = picture.pts
        self.last_end = picture.pts + picture.duration
        self.video_count += 1

    def write_pcm(self, pcm: bytes, first_sample_utc_us: int):
        if self.timings is not None:
            return self.timings.measure("mux_audio", self._write_pcm, pcm, first_sample_utc_us)
        return self._write_pcm(pcm, first_sample_utc_us)

    def _write_pcm(self, pcm, first_sample_utc_us):
        self._check_bound()
        if type(pcm) is not bytes or len(pcm) > self.pcm_limit:
            raise ValueError("PCM block byte limit")
        if type(first_sample_utc_us) is not int:
            raise ValueError("integer PCM source clock required")
        placed = trim_to_origin(pcm, first_sample_utc_us, self.run.origin_ts, self.rate)
        if placed is None:
            return
        data, relative = placed
        samples = len(data) // 4
        try:
            frame = av.AudioFrame(format="s16", layout="stereo", samples=samples)
            frame.planes[0].update(data)
            frame.sample_rate = self.rate
            frame.pts, frame.time_base = relative, Fraction(1, 1_000_000)
            self.buffer.push(frame)
            self._drain_filter()
        except Exception:
            self.failed = True
            raise
        self.audio_input_samples += samples

    def _drain_filter(self):
        while True:
            try:
                frame = self.graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                return
            for packet in self.audio.encode(frame):
                self.mux.mux(packet)
                self.audio_output_packets += 1

    def _release(self):
        """Attempt container cleanup once; retain secondary errors without masking the cause."""
        if self.closed:
            return
        try:
            self.mux.close()
        except Exception as exc:  # noqa: BLE001 - preserve the original error if foreign output cleanup also fails.
            self.cleanup_error = str(exc)[:512]
            self._cleanup_cause = exc
            self.failed = True
        finally:
            self.closed = True

    def close(self):
        self._check_owner()
        if self.closed:
            return
        if self.failed:
            self._release()
            raise RuntimeError("failed packet mux cannot finish a complete run")
        if self.run is None:
            # A cancelled bootstrap has no timeline to flush or publish.
            self._release()
            if self.cleanup_error is not None:
                raise RuntimeError("unbound packet mux cleanup failed") from self._cleanup_cause
            return
        try:
            self.buffer.push(None)
            self._drain_filter()
            for packet in self.audio.encode(None):
                self.mux.mux(packet)
                self.audio_output_packets += 1
        except Exception:
            self.failed = True
            raise
        finally:
            self._release()
        if self.cleanup_error is not None:
            raise RuntimeError(
                f"packet mux container close failed: {self.cleanup_error}"
            ) from self._cleanup_cause

    def abort(self):
        """Release libav without flushing new audio or declaring archive success."""
        self._check_owner()
        self.failed = True
        self._release()
