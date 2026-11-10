// SPDX-License-Identifier: MIT
// Original standalone probe code. See this directory's LICENSE.
#pragma once
#include <windows.h>
#include <psapi.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <vector>

namespace eos_probe {
using Clock = std::chrono::steady_clock;
inline uint64_t filetime_ticks(const FILETIME& value) {
    return (static_cast<uint64_t>(value.dwHighDateTime) << 32) | value.dwLowDateTime;
}
inline double process_cpu_seconds() {
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) return -1;
    return static_cast<double>(filetime_ticks(kernel) + filetime_ticks(user)) / 10000000.0;
}
struct Measurements {
    Clock::time_point started = Clock::now();
    double cpu_start = process_cpu_seconds();
    std::vector<double> tick_us;
    DWORD handles_max = 0;
    size_t resident_max = 0, private_max = 0;
    uint64_t outgoing_packets_max = 0, incoming_packets_max = 0;
    uint64_t outgoing_bytes_max = 0, incoming_bytes_max = 0;
    bool process_measurement_failed = false;

    Measurements() { tick_us.reserve(20000); }
    void process_sample() {
        PROCESS_MEMORY_COUNTERS_EX memory{};
        memory.cb = sizeof(memory);
        DWORD handles = 0;
        if (!GetProcessMemoryInfo(GetCurrentProcess(),
                reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&memory), sizeof(memory)) ||
                !GetProcessHandleCount(GetCurrentProcess(), &handles)) {
            process_measurement_failed = true;
            return;
        }
        resident_max = (std::max)(resident_max, memory.WorkingSetSize);
        private_max = (std::max)(private_max, memory.PrivateUsage);
        handles_max = (std::max)(handles_max, handles);
    }
    void tick_sample(Clock::time_point before) {
        if (tick_us.size() < 20000)
            tick_us.push_back(std::chrono::duration<double, std::micro>(Clock::now() - before).count());
    }
    void report() {
        process_sample();
        const double elapsed = std::chrono::duration<double>(Clock::now() - started).count();
        const double cpu_end = process_cpu_seconds();
        std::sort(tick_us.begin(), tick_us.end());
        auto percentile = [&](double fraction) {
            return tick_us.empty() ? 0.0 : tick_us[static_cast<size_t>((tick_us.size() - 1) * fraction)];
        };
        std::cout << "{\"report_version\":2,\"probe_version\":2,\"event\":\"measurements\",\"ticks\":" << tick_us.size()
            << ",\"tick_p50_us\":" << percentile(.50) << ",\"tick_p95_us\":" << percentile(.95)
            << ",\"tick_p99_us\":" << percentile(.99) << ",\"tick_max_us\":" << percentile(1)
            << ",\"wall_seconds\":" << elapsed
            << ",\"process_cpu_seconds\":" << ((cpu_start >= 0 && cpu_end >= 0) ? cpu_end - cpu_start : -1)
            << ",\"resident_peak_bytes\":" << resident_max << ",\"private_peak_bytes\":" << private_max
            << ",\"handle_peak\":" << handles_max << ",\"incoming_packets_peak\":" << incoming_packets_max
            << ",\"outgoing_packets_peak\":" << outgoing_packets_max
            << ",\"incoming_bytes_peak\":" << incoming_bytes_max
            << ",\"outgoing_bytes_peak\":" << outgoing_bytes_max
            << ",\"queue_packet_age_known\":false"
            << ",\"process_measurement_failed\":" << process_measurement_failed << "}\n";
    }
};
}  // namespace eos_probe
