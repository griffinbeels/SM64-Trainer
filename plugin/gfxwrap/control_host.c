/* CPU-only IPC/lifecycle host. No windows, GL context or emulator. */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "zilmar.h"
static unsigned char ram[256];
static DWORD reg;
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    HMODULE module = LoadLibraryA(argv[1]);
    if (!module) return 3;
    gfx_api_t api;
    RESOLVE_GFX_API(api, module);
    GFX_INFO info = {0};
    info.MemoryBswaped = TRUE;
    info.RDRAM = info.DMEM = info.IMEM = info.HEADER = ram;
    info.VI_ORIGIN_REG = info.VI_STATUS_REG = info.VI_WIDTH_REG = &reg;
    BOOL opened = api.InitiateGFX(info);
    if (!opened) return 4;
    api.RomOpen();
    puts("ready"); fflush(stdout);
    char command[40];
    while (fgets(command, sizeof command, stdin)) {
        if (!strncmp(command, "quit", 4)) break;
        if (!strncmp(command, "restart", 7)) {
            /* Deliberately omit RomClosed: CloseDLL must clear ROM state. */
            api.CloseDLL();
            opened = api.InitiateGFX(info);
            if (!opened) return 5;
        } else if (!strncmp(command, "close", 5)) {
            api.CloseDLL(); opened = FALSE;
        } else if (!strncmp(command, "fail-reinit", 11)) {
            SetEnvironmentVariableA("SM64_FAKE_INIT_FAIL", "1");
            opened = api.InitiateGFX(info);
            SetEnvironmentVariableA("SM64_FAKE_INIT_FAIL", NULL);
            if (opened) return 6;
        } else if (!strncmp(command, "frames", 6)) {
            for (unsigned n = 0; n < 10000; ++n) {
                api.ProcessDList(); ++reg; api.UpdateScreen(); api.ProcessRDPList();
            }
        }
        puts("ok"); fflush(stdout);
    }
    api.CloseDLL();
    FreeLibrary(module);
    return 0;
}
