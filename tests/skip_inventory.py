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
    ("SM64_TEST_GHOST_",
     "The hidden desktop OpenGL ghost witnesses require explicit hardware "
     "admission and include GPU timing. Run them enabled after native ghost "
     "changes, separately from the ordinary full suite; see docs/testing.md.",
     "tests/test_ghost_compositor.py, test_ghost_presentation.py, "
     "test_ghost_compositor_tint.py, test_ghost_pipeline.py"),
    ("set SM64_MODEL_ROM",
     "The native model witness requires locally supplied copyrighted US ROM "
     "bytes, which a fresh clone cannot include. Lifts with SM64_MODEL_ROM "
     "and the documented pinned model seed; see docs/testing.md.",
     "tests/test_native_model.py"),
    ("SM64_GHOST_WITNESS_SOURCE",
     "The ghost adapter compile witness requires a separately staged pinned "
     "GLideN64 tree. Lifts by pointing SM64_GHOST_WITNESS_SOURCE at the source "
     "retained by a current tools/build_renderer.py --keep-scratch build.",
     "tests/test_ghost_pass.py"),
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
    ("data/tracker.db not present",
     "The corpus check reads this machine's own practice database, which a "
     "fresh clone has not got. Its rules are covered by seeded tests; this "
     "row only runs them against real history.",
     "tests/test_corpus_from_db.py"),
    ("Optional profiling dependency group is not installed",
     "The profiling extra is not in the default sync. Lifts with "
     "`uv sync --group profiling`.",
     "tests/test_profile_external.py"),
    ("no saved clip with a picture timer",
     "The ghost follower driven against REAL footage. `replays/` is this "
     "checkout's own and gitignored, so a clone that has never recorded has "
     "nothing to follow. The follower's rules are covered by seeded tests; "
     "this row only runs them against a real recording.",
     "tests/test_ui_ghost_follower.py"),
    ("no saved clip with a usable Mario box list",
     "The cut-out driven against a REAL box list. Box lists are made outside "
     "the app, one clip at a time, and saved beside the clip in a gitignored "
     "`replays/` -- so only a checkout somebody has made them in can run this. "
     "Lifts by running the tracker in "
     "`.tasks/features/session-f80d4eae236dc626451116fc/evidence/mario_boxes/` "
     "over a saved clip. The geometry and the refusals are covered by seeded "
     "tests; this row is the one that uses his own footage.",
     "tests/test_ui_ghost_page.py"),
)


def allowed_for(reason: str) -> tuple | None:
    """The inventory row covering this skip reason, or None."""
    for row in ALLOWED:
        if row[0] in reason:
            return row
    return None
