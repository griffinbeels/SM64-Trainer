/* The scene tap: an armed, time-limited recorder of graphics-task closures
 * for the M4 capture experiment (E0/E1). Idle and allocation-free unless an
 * arming file exists when a practice ROM opens; see scene_tap.cpp. */
#pragma once
#include "zilmar.h"
#ifdef __cplusplus
extern "C" {
#endif
void __cdecl st_configure(const GFX_INFO *info);
void __cdecl st_rom_open(void);
void __cdecl st_process_dlist(void);           // before the renderer runs the task
void __cdecl st_update_screen(fn_readscreen read_screen);  // after the renderer presents
void __cdecl st_stop(void);                     // finish: the writer drains on its own
void __cdecl st_close(unsigned long timeout_ms); // before unload: stop and join the writer
#ifdef __cplusplus
}
#endif
