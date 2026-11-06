/* THE RENDERER'S SETTINGS FOLDER: where GLideN64 reads GLideN64.ini and
 * GLideN64.custom.ini and keeps its own files (shader storage, its log, the
 * texture-pack folders). Upstream it is always the folder the renderer DLL
 * sits in (PluginAPI::FindPluginPath in src/windows/CommonAPIImpl_windows.cpp,
 * which tools/build_renderer.py routes through named() below).
 *
 * Inside Project64 nothing calls the setter, so the folder stays the DLL's
 * own and live play is unchanged. A scene-lab host (tools/scene_lab/
 * viewer_host.c, replay_host.c) loads the renderer where the Trainer keeps
 * it and names a folder of its own here, so it never copies the renderer
 * beside a private ini (Defender flagged exactly that copy, 2026-11-06).
 *
 * SM64RendererSettingsFolderV1(folder) takes a drive-absolute path to an
 * existing directory, at most MAX_FOLDER characters, once, before the
 * renderer first asks for its folder (the first plugin call that reads
 * settings: RomOpen, DllConfig, DllAbout, a log line). It returns 1 when
 * taken and 0 when refused; a refusal leaves the renderer unchanged, so a
 * host that gets 0 must not draw. */
#pragma once
#include <cstddef>

namespace renderer_settings_folder {
// Room for the longest name GLideN64 appends inside its PLUGIN_PATH_SIZE (260) buffers.
constexpr size_t MAX_FOLDER = 196;

// The folder a host named ('/' separators, no trailing one) into `path`;
// false when none was named, and from then on the setter refuses.
bool named(wchar_t *path, size_t capacity);
}

extern "C" int __cdecl SM64RendererSettingsFolderV1(const wchar_t *folder);
