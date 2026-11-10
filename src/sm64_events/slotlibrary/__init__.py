# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""The Slot Library: every Usamune save state Griffin sets, kept so it can come back (Ghost Handoff).

`usamune` knows the slot format and is the one write path into game memory; `store` keeps states on disk;
`watcher` notices new saves; `coursesets` puts a course's states into the slots; `refit` moves this place's slots to Mario's place after
each spawn; `sharefile` is the file a friend receives. Nothing here runs by itself: `.claude/rules/slot-library.md` says how it gets wired.
"""
