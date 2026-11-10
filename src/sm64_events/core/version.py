# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
# src/sm64_events/core/version.py
"""THE runtime version constant. Read by the app (update check baseline), the
build, and tools/release.py (which rewrites it on each release). The frozen exe
can't read pyproject.toml, so this in-package constant is authoritative;
release.py keeps pyproject.toml [project].version in sync for tooling."""
__version__ = "1.9.1"
