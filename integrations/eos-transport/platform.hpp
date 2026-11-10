// SPDX-License-Identifier: MIT
// Original Windows plumbing for private parent pipes and bounded configuration.
#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "json.hpp"
#include <array>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <thread>

namespace eos_transport {
inline uint64_t qpc() { LARGE_INTEGER value{}; need(QueryPerformanceCounter(&value) != 0); return static_cast<uint64_t>(value.QuadPart); }
inline uint64_t qpc_frequency() { LARGE_INTEGER value{}; need(QueryPerformanceFrequency(&value) != 0 && value.QuadPart > 0); return static_cast<uint64_t>(value.QuadPart); }
inline std::string utf8(const std::filesystem::path& path) {
    const auto& wide = path.native();
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(), static_cast<int>(wide.size()), nullptr, 0, nullptr, nullptr);
    need(size > 0 && size <= 32760); std::string result(size, '\0');
    need(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(), static_cast<int>(wide.size()), result.data(), size, nullptr, nullptr) == size); return result;
}
class Configuration {
    std::map<std::string, std::string> values;
public:
    explicit Configuration(const std::filesystem::path& path) {
        std::ifstream file(path, std::ios::binary); need(file.good());
        std::array<char, 4097> bytes{}; file.read(bytes.data(), bytes.size());
        const auto size = static_cast<size_t>(file.gcount()); need(size && size < bytes.size() && !file.bad());
        std::string body(bytes.data(), size); SecureZeroMemory(bytes.data(), bytes.size());
        if (body.compare(0, 3, "\xef\xbb\xbf") == 0) body.erase(0, 3);
        size_t start = 0;
        while (start < body.size()) {
            auto end = body.find('\n', start); if (end == std::string::npos) end = body.size();
            std::string row = body.substr(start, end - start); if (!row.empty() && row.back() == '\r') row.pop_back();
            const auto equals = row.find('='); need(!row.empty() && row.size() <= 320 && equals && equals != std::string::npos && equals + 1 < row.size());
            for (unsigned char value : row) need(value >= 33 && value <= 126);
            need(values.emplace(row.substr(0, equals), row.substr(equals + 1)).second);
            SecureZeroMemory(row.data(), row.size()); start = end + 1;
        }
        SecureZeroMemory(body.data(), body.size());
        need(values.size() == 5);
        for (const char* name : {"product_id", "sandbox_id", "deployment_id", "client_id", "client_secret"}) {
            const auto found = values.find(name); need(found != values.end() && !found->second.empty());
            const bool secret = std::string_view(name) == "client_secret";
            need(found->second.size() <= (secret ? 256u : 64u));
            if (!secret) for (char value : found->second)
                need((value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') || (value >= '0' && value <= '9') || value == '-' || value == '_');
        }
    }
    ~Configuration() { for (auto& entry : values) SecureZeroMemory(entry.second.data(), entry.second.size()); }
    const char* get(const char* name) const { return values.at(name).c_str(); }
    Configuration(const Configuration&) = delete;
    Configuration& operator=(const Configuration&) = delete;
};

class Pipes {
    struct Latest { uint64_t peer = 0; std::string line; };
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE), output = GetStdHandle(STD_OUTPUT_HANDLE);
    std::thread reader, writer;
    std::mutex mutex;
    std::condition_variable changed;
    std::deque<std::string> incoming, outgoing;
    std::array<Latest, 3> latest_in{}, latest_out{};
    size_t next_in = 0, next_out = 0;
    bool writing = false;
    std::atomic<bool> stopping{false};
    void replace(std::array<Latest, 3>& slots, uint64_t peer, std::string line) {
        for (auto& slot : slots) if (slot.peer == peer && !slot.line.empty()) { slot = Latest{peer, std::move(line)}; return; }
        for (auto& slot : slots) if (slot.line.empty()) { slot = Latest{peer, std::move(line)}; return; }
        // The private parent may replace old membership refs; do not grow state.
        slots[0] = Latest{peer, std::move(line)};
    }
    void read_loop() noexcept {
        try {
            std::array<char, 512> bytes{}; std::string line;
            while (!stopping) {
                DWORD received = 0;
                if (!ReadFile(input, bytes.data(), static_cast<DWORD>(bytes.size()), &received, nullptr) || !received) { eof = true; break; }
                for (DWORD at = 0; at < received; ++at) {
                    if (bytes[at] != '\n') { need(line.size() < LineLimit); line += bytes[at]; continue; }
                    const auto object = Parser(line).parse(); need(number(object, "v") == 1);
                    const auto operation = text(object, "op");
                    std::lock_guard<std::mutex> lock(mutex);
                    if (operation == "heartbeat") { need(object.size() == 2); heartbeat_qpc = qpc(); }
                    else if (operation == "send_latest") replace(latest_in, number(object, "peer_ref"), std::move(line));
                    else { need(incoming.size() < 32); incoming.push_back(std::move(line)); }
                    line.clear();
                }
            }
        } catch (...) { failed = true; }
    }
    void write_loop() noexcept {
        try {
            while (!stopping) {
                std::string line;
                {
                    std::unique_lock<std::mutex> lock(mutex);
                    changed.wait_for(lock, std::chrono::milliseconds(100), [&] {
                        if (stopping || !outgoing.empty()) return true;
                        for (const auto& slot : latest_out) if (!slot.line.empty()) return true;
                        return false;
                    });
                    if (stopping) break;
                    if (!outgoing.empty()) { line = std::move(outgoing.front()); outgoing.pop_front(); }
                    else for (size_t count = 0; count < latest_out.size(); ++count) {
                        auto& slot = latest_out[next_out]; next_out = (next_out + 1) % latest_out.size();
                        if (!slot.line.empty()) { line = std::move(slot.line); slot = {}; break; }
                    }
                    writing = !line.empty();
                }
                if (line.empty()) continue;
                line += '\n'; DWORD written = 0;
                need(WriteFile(output, line.data(), static_cast<DWORD>(line.size()), &written, nullptr) && written == line.size());
                { std::lock_guard<std::mutex> lock(mutex); writing = false; }
            }
        } catch (...) { failed = true; }
    }
public:
    std::atomic<bool> failed{false}, eof{false};
    std::atomic<uint64_t> heartbeat_qpc{0};
    Pipes() {
        need(GetFileType(input) == FILE_TYPE_PIPE && GetFileType(output) == FILE_TYPE_PIPE);
        heartbeat_qpc = qpc();
        try { reader = std::thread([&] { read_loop(); }); writer = std::thread([&] { write_loop(); }); }
        catch (...) { stopping = true; if (reader.joinable()) { CancelSynchronousIo(reader.native_handle()); reader.join(); } throw; }
    }
    ~Pipes() {
        stopping = true; changed.notify_all();
        if (reader.joinable()) { CancelSynchronousIo(reader.native_handle()); reader.join(); }
        if (writer.joinable()) { CancelSynchronousIo(writer.native_handle()); writer.join(); }
    }
    bool take(std::string& line) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!incoming.empty()) { line = std::move(incoming.front()); incoming.pop_front(); return true; }
        for (size_t count = 0; count < latest_in.size(); ++count) {
            auto& slot = latest_in[next_in]; next_in = (next_in + 1) % latest_in.size();
            if (!slot.line.empty()) { line = std::move(slot.line); slot = {}; return true; }
        }
        return false;
    }
    void emit(std::string line, uint64_t latest_peer = 0) {
        need(line.size() <= LineLimit);
        std::lock_guard<std::mutex> lock(mutex);
        if (latest_peer) replace(latest_out, latest_peer, std::move(line));
        else if (outgoing.size() < 64) outgoing.push_back(std::move(line));
        else failed = true;
        changed.notify_one();
    }
    void discard_data(uint64_t peer) {
        std::lock_guard<std::mutex> lock(mutex);
        for (auto& slot : latest_out) if (slot.peer == peer) slot = {};
        for (auto& slot : latest_in) if (slot.peer == peer) slot = {};
    }
    void flush(unsigned milliseconds) {
        const auto until = GetTickCount64() + milliseconds;
        while (!failed && GetTickCount64() < until) {
            { std::lock_guard<std::mutex> lock(mutex); if (outgoing.empty() && !writing) return; }
            Sleep(2);
        }
    }
};
}  // namespace eos_transport
