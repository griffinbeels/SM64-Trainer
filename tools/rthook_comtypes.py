# tools/rthook_comtypes.py
"""Runtime hook: comtypes (pulled in by pycaw) generates cache modules at
import time and needs a WRITABLE directory. The frozen bundle is read-only,
so point it at a temp dir before pycaw is imported."""
import os
import sys
import tempfile

if getattr(sys, "frozen", False):
    gen = tempfile.mkdtemp(prefix="ctgen_")
    os.environ.setdefault("COMTYPES_GEN_DIR", gen)
    try:
        import comtypes.client
        comtypes.client.gen_dir = gen
    except Exception:
        pass
