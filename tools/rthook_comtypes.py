# tools/rthook_comtypes.py
"""Runtime hook: comtypes (pulled in by pycaw) generates cache modules at
import time and needs a WRITABLE directory. The frozen bundle is read-only,
so point it at a temp dir before pycaw is imported."""
import os
import sys
import tempfile

# This hook runs before gui_entry. The workbook worker never uses audio/COM;
# importing it here would add a cache directory and unrelated startup work.
if (getattr(sys, "frozen", False)
        and sys.argv[1:2] != ["--library-refresh-worker"]):
    gen = tempfile.mkdtemp(prefix="ctgen_")
    os.environ.setdefault("COMTYPES_GEN_DIR", gen)
    try:
        import comtypes.client
        comtypes.client.gen_dir = gen
    except Exception:
        pass
