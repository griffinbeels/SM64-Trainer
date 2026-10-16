"""Run a hidden two-core process tree, killed on runner exit."""
import ctypes
from ctypes import wintypes as wt
from pathlib import Path
import subprocess, sys, time
class Basic(ctypes.Structure):
    _fields_=[("pt",ctypes.c_int64),("jt",ctypes.c_int64),("flags",wt.DWORD),("min",ctypes.c_size_t),("max",ctypes.c_size_t),("processes",wt.DWORD),("affinity",ctypes.c_size_t),("priority",wt.DWORD),("scheduling",wt.DWORD)]
class Extended(ctypes.Structure):
    _fields_=[("basic",Basic),("io",ctypes.c_uint64*6),("pm",ctypes.c_size_t),("jm",ctypes.c_size_t),("peakp",ctypes.c_size_t),("peakj",ctypes.c_size_t)]
class Accounting(ctypes.Structure):
    _fields_=[("user",ctypes.c_int64),("kernel",ctypes.c_int64),("pu",ctypes.c_int64),("pk",ctypes.c_int64),("faults",wt.DWORD),("total",wt.DWORD),("active",wt.DWORD),("terminated",wt.DWORD)]
def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    if sys.platform!="win32":return subprocess.call(sys.argv[1:],cwd=Path(__file__).resolve().parents[1])
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wt.LPCWSTR];k.CreateJobObjectW.restype=wt.HANDLE
    k.SetInformationJobObject.argtypes=[wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD]
    k.AssignProcessToJobObject.argtypes=[wt.HANDLE,wt.HANDLE]
    k.QueryInformationJobObject.argtypes=[wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD,ctypes.c_void_p]
    k.CloseHandle.argtypes=[wt.HANDLE]
    job=k.CreateJobObjectW(None,None);child=None;started=time.monotonic()
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits=Extended();limits.basic.flags=0x2000|0x10|0x20;limits.basic.affinity=(1<<20)|(1<<21);limits.basic.priority=0x4000
        if not k.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):raise ctypes.WinError(ctypes.get_last_error())
        child=subprocess.Popen(sys.argv[1:],cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW|0x4|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        if not k.AssignProcessToJobObject(job,wt.HANDLE(child._handle)):raise ctypes.WinError(ctypes.get_last_error())
        nt=ctypes.WinDLL("ntdll");nt.NtResumeProcess.argtypes=[wt.HANDLE]
        if nt.NtResumeProcess(wt.HANDLE(child._handle))!=0:raise RuntimeError("Cannot resume owned process")
        output,_=child.communicate(timeout=600);print(output,end='',flush=True);result=child.returncode;a=Accounting();k.QueryInformationJobObject(job,1,ctypes.byref(a),ctypes.sizeof(a),None)
        print(f"owned-job wall={time.monotonic()-started:.2f}s cpu={(a.user+a.kernel)/10000000:.2f}s",flush=True);return result
    finally:
        k.CloseHandle(job)
        if child is not None:
            if child.poll() is None:child.kill()
            child.wait()
if __name__=="__main__":sys.exit(main())
