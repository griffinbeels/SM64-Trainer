/* Compile the real worker with one observable wait boundary. The bridge only
 * coordinates the witness's lock contention, then calls the real Win32 wait. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
DWORD WINAPI ghost_witness_wait(DWORD, const HANDLE*, BOOL, DWORD);
#define WaitForMultipleObjects ghost_witness_wait
#include "../../plugin/gfxwrap/ghost_ipc.cpp"
#undef WaitForMultipleObjects
