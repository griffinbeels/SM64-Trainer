"""The library a running app reads, and the rule for which copy wins.

Two copies exist: the snapshot bundled with the release, and whatever the user
last refreshed into their data directory. THE NEWER SHEET REVISION WINS, in
both directions — that is the whole point, and it is answerable only because a
snapshot carries the Ultimate Sheet's OWN newest Log-tab timestamp rather than
the moment we happened to fetch it.

Both directions matter, and the second is the one a naive rule gets wrong:

  * refresh on Tuesday, update the app on Wednesday to a release built Monday
    → the refreshed copy is newer and must survive the update;
  * refresh in January, update in March → the bundled copy is newer and must
    replace it, rather than a stale local file shadowing the release forever.

`schema_version` is a separate question and is checked first: a copy written by
an older build of THIS code is discarded whatever its sheet revision says,
because its shape is not what the readers expect."""
import gzip
import json
import logging
from pathlib import Path

from sm64_events.library.build import SCHEMA_VERSION

_log = logging.getLogger("sm64.library")


def read_snapshot(path) -> dict | None:
    """A snapshot from disk, gzipped or not, or None when unusable."""
    if not path:
        return None
    path = Path(path)
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, EOFError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        return None
    return data


def write_snapshot(path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so an unchanged sheet round-trips to identical bytes.
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as out:
        out.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _usable(snapshot) -> bool:
    return bool(snapshot) and snapshot.get("schema_version") == SCHEMA_VERSION


def newer(first, second):
    """Whichever snapshot carries the later SHEET revision, ignoring anything
    written by an older schema. Ties go to the first argument."""
    candidates = [s for s in (first, second) if _usable(s)]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.get("sheet_revision") or "")


