/* Separate boundary + surface-provider DLLs, actual process-lifetime module pins. */
#include "renderer_boundary.h"
#include <cstdio>
#include <cstdlib>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "lifetime contract line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
HANDLE entered, release_original;
void *command;
rb_ticket request{};
bool result = false;
decltype(&rb_source_begin) source_begin;
decltype(&rb_source_end) source_end;
struct Command { virtual bool run() = 0; };
void held(void *self) {
    CHECK(self == command); SetEvent(entered);
    CHECK(WaitForSingleObject(release_original, 5000) == WAIT_OBJECT_0);
}
// The overlay's UpdateScreen command shape: begin, the original work, end.
DWORD WINAPI render(void *) {
    const auto capture = source_begin(request);
    result = static_cast<Command *>(command)->run();
    source_end(capture);
    return 0;
}
template<typename T> T proc(HMODULE module, const char *name) {
    auto pointer = GetProcAddress(module, name); CHECK(pointer);
    return reinterpret_cast<T>(pointer);
}
int wmain(int argc, wchar_t **argv) {
    CHECK(argc == 3);
    HMODULE boundary = LoadLibraryW(argv[1]), provider = LoadLibraryW(argv[2]);
    CHECK(boundary && provider);
    auto create = proc<void *(*)(void (*)(void *), int)>(provider, "create_command");
    auto destroy = proc<void (*)(void *)>(provider, "destroy_command");
    auto surface = proc<rb_source_surface>(provider, "source_surface");
    auto bind = proc<decltype(&rb_bind_source)>(boundary, "rb_bind_source");
    source_begin = proc<decltype(&rb_source_begin)>(boundary, "rb_source_begin");
    source_end = proc<decltype(&rb_source_end)>(boundary, "rb_source_end");
    auto open = proc<decltype(&rb_rom_open)>(boundary, "rb_rom_open");
    auto activate = proc<decltype(&rb_activate)>(boundary, "rb_activate");
    auto close = proc<decltype(&rb_close)>(boundary, "rb_close");
    auto stage = proc<decltype(&rb_stage)>(boundary, "rb_stage");
    auto finish = proc<decltype(&rb_finish)>(boundary, "rb_finish");
    auto take = proc<decltype(&rb_take)>(boundary, "rb_take");
    auto give_back = proc<decltype(&rb_release)>(boundary, "rb_release");
    command = create(held, 1); CHECK(command);
    CHECK(bind(surface) == RB_INSTALLED);
    CHECK(bind(surface) == RB_ALREADY_INSTALLED);
    open(); CHECK(activate());
    rb_stamp value{}; request = stage(&value); CHECK(request.occurrence);
    entered = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    release_original = CreateEventW(nullptr, FALSE, FALSE, nullptr); CHECK(entered && release_original);
    HANDLE thread = CreateThread(nullptr, 0, render, nullptr, 0, nullptr); CHECK(thread);
    CHECK(WaitForSingleObject(entered, 5000) == WAIT_OBJECT_0);
    close(); // no join, original remains deliberately held
    FreeLibrary(boundary); FreeLibrary(provider);
    CHECK(GetModuleHandleW(argv[1]) == boundary && GetModuleHandleW(argv[2]) == provider);
    SetEvent(release_original);
    CHECK(WaitForSingleObject(thread, 5000) == WAIT_OBJECT_0 && result);
    finish(request); auto record = take(); CHECK(record && record->outcome == RB_RETIRED);
    CHECK(give_back({record->occurrence, record->slot}));
    destroy(command);
    CloseHandle(thread); CloseHandle(entered); CloseHandle(release_original);
    std::puts("pinned-two-modules; delayed-original-returned; stale-capture-retired");
    return 0;
}
