"""Reviewed portable recordings and author identities for packaged candidates."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile

from sm64_events.ghost.portable import validate_package
from sm64_events.ghost.bundled_beta import AUTHOR, checked_author_identity

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


def review_author(source: Path, build) -> dict:
    """Read the operator-reviewed public binding without echoing submitted values."""
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= 4096:
        raise ValueError("Choose a bounded reviewed ghost author binding.")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != {"schema", "author", "issuer", "subject"}
                or type(value["schema"]) is not int or value["schema"] != 1 or value["author"] != AUTHOR):
            raise ValueError()
        identity = checked_author_identity({"issuer": value["issuer"], "subject": value["subject"]})
    except (ValueError, UnicodeError):
        raise ValueError("The reviewed ghost author binding is invalid.") from None
    if not build.community_enabled or identity["issuer"] != build.supabase_url.rstrip("/") + "/auth/v1":
        raise ValueError("The reviewed ghost author binding does not match the configured Auth issuer.")
    return identity


def candidate_author(source: Path | None, ghosts: Path | None, build, *, private=False):
    """Public curated candidates require the reviewed account before any build."""
    if source is not None and ghosts is None:
        raise ValueError("A reviewed ghost author binding requires --ghosts.")
    if ghosts is not None and source is None and not private:
        raise ValueError("A public ghost collection requires --ghost-author with its reviewed Auth identity.")
    return review_author(source, build) if source is not None else None


def seed_descriptor(approved: ApprovedCollection, author_identity=None) -> dict:
    """One manifest shared by private packaging, public preparation and verification."""
    value = {"schema": 1, "file": COLLECTION_NAME, "author": AUTHOR,
             "sha256": approved.sha256, "count": approved.count}
    if author_identity is not None:
        value.update(schema=2, author_identity=checked_author_identity(author_identity))
    return value


def matches(path: Path, approved: ApprovedCollection) -> bool:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_COLLECTION_BYTES:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == approved.sha256


def _every_pad_known(extracted: Path, digest: str, verified: dict) -> bool:
    """The one completeness question (ghost/clip.py::inputs_complete), asked of the validated clip."""
    from sm64_events.ghost.clip import inputs_complete, read_clip
    assert digest in verified  # validate_package extracted and hash-checked it
    return inputs_complete(read_clip(extracted, digest))


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
    if with_inputs != len(rows):
        # His rule (2026-10-12): "If it's not a perfectly recorded ghost, it
        # doesn't belong in the system." A collection with one unknown frame
        # does not ship.
        raise ValueError(f"The beta collection must contain recordings with complete inputs only "
                         f"({len(rows) - with_inputs} of {len(rows)} are incomplete).")
    approved = ApprovedCollection(source, digest, len(rows), with_inputs)
    if not matches(source, approved):
        raise ValueError("The reviewed ghost collection changed during validation.")
    return approved
