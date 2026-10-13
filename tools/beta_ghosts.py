"""Explicitly reviewed portable recordings for a private beta, never a profile."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile

from sm64_events.ghost.portable import validate_package

COLLECTION_NAME = "griffman1212.sm64ghosts"
MAX_COLLECTION_BYTES = 128 << 20
MAX_UNPACKED_BYTES = 256 << 20


@dataclass(frozen=True)
class ApprovedCollection:
    source: Path
    sha256: str
    count: int
    # Recordings whose every record carries a controller pad. beta.2 shipped
    # 160 of 180 with none (round 39: "they're all missing their inputs!"),
    # because the collection was packaged from stored clips instead of their
    # playback copies. The number is reported, never a refusal: a recording
    # its input track cannot fill is honestly unknown.
    with_inputs: int = 0


def matches(path: Path, approved: ApprovedCollection) -> bool:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_COLLECTION_BYTES:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == approved.sha256


def _every_pad_known(extracted: Path, digest: str, verified: dict) -> bool:
    """True when the validated clip carries a pad on each of its records."""
    from sm64_events.ghost.clip import read_clip
    _clip_id, _clock, _blinks, pad_records, _level = verified[digest]
    return pad_records > 0 and pad_records == len(read_clip(extracted, digest).frames)


def review_collection(source: Path) -> ApprovedCollection:
    """Validate every original clip; human-source provenance remains separate."""
    source = source.absolute()
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= MAX_COLLECTION_BYTES:
        raise ValueError("Choose a bounded portable ghost collection, not a practice profile.")
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with tempfile.TemporaryDirectory(prefix="beta-ghost-review-") as staging:
        rows, verified = validate_package(source, staging, unpacked_limit=MAX_UNPACKED_BYTES)
        with_inputs = sum(_every_pad_known(Path(staging) / f"{row['sha256']}.ghost", row["sha256"], verified)
                          for row in rows)
    if any(row["author"] != "griffman1212" or row["practice"] or row["outcome"] != "success" for row in rows):
        raise ValueError("The beta collection must contain successful griffman1212 recordings only.")
    approved = ApprovedCollection(source, digest, len(rows), with_inputs)
    if not matches(source, approved):
        raise ValueError("The reviewed ghost collection changed during validation.")
    return approved
