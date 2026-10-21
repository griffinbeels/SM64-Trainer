// SPDX-License-Identifier: MIT
// Original standalone probe code. See this directory's LICENSE.
#pragma once
#include <windows.h>
#include <sddl.h>
#include <array>
#include <filesystem>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>

namespace eos_probe {
inline void require(bool condition) {
    if (!condition) throw std::runtime_error("probe operation failed");
}

// Configuration errors deliberately carry no input bytes or file paths.
inline std::map<std::string, std::string> read_fields(const std::filesystem::path& path) {
    std::ifstream file(path, std::ios::binary);
    require(file.good());
    std::array<char, 4097> bytes{};
    file.read(bytes.data(), bytes.size());
    const auto size = static_cast<size_t>(file.gcount());
    require(size > 0 && size < bytes.size() && !file.bad());
    std::string body(bytes.data(), size);
    SecureZeroMemory(bytes.data(), bytes.size());
    if (body.compare(0, 3, "\xef\xbb\xbf") == 0) body.erase(0, 3);
    std::map<std::string, std::string> fields;
    size_t begin = 0;
    while (begin < body.size()) {
        auto end = body.find('\n', begin);
        if (end == std::string::npos) end = body.size();
        std::string row = body.substr(begin, end - begin);
        if (!row.empty() && row.back() == '\r') row.pop_back();
        require(!row.empty() && row.size() <= 320);
        const auto equal = row.find('=');
        require(equal != std::string::npos && equal > 0 && equal + 1 < row.size());
        for (unsigned char ch : row) require(ch >= 33 && ch <= 126);
        require(fields.emplace(row.substr(0, equal), row.substr(equal + 1)).second);
        SecureZeroMemory(row.data(), row.size());
        begin = end + 1;
    }
    SecureZeroMemory(body.data(), body.size());
    return fields;
}

struct Configuration {
    std::map<std::string, std::string> values;
    explicit Configuration(const std::filesystem::path& path) : values(read_fields(path)) {
        const std::array<std::string, 5> names{
            "product_id", "sandbox_id", "deployment_id", "client_id", "client_secret"};
        require(values.size() == names.size());
        for (const auto& name : names) {
            const auto found = values.find(name);
            require(found != values.end());
            const auto limit = name == "client_secret" ? 256u : 64u;
            require(!found->second.empty() && found->second.size() <= limit);
            if (name != "client_secret") {
                for (unsigned char ch : found->second)
                    require((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                            (ch >= '0' && ch <= '9') || ch == '-' || ch == '_');
            }
        }
    }
    Configuration(const Configuration&) = delete;
    Configuration& operator=(const Configuration&) = delete;
    ~Configuration() {
        for (auto& entry : values) SecureZeroMemory(entry.second.data(), entry.second.size());
    }
    const char* get(const char* name) const { return values.at(name).c_str(); }
};

inline bool hexadecimal(const std::string& value, size_t length) {
    if (value.size() != length) return false;
    for (unsigned char ch : value)
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') ||
              (ch >= 'A' && ch <= 'F'))) return false;
    return true;
}

struct Ticket {
    std::string puid, lobby, socket;
    explicit Ticket(const std::filesystem::path& path) {
        auto fields = read_fields(path);
        require(fields.size() == 2 || fields.size() == 4);
        require(fields["version"] == "2" && hexadecimal(fields["puid"], 32));
        puid = fields.at("puid");
        if (fields.size() == 4) {
            lobby = fields.at("lobby");
            socket = fields.at("socket");
            require(lobby.size() <= 128 && hexadecimal(socket, 32));
        }
    }
};

// An explicit ticket is private runtime state. Never overwrite another ticket.
// Protected DACL: only the file owner receives access; no inherited public ACL.
inline void write_ticket(const std::filesystem::path& path, const std::string& body) {
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    require(ConvertStringSecurityDescriptorToSecurityDescriptorW(
        L"D:P(A;;GA;;;OW)", SDDL_REVISION_1, &descriptor, nullptr) != 0);
    SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), descriptor, FALSE};
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, &attributes, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    LocalFree(descriptor);
    require(file != INVALID_HANDLE_VALUE);
    DWORD written = 0;
    const bool okay = body.size() <= 4096 && WriteFile(file, body.data(),
        static_cast<DWORD>(body.size()), &written, nullptr) && written == body.size();
    const bool closed = CloseHandle(file) != 0;
    require(okay && closed);
}
}  // namespace eos_probe
