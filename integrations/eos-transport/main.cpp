// SPDX-License-Identifier: MIT
#include "network.hpp"
#include <cwchar>

int wmain(int argc, wchar_t** argv) {
    using namespace eos_transport;
    if (argc == 2 && std::wcscmp(argv[1], L"--help") == 0) return 0;
    try {
        need(argc >= 5 && argc % 2 == 1);
        std::map<std::wstring, std::wstring> arguments;
        for (int at = 1; at < argc; at += 2) need(arguments.emplace(argv[at], argv[at + 1]).second);
        need(arguments.size() == 2 || arguments.size() == 3);
        need(arguments.find(L"--config") != arguments.end() && arguments.find(L"--parent-pid") != arguments.end());
        for (const auto& argument : arguments) need(argument.first == L"--config" || argument.first == L"--parent-pid" || argument.first == L"--cache-dir");
        uint64_t parent = 0; const auto& raw_parent = arguments.at(L"--parent-pid"); need(!raw_parent.empty() && raw_parent.size() <= 10);
        for (wchar_t value : raw_parent) { need(value >= L'0' && value <= L'9'); parent = parent * 10 + value - L'0'; need(parent <= MAXDWORD); } need(parent != 0);
        Pipes pipes;
        try {
            int result = 2;
            {
                Engine engine(arguments.at(L"--config"), arguments.count(L"--cache-dir") ? arguments.at(L"--cache-dir") : L"", static_cast<DWORD>(parent), pipes);
                result = engine.run();
            }
            pipes.flush(250);
            return result;
        } catch (...) { pipes.emit(event("fatal", ",\"code\":\"initialization_failed\"")); Sleep(20); return 2; }
    } catch (...) { return 2; }  // No raw arguments/config/SDK output, including early startup errors.
}
