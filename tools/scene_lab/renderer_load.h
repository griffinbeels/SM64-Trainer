/* How a scene-lab host loads THE RENDERER (viewer_host.c, replay_host.c).
 *
 * The renderer is inside the Trainer's graphics plugin, sm64_trainer_gfx.dll
 * (one file since 2026-11-07). The host loads that file by its full path,
 * where the Trainer keeps it (the bundle: replay.py's trainer_renderer()),
 * never a copy, and before any plugin call asks for two things:
 *
 *   SM64RendererOnlyV1(1) (plugin/gfxwrap/gfxwrap.c): every call then goes
 *     straight to the renderer, with no control page, stamp adapter, scene tap
 *     or plugin log, so a lab host never publishes a page beside a running
 *     Project64's;
 *   SM64RendererSettingsFolderV1(folder) (renderer_settings_folder.h):
 *     GLideN64 reads its ini from a settings folder, upstream always the
 *     DLL's own, here the lab's.
 *
 * A DLL without either export (LINK's own renderer, an older Trainer build,
 * whose renderer was a second file) would capture or read and write the
 * folder it sits in, so it is refused and unloaded before it draws.
 *
 * Until 2026-11-06 each host copied the renderer into its private ini folder
 * under %TEMP% and loaded the copy. Defender's cloud model flagged exactly
 * that: an unknown program loading a DLL it had just written to a random
 * temp folder is how a dropper behaves. */
#pragma once
#include <windows.h>

#define RENDERER_ONLY_EXPORT "SM64RendererOnlyV1"
#define RENDERER_SETTINGS_EXPORT "SM64RendererSettingsFolderV1"

typedef int (__cdecl *renderer_only_t)(unsigned int version);
typedef int (__cdecl *renderer_settings_folder_t)(const wchar_t *folder);

/* The loaded plugin, its renderer alone, or NULL with *why naming the refusal. */
static HMODULE renderer_load(const char *renderer, const char *settings, const char **why) {
    char path[MAX_PATH];
    wchar_t folder[MAX_PATH];
    DWORD length = GetFullPathNameA(renderer, MAX_PATH, path, NULL);
    if (!length || length >= MAX_PATH) {
        *why = "the renderer path is empty or too long";
        return NULL;
    }
    if (!MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, settings, -1, folder, MAX_PATH)) {
        *why = "the settings folder path is too long";
        return NULL;
    }
    HMODULE module = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) {
        *why = "LoadLibrary of the renderer failed";
        return NULL;
    }
    renderer_only_t alone = (renderer_only_t)GetProcAddress(module, RENDERER_ONLY_EXPORT);
    if (!alone) {
        FreeLibrary(module);
        *why = "the plugin cannot run its renderer alone (no " RENDERER_ONLY_EXPORT
               "): only the Trainer's graphics plugin is drawn here, loaded where it is";
        return NULL;
    }
    if (!alone(1)) {
        FreeLibrary(module);
        *why = "the plugin refused " RENDERER_ONLY_EXPORT " (it was already initialized)";
        return NULL;
    }
    renderer_settings_folder_t name =
        (renderer_settings_folder_t)GetProcAddress(module, RENDERER_SETTINGS_EXPORT);
    if (!name) {
        FreeLibrary(module);
        *why = "the renderer cannot take a settings folder (no " RENDERER_SETTINGS_EXPORT
               "): only the Trainer's renderer is drawn here, loaded where it is";
        return NULL;
    }
    if (!name(folder)) {
        FreeLibrary(module);
        *why = "the renderer refused the settings folder (it must be an existing, drive-absolute "
               "folder of at most 196 characters)";
        return NULL;
    }
    return module;
}
