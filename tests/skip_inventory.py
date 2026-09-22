"""Every skip a whole-suite run is allowed to take, and why.

A skip is invisible. The gate prints one number, and 67 of them can mean
"three vendors' hardware is absent" or "the rendered gate switched itself off
and 321 browser tests did nothing" -- both read the same. On 2026-09-17 the
second one had been true in every `.codex` worktree for weeks.

So the rule is: a skip is documented here or it fails the run. Deliberately
NOT listed are the guards that must never fire on a working machine -- uilab
missing, node/ffmpeg/MSVC absent, the harness or chain checker not installed.
If one of those ever fires here, the gate says so instead of quietly dropping
the tests.

`tests/conftest.py` enforces this only when the whole suite was collected; a
single-file run is free to skip anything.
"""

# (substring of the skip reason, why it is allowed, what would lift it)
ALLOWED = (
    ("is not usable on this machine",
     "A vendor's hardware video encoder is absent (this box is NVIDIA, so the "
     "Intel QuickSync and AMD paths cannot run). Lifts on hardware that has it.",
     "tests/test_replay_picture_identity.py::encoder"),
    ("separately staged pinned LINK source",
     "The renderer-source witnesses compile against the pinned third-party "
     "tree. Lifts with SM64_LINK_WITNESS_SOURCE pointing at "
     "tools/stage_link_witness.py's output.",
     "tests/test_link_source_witness.py, tests/test_renderer_source.py"),
    ("SM64_TEST_GPU",
     "The real-adapter GPU witnesses. Kept out of the gate on purpose "
     "(.claude/rules/native-plugin.md): they need the NVIDIA adapter and are "
     "timing-sensitive. Run by hand after a native contract change.",
     "tests/test_gpu_bridge.py, test_gpu_cadence.py, test_gpu_fidelity.py, "
     "test_gpu_selection.py"),
    ("no sync report for",
     "The layout-vs-report gate. data/version_sync/ is tracked but empty, so "
     "there is nothing to compare against yet. Lifts when a live "
     "tools/sync_version.py run is committed -- a real hole, kept visible.",
     "tests/test_layout_matches_report.py"),
    ("symlink creation unavailable",
     "Creating a symlink needs Developer Mode or an elevated host; the "
     "inventory's non-symlink behaviour is still covered.",
     "tests/test_replay_inventory.py"),
    ("WPR recording needs an elevated host",
     "Windows Performance Recorder needs elevation. The ownership half of "
     "that module still runs.",
     "tests/test_profile_external.py"),
    ("only meaningful under",
     "A check on the xdist scheduler itself, which a SERIAL run has no "
     "scheduler to check. Lifts under `-n <k> --dist loadgroup`, which is how "
     "tools/run_tests.py always runs -- found 2026-09-18 by `release.py`, "
     "which runs a bare serial `pytest -q` instead of the project's gate.",
     "tests/test_worker_groups.py"),
    ("no OpenGL 3.3+ driver",
     "The native GL witnesses (snapshot, context lifetime, capture chain, "
     "source snapshot) need a real OpenGL driver; a GitHub runner has only "
     "Windows' GDI OpenGL 1.1. They run on any machine with a GPU driver, and "
     "in the blast radius of any change to their native sources. Lifts on the "
     "runner with a software GL 3.3+ (Mesa's opengl32.dll beside the host).",
     "tests/test_gl_snapshot.py, test_context_lifetime.py, test_capture_chain.py, "
     "test_source_snapshot.py"),
    ("data/tracker.db not present",
     "The corpus check reads this machine's own practice database, which a "
     "fresh clone has not got. Its rules are covered by seeded tests; this "
     "row only runs them against real history.",
     "tests/test_corpus_from_db.py"),
    ("Optional profiling dependency group is not installed",
     "The profiling extra is not in the default sync. Lifts with "
     "`uv sync --group profiling`.",
     "tests/test_profile_external.py"),
)


# Skips a GitHub runner may take and this desktop may NOT: the runner has no
# machine-level harness or knowledge repo, no NVIDIA encoder and no live
# journal, while here each of those skipping means something broke.
RUNNER_ONLY = (
    ("the shared harness is not installed",
     "The skill-identity guards read ~/.claude/harness, which only Griffin's "
     "machines have. They run in every local merge check that touches them.",
     "tests/test_agent_config_parity.py"),
    ("no harness installed at",
     "The Codex hook generation check runs the machine's harness installer.",
     "tests/test_agent_config_parity.py"),
    ("the machine-wide chain checker is not installed",
     "The chain checker lives in the knowledge repo (~/.claude/knowledge).",
     "tests/test_chains.py"),
    ("nvenc not available",
     "An NVENC witness; a runner has no NVIDIA encoder.",
     "tests/test_replay_encoder.py"),
    ("no live journal reachable",
     "Reads this machine's own practice journal; a runner has none.",
     "tests/test_rekey.py"),
)


def allowed_for(reason: str, *, runner: bool | None = None) -> tuple | None:
    """The inventory row covering this skip reason, or None. RUNNER_ONLY
    rows count only on a GitHub runner (GITHUB_ACTIONS=true)."""
    import os
    runner = os.environ.get("GITHUB_ACTIONS") == "true" if runner is None else runner
    for row in ALLOWED + (RUNNER_ONLY if runner else ()):
        if row[0] in reason:
            return row
    return None
