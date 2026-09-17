"""Strict disposal adapter for proctap's Windows ownership boundary.

The installed 1.0.3 core clears its reader after a timed join without checking
it, and both Python stop wrappers swallow backend failures. Its native stop
also ignores IAudioClient::Stop's HRESULT. Therefore quiet means joining the
actual reader, then releasing the native owner (whose destructor releases the
COM clients), not trusting the public stop result. Keep private API knowledge
here; a dependency change must retain this contract or fail visibly.
"""


def stop_process_tap(tap):
    backend = getattr(tap, "_backend", None)
    if backend is None:
        # Injectable AudioSource adapters expose an honest public stop method.
        tap.stop()
        return
    if not hasattr(backend, "_native") or not hasattr(tap, "_stop_event"):
        raise RuntimeError("unsupported process-audio disposal contract; tap remains owned")
    tap._stop_event.set()
    thread = tap._thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)
        if thread.is_alive():
            raise RuntimeError("process-audio reader did not stop; tap remains owned")
    # No reader or watchdog can now enter native read(). Bypass the wrappers
    # that suppress errors, then release the sole backend reference explicitly.
    # Native destruction is on recorder/watchdog teardown, never emulation.
    if backend._native is not None:
        backend._native.stop()
        backend._native = None
    tap._thread = None
