"""Find a process's output endpoint without importing audio capture or NumPy."""
import logging

log = logging.getLogger("sm64.replay")


def device_name_hosting_pid(pid: int) -> str | None:
    """FriendlyName of the active render endpoint hosting this process.

    Initialize COM on the calling thread; watchdogs and the sound player
    otherwise do not inherit the apartment initialized during the first import.
    """
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import AudioUtilities

        # Long-lived rescanners intentionally retain this apartment: releasing
        # wrappers after a per-call CoUninitialize can invalidate their owner.
        try:
            comtypes.CoInitialize()
        except OSError:
            pass  # already initialized in another usable mode
        devenum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
        coll = devenum.EnumAudioEndpoints(0, 1)  # eRender, DEVICE_STATE_ACTIVE
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
            mgr = mgr.QueryInterface(IAudioSessionManager2)
            sess_enum = mgr.GetSessionEnumerator()
            for j in range(sess_enum.GetCount()):
                ctl2 = sess_enum.GetSession(j).QueryInterface(IAudioSessionControl2)
                try:
                    if ctl2.GetProcessId() == pid:
                        return AudioUtilities.CreateDevice(dev).FriendlyName
                except Exception:  # noqa: BLE001, S112 - vanished sessions do not invalidate other endpoints
                    continue
    except Exception:
        log.exception("audio session scan failed")
    return None