class LibraryStore:
    """Read side of the library, plus the refresh that replaces it."""

    def __init__(self, path=None, bundled_path=None):
        self.path = Path(path) if path else None
        self.bundled_path = Path(bundled_path) if bundled_path else None
        self._payload = None
        self._source = None   # "local" | "bundled" | None (nothing loaded)

    # ---- load ----
    def load(self) -> None:
        bundled = read_snapshot(self.bundled_path)
        local = read_snapshot(self.path)
        self._payload = newer(local, bundled)
        self._source = ("local" if self._payload is local and local is not None
                        else "bundled" if self._payload is not None else None)
        if self._payload is None:
            _log.info("no usable sheet library at %s or %s", self.path,
                      self.bundled_path)
        elif local is not None and self._payload is not local:
            _log.info("bundled sheet library is newer (%s) than the local copy "
                      "(%s); using the bundled one",
                      bundled.get("sheet_revision") if bundled else None,
                      local.get("sheet_revision"))

    @property
    def payload(self) -> dict:
        return self._payload or {"schema_version": SCHEMA_VERSION,
                                 "sheet_revision": None, "targets": [],
                                 "runners": [], "ladder_model": {}}

    @property
    def revision(self) -> str | None:
        return self.payload.get("sheet_revision")

    def status(self) -> dict:
        payload = self.payload
        return {"sheet_revision": payload.get("sheet_revision"),
                "fetched_at": payload.get("fetched_at"),
                "targets": len(payload["targets"]),
                "runners": len(payload.get("runners") or []),
                "ladder_model": payload.get("ladder_model") or {},
                "source": self._source}

    # ---- reads ----
    def index(self) -> dict:
        """Groups, and the targets inside each — enough to draw a browser
        without shipping 44,000 entries to do it."""
        groups = {}
        for position, target in enumerate(self.payload["targets"]):
            # The stored "group" must be the SAME value used as the dict key --
            # a falsy target["group"] keyed by target["section"] but stamped
            # with the raw (falsy) value would hand the UI a group it can
            # never reopen, since falsy is its own "nothing is open" sentinel.
            key = target["group"] or target["section"]
            group = groups.setdefault(key, {"group": key, "targets": []})
            group["targets"].append({
                "index": position,
                "section": target["section"], "label": target["label"],
                "entity_key": target["entity_key"],
                "miss_reason": target["miss_reason"],
                "approaches": len(target["approaches"]),
                "subsections": len(target["subsections"]),
                # The names of every way of doing this target, so the Library's
                # search box can match on them without a lookup per keystroke.
                # Griffin's call (round 12): a result row is always a TARGET,
                # matched on its own label AND on its approaches' names, so
                # typing "LBLJ" finds the target that documents it. The whole
                # snapshot's 631 names are 15.9 KB of text against an index
                # already carrying 252 rows -- cheap enough that a second
                # endpoint would cost more than it saved, and this way the
                # match runs on data the page already holds.
                "approach_names": [way["name"] for way
                                   in target["approaches"] + target["subsections"]
                                   if way.get("name")],
                "entries": sum(len(item["entries"]) for item
                               in target["approaches"] + target["subsections"])})
        return {"sheet_revision": self.revision, "groups": list(groups.values())}

    def target(self, index: int) -> dict | None:
        targets = self.payload["targets"]
        return targets[index] if 0 <= index < len(targets) else None

    def for_entity(self, entity_key: str) -> list:
        """Every target mapped to one entity — what the book mark on the
        objective card jumps to. Several is normal: each `+ 100c` row is the
        same 100-coin star run a different way."""
        return [{"index": position, **target}
                for position, target in enumerate(self.payload["targets"])
                if target["entity_key"] == entity_key]

    def runner(self, name: str) -> dict:
        """One runner's whole sheet, newest question first: what have they run,
        how fast, and where is the video."""
        rows = []
        for position, target in enumerate(self.payload["targets"]):
            for kind in ("approaches", "subsections"):
                for item in target[kind]:
                    for entry in item["entries"]:
                        if entry["runner"] != name:
                            continue
                        rows.append({
                            "target_index": position, "target": target["label"],
                            "section": target["section"],
                            "entity_key": target["entity_key"],
                            "approach": item["name"], "kind": kind[:-1],
                            "time_cs": entry["time_cs"],
                            "video": entry["video"],
                            "ladder": item.get("ladder")})
        return {"runner": name, "entries": rows}

    def runners(self) -> list:
        return list(self.payload.get("runners") or [])

    # ---- refresh ----
    def refresh(self, fetch_fn, overrides=None) -> dict:
        """Fetch the live sheet, rebuild, and keep it only if it is NEWER.

        A refresh that lands on an older revision than what we already have is
        not an error and is not applied: the sheet is the authority on its own
        age, and re-fetching an unchanged sheet should not churn the file."""
        from sm64_events.library.build import build
        from sm64_events.library.ladders import fit_payload
        from datetime import datetime, timezone

        data = fetch_fn()
        fetched_at = (datetime.now(timezone.utc).replace(microsecond=0)
                      .isoformat().replace("+00:00", "Z"))
        fresh = fit_payload(build(data, fetched_at=fetched_at,
                                  overrides=overrides))
        # Stamp each approach's vetted twin, exactly as the bundled snapshot
        # does at scrape time -- a refresh must not produce a snapshot the
        # Library page reads differently.
        from sm64_events.core.paths import bundled_rank_standards
        from sm64_events.library.adopt import stamp_matches
        seed_path = bundled_rank_standards()
        if seed_path:
            seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
            stamp_matches(fresh,
                          {ek: {s: l for s, l in e.get("strategies", {}).items() if l}
                           for ek, e in seed["entities"].items()})
        current = self._payload
        if current is not None and newer(current, fresh) is current:
            return {"applied": False, "sheet_revision": self.revision,
                    "fetched_revision": fresh.get("sheet_revision"),
                    "reason": "the live sheet is not newer than what we have"}
        if self.path:
            write_snapshot(self.path, fresh)
        self._payload = fresh
        # "local" only means something once written -- a pathless store (no
        # embedder passes one; create_app always does) never persists this
        # payload anywhere, so claiming "local" here would have status()
        # report a copy on disk that was never actually saved.
        self._source = "local" if self.path else None
        return {"applied": True, "sheet_revision": fresh.get("sheet_revision"),
                "fetched_revision": fresh.get("sheet_revision"),
                "targets": len(fresh["targets"])}
