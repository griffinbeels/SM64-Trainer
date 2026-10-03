/* THE OVERLAY WARM-UP (2026-10-03). His report: "the entire picture freezes",
 * and "we shouldn't assume that players will disable [the Discord overlay],
 * because most people use it!!!" Discord's hook attaches 2-33 s into play and,
 * on the next present, makes a D3D11 device plus NV interop inside
 * SwapBuffers on the renderer thread: 33-135 ms frozen, matched live to its
 * own log in every launch (docs/replay-gpu-runtime.md, "First capture
 * freeze"). Most of that is the driver's one-time start-up for the first
 * D3D11 device made while a GL renderer is running. A fresh-process host doing
 * the same init on a busy hidden GL renderer (3 runs per arm, this machine)
 * froze 96-114 ms with nothing before it, and 22-33 ms when a D3D11 device had
 * been made earlier in the process after rendering began (as soon as 50 ms
 * in). Loading d3d11.dll and dxgi.dll alone saved nothing (93-99 ms), and a
 * device made at context creation, before the first picture, saved nothing
 * either (100-101 ms): the start-up is paid by the first device made once
 * the renderer draws.
 *
 * So the wrapper makes one D3D11 device, on the renderer's own adapter and
 * thread, the first time the game stops drawing for HOLD_VIS VIs (a load or
 * reset: the picture is already still) after SETTLE_LISTS pictures. If that
 * VI did not take ABSORBED_MS, the driver had nothing to start yet, and the
 * next hold tries again, at most MOST_ATTEMPTS times. Devices are kept for the
 * life of Project64 and never used. Practice ROM only: gfxwrap.c calls these
 * on its practice-ROM path, and every other ROM keeps the plain renderer.
 * Every call belongs to the emulation thread. */
#pragma once
#include <stdint.h>

#define OW_HOLD_VIS 6         /* VIs in a row without a new picture: the game is loading */
#define OW_SETTLE_LISTS 30    /* pictures drawn first, so the driver is running the renderer */
#define OW_ABSORBED_MS 40.0   /* a warm VI this long paid the driver's start-up */
#define OW_MOST_ATTEMPTS 3

#ifdef __cplusplus
extern "C" {
#endif
typedef struct ow_stats {
    uint32_t lists, attempts, devices, absorbed;
    double last_vi_ms; /* the VI of the latest attempt */
} ow_stats;
/* A list drew a new picture. */
void ow_list(void);
/* Start of UpdateScreen, the renderer's context current: 1 when this VI warms. */
int ow_vi(void);
/* After the forwarded UpdateScreen of a VI ow_vi warmed on: its duration. */
void ow_vi_done(double milliseconds);
/* RomOpen: count pictures and holds again; devices already made stay. */
void ow_reset(void);
ow_stats ow_read(void);
/* Tests only: what makes a device (1 made, 0 not); null restores the real one. */
void ow_set_maker(int (*maker)(void));
#ifdef __cplusplus
}
#endif
