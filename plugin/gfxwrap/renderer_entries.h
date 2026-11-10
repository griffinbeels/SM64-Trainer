/* THE RENDERER INSIDE THE GRAPHICS PLUGIN: the link seam between the capture
 * layer (gfxwrap.c) and the renderer it forwards every Zilmar call to.
 *
 * Since 2026-11-07 the plugin Project64 loads is ONE file. The renderer is
 * linked into it, never loaded: gfxwrap.c binds `renderer_entries()` once, in
 * DllMain, and forwards through that table. Exactly one renderer is linked
 * into each plugin build:
 *
 *   - the shipped plugin: renderer_entries.c, staged into LINK's GLideN64 by
 *     tools/build_renderer.py, which renames upstream's Zilmar functions
 *     (src/ZilmarPluginAPI.cpp, src/CommonPluginAPI.cpp) to GLideN64_<name>
 *     and its DllMain (src/windows/GLideN64_windows.cpp) to GLideN64_DllMain,
 *     so only the capture layer exports the Zilmar names;
 *   - a test plugin (tools/build_plugin.py build_test_plugin): a stand-in
 *     renderer instead (fake_gfx.c, wrapper_runtime_original.cpp,
 *     tests/native/settings_folder_renderer.cpp). It never enters the bundle.
 *
 * A NULL entry is a call that renderer does not implement; the capture layer
 * skips it. `renderer_dll_main` runs inside the plugin's DllMain, under the
 * loader lock, for every reason: it may only record what DllMain may. */
#pragma once
#include "zilmar.h"

#ifdef __cplusplus
extern "C" {
#endif

const gfx_api_t *__cdecl renderer_entries(void);
BOOL WINAPI renderer_dll_main(HINSTANCE instance, DWORD reason, LPVOID reserved);

#ifdef __cplusplus
}
#endif
