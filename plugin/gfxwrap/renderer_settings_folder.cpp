#include <windows.h>
#include <cwchar>
#include "renderer_settings_folder.h"

namespace {
enum class State { open, named, closed };   // closed: the renderer looked first, upstream's folder for good
SRWLOCK lock = SRWLOCK_INIT;                 // constant-initialized: no constructor runs in DllMain
State state = State::open;
wchar_t folder_path[renderer_settings_folder::MAX_FOLDER + 1];
}

bool renderer_settings_folder::named(wchar_t *path, size_t capacity) {
    AcquireSRWLockExclusive(&lock);
    if (state == State::open) state = State::closed;
    const bool have = state == State::named && path && capacity > wcslen(folder_path);
    if (have) wcscpy_s(path, capacity, folder_path);
    ReleaseSRWLockExclusive(&lock);
    return have;
}

#pragma comment(linker, "/EXPORT:SM64RendererSettingsFolderV1=_SM64RendererSettingsFolderV1")
extern "C" int __cdecl SM64RendererSettingsFolderV1(const wchar_t *folder) {
    using renderer_settings_folder::MAX_FOLDER;
    if (!folder) return 0;
    const size_t length = wcsnlen(folder, MAX_FOLDER + 1);
    // Drive-absolute only: a relative path would follow the host's working folder.
    if (length < 3 || length > MAX_FOLDER || folder[1] != L':' || (folder[2] != L'\\' && folder[2] != L'/'))
        return 0;
    const DWORD attributes = GetFileAttributesW(folder);
    if (attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_DIRECTORY)) return 0;
    wchar_t clean[MAX_FOLDER + 1];
    wcscpy_s(clean, folder);
    size_t end = length;
    for (size_t at = 0; at < end; ++at)
        if (clean[at] == L'\\') clean[at] = L'/';   // FindPluginPath's own form: GLideN64 appends "/name"
    while (end > 3 && clean[end - 1] == L'/') clean[--end] = 0;
    AcquireSRWLockExclusive(&lock);
    const bool taken = state == State::open;
    if (taken) {
        wcscpy_s(folder_path, clean);
        state = State::named;
    }
    ReleaseSRWLockExclusive(&lock);
    return taken ? 1 : 0;
}
