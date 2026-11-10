/* The scene tap: a recorder of graphics-task closures and the game state the
 * scene renderer reads (M4). Idle and allocation-free unless the Trainer holds
 * the live spool's lease (or, in the offline host's build only, a one-shot
 * arming file exists when its ROM opens); see scene_tap.cpp. */
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
/* Test hosts only (tools/scene_lab/tap_offline_host.cpp), never Project64: */
void __cdecl st_live_any_process(void);         // let a host that is not Project64.exe take the lease
int __cdecl st_live_poke(unsigned long timeout_ms);  // check the lease, drain, write rows now; 1 capturing
void __cdecl st_live_hold(int hold);            // stop (1) or resume (0) draining the ring
#ifdef __cplusplus
}
#endif
