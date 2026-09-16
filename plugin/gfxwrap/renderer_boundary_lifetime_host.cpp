/* Separate original + adapter DLLs, actual process-lifetime module pins. */
#include "renderer_boundary.h"
#include <cstdio>
#include <cstdlib>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "lifetime contract line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
HANDLE entered, release_original;
void *command;
bool result = false;
struct Command { virtual bool run() = 0; };
void held(void *self) {
    CHECK(self == command); SetEvent(entered);
    CHECK(WaitForSingleObject(release_original, 5000) == WAIT_OBJECT_0);
}
DWORD WINAPI render(void *) { result = static_cast<Command *>(command)->run(); return 0; }
int surface(rb_surface *) { return 1; }
template<typename T> T proc(HMODULE module, const char *name) {
    auto pointer = GetProcAddress(module, name); CHECK(pointer);
    return reinterpret_cast<T>(pointer);
}
int wmain(int argc, wchar_t **argv) {
    CHECK(argc == 3);
    HMODULE adapter = LoadLibraryW(argv[1]), original = LoadLibraryW(argv[2]);
    CHECK(adapter && original);
    auto create = proc<void *(*)(void (*)(void *), int)>(original, "create_command");
    auto destroy = proc<void (*)(void *)>(original, "destroy_command");
    auto install = proc<decltype(&rb_install_test_pinned)>(adapter, "rb_install_test_pinned");
    auto open = proc<decltype(&rb_rom_open)>(adapter, "rb_rom_open");
    auto activate = proc<decltype(&rb_activate)>(adapter, "rb_activate");
    auto close = proc<decltype(&rb_close)>(adapter, "rb_close");
    auto stage = proc<decltype(&rb_stage)>(adapter, "rb_stage");
    auto finish = proc<decltype(&rb_finish)>(adapter, "rb_finish");
    auto take = proc<decltype(&rb_take)>(adapter, "rb_take");
    auto give_back = proc<decltype(&rb_release)>(adapter, "rb_release");
    command = create(held, 1); CHECK(command);
    auto table = *reinterpret_cast<void ***>(command);
    CHECK(install(table, *table, surface, original, GetModuleHandleW(nullptr)) == RB_PIN_FAILED);
    CHECK(install(table, *table, surface, original, adapter) == RB_INSTALLED);
    CHECK(install(table, *table, surface, original, adapter) == RB_ALREADY_INSTALLED);
    open(); CHECK(activate());
    rb_stamp value{}; auto id = stage(&value); CHECK(id.occurrence);
    entered = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    release_original = CreateEventW(nullptr, FALSE, FALSE, nullptr); CHECK(entered && release_original);
    HANDLE thread = CreateThread(nullptr, 0, render, nullptr, 0, nullptr); CHECK(thread);
    CHECK(WaitForSingleObject(entered, 5000) == WAIT_OBJECT_0);
    close(); // no join, original remains deliberately held
    FreeLibrary(adapter); FreeLibrary(original);
    CHECK(GetModuleHandleW(argv[1]) == adapter && GetModuleHandleW(argv[2]) == original);
    SetEvent(release_original);
    CHECK(WaitForSingleObject(thread, 5000) == WAIT_OBJECT_0 && result);
    finish(id); auto record = take(); CHECK(record && record->outcome == RB_RETIRED);
    CHECK(give_back({record->occurrence, record->slot}));
    destroy(command);
    CloseHandle(thread); CloseHandle(entered); CloseHandle(release_original);
    std::puts("pinned-two-modules; delayed-original-returned; stale-capture-retired");
    return 0;
}
