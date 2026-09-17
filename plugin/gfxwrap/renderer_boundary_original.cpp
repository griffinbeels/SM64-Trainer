/* Test-only renderer module: the bound surface provider, and a command whose
 * epilogue must survive FreeLibrary while it runs. */
#include <windows.h>
struct rb_surface;
class Original {
public:
    virtual bool run() { callback(this); return answer; }
    void (*callback)(void *);
    bool answer;
};
extern "C" __declspec(dllexport) void *create_command(void (*callback)(void *), int answer) {
    auto command = new Original;
    command->callback = callback; command->answer = answer != 0;
    return command;
}
extern "C" __declspec(dllexport) void destroy_command(void *pointer) {
    delete static_cast<Original *>(pointer);
}
extern "C" __declspec(dllexport) int __cdecl source_surface(rb_surface *) { return 1; }
