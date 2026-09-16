/* Test-only original module: its epilogue must survive release during run. */
#include <windows.h>
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
