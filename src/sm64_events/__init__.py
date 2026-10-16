"""SM64 Trainer.

ONE PROCESS-WIDE SETTING, and it has to live here: every entry point -- the
server (`python -m sm64_events.main`), the desktop app, the frozen exe's
`gui_entry.py` and the children it re-enters as -- imports this package before
anything that loads numpy.

numpy's bundled OpenBLAS reserves a working buffer per thread when it is
imported, one per logical CPU: measured on his 32-thread machine, `import
numpy` alone cost 749 MiB of private commit, and with one thread 9 MiB. Nothing
in this app calls BLAS (numpy here is array copies and byte views), so the
threads buy nothing. `setdefault`, so an explicit value in the environment
still wins.
"""
import os as _os

_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
