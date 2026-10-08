#include <windows.h>
#include "ghost_assets.h"
#include <cstdio>
#include <vector>

int wmain(int argc, wchar_t **argv) {
    if (argc < 3) return 90;
    // Deliberately built without a manifest: match PJ64's legacy Win32 host.
    HANDLE plain = CreateFileW(argv[2], GENERIC_READ, FILE_SHARE_READ, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    const DWORD error = plain == INVALID_HANDLE_VALUE ? GetLastError() : 0;
    if (plain != INVALID_HANDLE_VALUE) CloseHandle(plain);
    std::printf("plain_error=%lu\n", error);
    std::vector<uint8_t> bytes;
    for (int i = 1; i < argc; ++i) {
        const bool ok = ghost_assets::read(reinterpret_cast<const uint16_t *>(argv[i]),
            520, 65536, bytes);
        std::printf("%u %zu\n", unsigned(ok), bytes.size());
    }
    const uint16_t unterminated[] = {'C', ':', '\\'};
    if (ghost_assets::read(nullptr, 520, 65536, bytes)
        || ghost_assets::read(unterminated, 3, 65536, bytes)
        || ghost_assets::read(reinterpret_cast<const uint16_t *>(argv[1]), 0, 65536, bytes)
        || ghost_assets::read(reinterpret_cast<const uint16_t *>(argv[1]), 520, 0, bytes)) return 91;
    return 0;
}
