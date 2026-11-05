"""The Slot Library: every Usamune save state Griffin sets, kept so it can come back (Ghost Handoff).

`usamune` knows the slot format and is the one write path into game memory; `store` keeps states on disk;
`watcher` notices new saves; `coursesets` puts a course's states into the slots; `sharefile` is the file a
friend receives. Nothing here runs by itself: `.claude/rules/slot-library.md` says how it gets wired.
"""
