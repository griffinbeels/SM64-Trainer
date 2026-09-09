# Replay storage and preservation

One continuously encoded recording supplies temporary replay views. No encoder
is created per attempt. Successful native cuts reuse encoded packets; saved
MP4 files retain the existing picture and input metadata.

`SegmentRing` owns temporary-byte accounting and eviction: closed segments,
extracted clip groups, busy-file deletion retries, and sampled live partial/index
files count toward one budget. The new default is 2 GiB; existing saved settings
win. A 5 GiB free-space reserve can reduce the effective cap further. Oldest
eligible unsaved media goes first. Active encoding and pinned work may briefly
exceed the configured cap. When eligible eviction cannot restore the free-space
reserve, recording pauses and retries automatically; the resulting coverage gap
stays explicit rather than compressing time or inventing missing pictures.

Source-span leases include newly published tail segments through extraction and
identity mapping. Temporary-group leases also protect an output before it is
registered, and HTTP range responses release them in a finally block. Idle
segment discard uses the same owner. A failed Windows unlink remains accounted
for and retried. Directory inventory runs at maintenance frequency, not per frame.
Registered segment paths are resolved once at admission and retained until eviction
or reset. Inventory uses a fresh `os.scandir` pass over regular directories, skips
symlinks and junctions, and reads current file sizes from that pass. Growing encoder
files, partial clips and SQLite/WAL files remain accounted; no directory entry is
cached across passes. Recounts and lease releases reuse the registered identities.
On Windows, directory enumeration already supplies regular-file metadata, avoiding
the per-file opens made by repeated path resolution and stat calls.
See [Python's directory-entry contract](https://docs.python.org/3/library/os.html#os.DirEntry).

A valid manual PB command calls replay preservation off the event loop. Video
failure is a separate outcome, displayed beside the row with a retry action;
it never rolls back the valid time or silently chooses another PB. Undo and
supersession keep previously saved videos. Explicit Save replay uses the same
publication path. Saved files are never automatic eviction candidates.

Publication stages video, identity metadata and review preferences under the
save root. On the same volume a hard link retains the immutable video without
another full write; a copy fallback checks free space. Metadata is installed
before the final MP4 name becomes visible. Completed staging can recover after
an interruption without its original scratch source. An interrupted incomplete
save is reported; it is not presented as a successfully preserved video. The
temporary duplicate is removed after publication, and an already-open temporary
URL can resolve to the same saved bytes.

`SessionGate` drains extraction/save operations before replacing their identity
ledger. A user session switch rotates owned scratch while retaining the recorder
lock. Shutdown and next startup clean unsaved files; active HTTP leases survive
until their reads finish. Cleanup requires both machine ownership and an exact
lifetime token. A losing viewer or former owner cannot delete the current
recorder's files. Practice reset, pause, emulator reconnect and drawer close do
not end session retention.

These storage changes do not establish lower whole-machine CPU/GPU usage,
instantaneous first playback, or a fix for physical display blinking. Incremental
browser media and GPU texture transfer require their own timing, pixel, audio
and actual-consumer evidence before replacing the current capture/playback path.
