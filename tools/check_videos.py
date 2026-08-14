"""Check every example-video URL for liveness and write the verdicts seed.

    uv run python tools/check_videos.py [--recheck-days N] [--limit N]

Task 0098 round 2: a dead video must never be THE example a rank standard
links to. This sweeps every URL that can become an example — every library
entry's video, every vetted xcams clip, every strat-header video — through
`library/videocheck.py`'s per-platform probes, keeps fresh verdicts without a
request, and writes `src/sm64_events/data/video_checks.seed.json.gz` (read by
the standards API at startup; the exe ships it).

READ THE REPORT: the per-platform ok/dead/unknown counts are the deliverable.
`unknown` is honest — Twitch/bsky have no cheap unauthenticated liveness
probe and are never marked dead. `tools/scrape_sheet.py` runs this same sweep
after a refresh, so a routine re-scrape only pays for URLs it has not seen.
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core import paths  # noqa: E402
from sm64_events.library import videocheck  # noqa: E402
from sm64_events.library.store import read_snapshot  # noqa: E402

CHECKS_PATH = REPO / "src" / "sm64_events" / "data" / "video_checks.seed.json.gz"
TIMEOUT_S = 10
WORKERS = 16


def example_urls() -> list[str]:
    """Every URL that can surface as an example link, deduped, stable order."""
    urls: dict[str, None] = {}
    snapshot = read_snapshot(paths.bundled_sheet_library()) or {"targets": []}
    for target in snapshot["targets"]:
        for item in target["approaches"] + target["subsections"]:
            for entry in item["entries"]:
                if entry.get("video"):
                    urls.setdefault(entry["video"])
    import json
    seed = json.loads(Path(paths.bundled_rank_standards()).read_text())
    for entity in seed.get("entities", {}).values():
        for url in (entity.get("videos") or {}).values():
            if url:
                urls.setdefault(url)
        for clip_list in (entity.get("clips") or {}).values():
            for _cs, url in clip_list:
                if url:
                    urls.setdefault(url)
    return list(urls)


def http_status(probe: str) -> int | None:
    request = urllib.request.Request(probe, method="GET", headers={
        "User-Agent": "sm64-trainer-videocheck/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError, ValueError):
        return None           # network trouble is UNKNOWN, never dead


def sweep(urls, checks, recheck_days) -> dict:
    """run_checks with the network fanned out: the pool fetches a status per
    stale URL first (keyed by its probe URL, which embeds the video URL and
    is therefore unique), then the pure pass consumes the dict."""
    stale = videocheck.stale_urls(urls, checks, recheck_days=recheck_days)
    probes = [videocheck.probe_url(url) for url in stale]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        status_by_probe = dict(zip(probes, pool.map(http_status, probes)))
    return videocheck.run_checks(
        urls, checks, lambda probe: status_by_probe.get(probe),
        recheck_days=recheck_days)


def run(recheck_days: int = videocheck.RECHECK_DAYS,
        limit: int | None = None) -> dict:
    urls = example_urls()
    if limit:
        urls = urls[:limit]
    checks = videocheck.load_checks(CHECKS_PATH)
    stats = sweep(urls, checks, recheck_days)
    videocheck.save_checks(CHECKS_PATH, checks)
    dead = videocheck.dead_urls(checks)
    report = {**stats, "urls": len(urls), "dead_total": len(dead),
              "ok_total": sum(1 for verdict in checks.values()
                              if verdict.get("status") == videocheck.OK)}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recheck-days", type=int,
                        default=videocheck.RECHECK_DAYS)
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N urls (a smoke run)")
    args = parser.parse_args()
    report = run(args.recheck_days, args.limit)
    print(f"urls considered: {report['urls']}")
    print(f"probed now: {report['checked']}  fresh (skipped): "
          f"{report['skipped_fresh']}  unknown (unstored): {report['unknown']}")
    print(f"verdicts on file: ok {report['ok_total']} / dead {report['dead_total']}")
    print(f"wrote {CHECKS_PATH}")


if __name__ == "__main__":
    main()
