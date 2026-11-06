/* How a scene-lab host loads THE RENDERER (viewer_host.c, replay_host.c).
 *
 * By its full path, where the Trainer keeps it (the bundle: replay.py's
 * trainer_renderer()), never a copy. GLideN64 reads its ini from a settings
 * folder, upstream always the DLL's own; the Trainer renderer takes another
 * through SM64RendererSettingsFolderV1 (plugin/gfxwrap/renderer_settings_folder.h),
 * named here before any plugin call. A renderer without that export (LINK's
 * own, an older Trainer build) would read and write the folder it sits in,
 * so it is refused and unloaded before it draws.
 *
 * Until 2026-11-06 each host copied the renderer into its private ini folder
 * under %TEMP% and loaded the copy. Defender's cloud model flagged exactly
 * that: an unknown program loading a DLL it had just written to a random
 * temp folder is how a dropper behaves. */
#pragma once
#include <windows.h>

#define RENDERER_SETTINGS_EXPORT "SM64RendererSettingsFolderV1"

typedef int (__cdecl *renderer_settings_folder_t)(const wchar_t *folder);

/* The loaded renderer, or NULL with *why naming the refusal. */
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
