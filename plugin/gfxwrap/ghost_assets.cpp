#include "ghost_assets.h"
#include "ghost_clip_upgrade.h"
#include <windows.h>
#include <bcrypt.h>
#include <cstring>
#include <new>
#pragma comment(lib, "bcrypt.lib")
namespace ghost_assets {
bool read(const uint16_t *path, uint32_t capacity, uint32_t maximum,
          std::vector<uint8_t> &out, bool *out_of_memory) {
    out.clear();
    uint32_t length = 0;
    if (!path || !capacity || !maximum) return false;
    while (length < capacity && path[length]) ++length;
    if (!length || length == capacity) return false;
    // Local absolute disk paths only. Never execute a URI or open a named pipe.
    if (length < 3 || path[1] != ':' || (path[2] != '\\' && path[2] != '/')) return false;
    HANDLE file = CreateFileW(reinterpret_cast<const wchar_t *>(path), GENERIC_READ,
        FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    bool ok = GetFileType(file) == FILE_TYPE_DISK && GetFileSizeEx(file, &size)
        && size.QuadPart > 0 && size.QuadPart <= maximum;
    if (ok) {
        try { out.resize(static_cast<size_t>(size.QuadPart)); }
        catch (const std::bad_alloc &) { if (out_of_memory) *out_of_memory = true; CloseHandle(file); return false; }
        catch (...) { CloseHandle(file); return false; }
        DWORD got = 0;
        ok = ReadFile(file, out.data(), static_cast<DWORD>(out.size()), &got, nullptr)
            && got == out.size();
    }
    CloseHandle(file);
    if (!ok) {
        // A failed read may still have filled part of the initialized buffer.
        // Do not leave a stale cartridge image in allocator-retained storage.
        if (!out.empty()) SecureZeroMemory(out.data(), out.size());
        out.clear();
    }
    return ok;
}
bool sha256(const std::vector<uint8_t> &data,uint8_t digest[32]) {
    if(!digest || data.size()>UINT32_MAX)return false;
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    bool ok = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0;
    if (ok) ok = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    if (ok) ok = BCryptHashData(hash, const_cast<PUCHAR>(data.data()),
                               static_cast<ULONG>(data.size()), 0) >= 0;
    if (ok) ok = BCryptFinishHash(hash, digest, 32, 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}
bool clip(std::vector<uint8_t> &data, const uint8_t expected[32],
          const uint8_t id[16], gclip_header &out, bool *out_of_memory) {
    out = {};
    if (data.size() < sizeof(out)) return false;
    // The digest names the file as saved; a GCLIP4 file is then read as GCLIP5.
    uint8_t digest[32]{};
    if (!sha256(data,digest) || std::memcmp(digest, expected, sizeof(digest))) return false;
    bool upgrade_memory = false;
    ghost_clip::upgrade_v4(data, &upgrade_memory);
    if (upgrade_memory) { if (out_of_memory) *out_of_memory = true; return false; }
    gclip_header header{}; std::memcpy(&header, data.data(), sizeof(header));
    if (std::memcmp(header.magic, GCLIP_MAGIC, 8) || header.version != GCLIP_VERSION
            || header.bytes != sizeof(header) || header.record_bytes != sizeof(gclip_frame)
            || !header.record_count || header.record_count > GCLIP_MAX_RECORDS
            || header.sample_version != GC_VERSION || header.rom_layout != GC_ROM_US
            || data.size() != sizeof(header) + static_cast<size_t>(header.record_count) * sizeof(gclip_frame)
            || std::memcmp(header.clip_id, id, 16)) return false;
    out = header; return true;
}
}
