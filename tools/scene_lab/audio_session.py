# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""The audio capture file and the offline join with a scene-tap session (M4 E4-audio).

    audio_session.py build <tap session> <audio.cap> <out.audio> [--codec flac|stored] [--offset-ms X]
        [--report report.json]
    audio_session.py report <tap session> <audio.cap> [--offset-ms X]
    audio_session.py attach <package.sm64scene> <layer.audio> <out.sm64scene> --rom <rom.z64>

The capture file is what a bounded fanout from the recorder's process tap
writes (e4-audio-design.md): a header naming the sample rate and the QPC
frequency, then one record per tap chunk in arrival order, each with its tap
epoch, first sample index in that epoch, sample count, the QPC tick it reached
the fanout and, unless the fanout had to drop it (LOST), its s16 stereo PCM.
`build` reads the session's present stamps (tap.py), fits the audio clock
(audio_clock.py), maps every present and writes the package-ready layer
(audio_layer.py) with a JSON report: drift, jitter, holes, losses and picture
statuses. `attach` writes a scene package again with that layer as its
source-audio layer (package.with_audio; `out` may be the package's own path),
refusing audio from another session. Capture files hold private game audio:
keep them out of the repository.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402

MAGIC = b"SM64ACP\x00"
VERSION = 1
FILE_HEADER = struct.Struct("<8sHHIQ")     # magic, version, channels, rate, QPC frequency
RECORD = struct.Struct("<HHIQIq")         # kind, flags, epoch, first, samples, arrival QPC
CHUNK_KIND, LOST = 1, 1
MAX_CAPTURE_BYTES = 4 << 30


class CaptureError(ValueError):
    """The capture file is malformed or does not belong with the session."""


def write_capture(path: Path, rate: int, frequency: int, records) -> None:
    """records: (Chunk, s16 stereo bytes | None). The format a fanout writes."""
    with path.open("wb") as out:
        out.write(FILE_HEADER.pack(MAGIC, VERSION, 2, rate, frequency))
        for chunk, data in records:
            out.write(RECORD.pack(CHUNK_KIND, LOST if chunk.lost else 0, chunk.epoch, chunk.first,
                                  chunk.samples, chunk.arrival))
            if not chunk.lost:
                out.write(data)


def read_capture(path: Path) -> tuple[int, int, list, list]:
    """(rate, frequency, chunks, payloads); refuses anything malformed."""
    size = path.stat().st_size
    if size > MAX_CAPTURE_BYTES:
        raise CaptureError("capture file larger than allowed")
    data = path.read_bytes()
    if len(data) < FILE_HEADER.size:
        raise CaptureError("capture file truncated")
    magic, version, channels, rate, frequency = FILE_HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION or channels != 2:
        raise CaptureError("not a version-1 stereo audio capture")
    chunks, payloads, at = [], [], FILE_HEADER.size
    while at < len(data):
        if at + RECORD.size > len(data):
            raise CaptureError(f"capture record truncated at byte {at}")
        kind, flags, epoch, first, samples, arrival = RECORD.unpack_from(data, at)
        if kind != CHUNK_KIND or flags & ~LOST:
            raise CaptureError(f"unknown capture record at byte {at}")
        at += RECORD.size
        if flags & LOST:
            payloads.append(None)
        else:
            if at + samples * 4 > len(data):
                raise CaptureError(f"capture PCM truncated at byte {at}")
            payloads.append(data[at:at + samples * 4])
            at += samples * 4
        chunks.append(ac.Chunk(epoch, first, samples, arrival, bool(flags & LOST)))
    return rate, frequency, chunks, payloads


def session_presents(session: Path) -> tuple[int, list]:
    """(QPC frequency, presents) of a scene-tap session, in record order."""
    import tap

    header = tap.session_header(session)
    presents = [ac.Present(r.sequence, r.qpc) for r in tap.read_records(session / "tap.bin")
                if isinstance(r, tap.Present)]
    return int(header["qpc_frequency"]), presents


def build(session: Path, capture: Path, *, offset_ms: float = 0.0, presents: list | None = None):
    """(AudioClock, stored stream) for a tap session and its audio capture.
    `presents`: the session's presents when the caller already read them (a
    long session's tap.bin is gigabytes; this skips a second pass)."""
    if presents is None:
        frequency, presents = session_presents(session)
    else:
        import tap

        frequency = int(tap.session_header(session)["qpc_frequency"])
    rate, cap_frequency, chunks, payloads = read_capture(capture)
    if cap_frequency != frequency:
        raise CaptureError(f"capture clock {cap_frequency} Hz differs from the session's {frequency} Hz")
    clock = ac.build_clock(chunks, presents, rate=rate, frequency=frequency,
                           offset_ticks=round(offset_ms * frequency / 1000))
    return clock, ac.stream_pcm(chunks, payloads)


def attach(package_path: Path, layer_path: Path, out: Path, rom_path: Path) -> dict:
    """Write the package again with the layer file as its source-audio layer (atomically, so
    `out` may be the package itself). Returns what the viewer will play, counted per frame a
    present showed: with sound, with lost sound (silence, marked), without any."""
    import package as scene_package
    import scene_audio

    rom = rom_path.read_bytes()
    data = scene_package.with_audio(package_path.read_bytes(), rom, layer_path.read_bytes())
    partial = out.with_name(out.name + ".partial")
    partial.write_bytes(data)
    partial.replace(out)
    package = scene_package.Package(data, rom)
    outlines = [package.outline(index) for index in range(len(package))]
    sound = scene_audio.SceneSound.of(package, [outline["present"] for outline in outlines if outline["vi"]])
    silent = sum(sample < 0 for sample in sound.samples)
    return {"package_bytes": len(data), "seconds": round(sound.layer.total_samples / sound.layer.rate, 3),
            "frames": {"sound": len(sound.samples) - silent - len(sound.lost), "lost": len(sound.lost),
                       "none": silent}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "report"):
        p = sub.add_parser(name)
        p.add_argument("session")
        p.add_argument("capture")
        if name == "build":
            p.add_argument("out")
            p.add_argument("--codec", choices=sorted(al.CODECS), default="flac")
            p.add_argument("--report")
        p.add_argument("--offset-ms", type=float, default=0.0)
    p = sub.add_parser("attach", help="write a scene package again with its source-audio layer")
    p.add_argument("package", type=Path)
    p.add_argument("layer", type=Path, help="what build wrote")
    p.add_argument("out", type=Path, help="the package written again with the layer (may be the same path)")
    p.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "attach":
        try:
            print(json.dumps(attach(args.package, args.layer, args.out, args.rom), indent=2))
        except ValueError as error:          # package.PackageError, audio_layer.LayerError
            print(f"refused: {error}", file=sys.stderr)
            return 2
        return 0
    clock, pcm = build(Path(args.session), Path(args.capture), offset_ms=args.offset_ms)
    report = dict(clock.stats, total_samples=clock.total_samples, offset_ms=args.offset_ms)
    if args.command == "build":
        payload = al.encode_layer(clock, pcm, codec=args.codec)
        Path(args.out).write_bytes(payload)
        report["layer_bytes"] = len(payload)
        report["mb_per_min"] = round(len(payload) / 1e6 / (clock.total_samples / clock.rate / 60), 3) \
            if clock.total_samples else None
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "segments"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
