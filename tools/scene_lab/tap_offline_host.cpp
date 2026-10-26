/* The scene tap without Project64 (E2-live's offline proof): feeds frame
 * inputs (savestate_frames.py format) through the REAL producer in
 * plugin/gfxwrap/scene_tap.cpp, so a tap session recorded here is exactly what
 * the live tap would have written for that RDRAM: same walker, same regions,
 * same record format, same timings measured on this machine.
 *
 *   tap_offline_host <lab_root> [vi:]<frame_dir>...
 *
 * <lab_root> stands in for %LOCALAPPDATA%: the arming file must already be at
 * <lab_root>\SM64Trainer\scene-lab\armed.txt (tools/scene_lab/tap.py offline
 * writes it), and the session lands beside it. Frames are fed in the order
 * given, one ProcessDList each; repeat a folder to repeat a task. A folder
 * prefixed vi: is one UpdateScreen instead (a VI with no new task), which is
 * where the tap's VI regions are copied. Exit 0 when
 * every frame was offered to an armed tap; the session's summary.txt says
 * what it recorded. */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>
#include "scene_tap.h"

namespace {

std::vector<uint8_t> read_file(const std::string &path) {
    std::vector<uint8_t> data;
    FILE *file = fopen(path.c_str(), "rb");
    if (!file) return data;
    fseek(file, 0, SEEK_END);
    const long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    if (size > 0) {
        data.resize((size_t)size);
        if (fread(data.data(), 1, data.size(), file) != data.size()) data.clear();
    }
    fclose(file);
    return data;
}

// frame.json is ours (scene-lab-frame/1): one hex string, flat numbers.
bool json_hex(const std::string &json, const char *key, uint8_t *out, size_t bytes) {
    const size_t at = json.find(std::string("\"") + key + "\": \"");
    if (at == std::string::npos) return false;
    const size_t start = json.find('"', json.find(':', at)) + 1;
    for (size_t i = 0; i < bytes; ++i) {
        unsigned value = 0;
        if (sscanf(json.c_str() + start + 2 * i, "%2x", &value) != 1) return false;
        out[i] = (uint8_t)value;
    }
    return true;
}

unsigned json_number(const std::string &json, const char *key) {
    const size_t at = json.find(std::string("\"") + key + "\":");
    return at == std::string::npos ? 0u : (unsigned)strtoul(json.c_str() + json.find(':', at) + 1, nullptr, 10);
}

}  // namespace

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: tap_offline_host <lab_root> <frame_dir>...\n");
        return 2;
    }
    if (!SetEnvironmentVariableA("LOCALAPPDATA", argv[1])) return 2;
    static uint8_t dmem[0x1000], imem[0x1000], header[0x40];
    static unsigned int regs[32];
    uint8_t *rdram = (uint8_t *)VirtualAlloc(nullptr, 8u << 20, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!rdram) return 2;
    const std::string first = std::string(argv[2]) + "\\frame.json";
    std::vector<uint8_t> meta = read_file(first);
    if (meta.empty() || !json_hex(std::string(meta.begin(), meta.end()), "rom_header", header, sizeof header)) {
        fprintf(stderr, "%s: no rom_header\n", first.c_str());
        return 2;
    }
    GFX_INFO info = {};
    info.MemoryBswaped = TRUE;
    info.HEADER = header;
    info.RDRAM = rdram;
    info.DMEM = dmem;
    info.IMEM = imem;
    unsigned int **slots[] = {&info.MI_INTR_REG, &info.DPC_START_REG, &info.DPC_END_REG, &info.DPC_CURRENT_REG,
        &info.DPC_STATUS_REG, &info.DPC_CLOCK_REG, &info.DPC_BUFBUSY_REG, &info.DPC_PIPEBUSY_REG,
        &info.DPC_TMEM_REG, &info.VI_STATUS_REG, &info.VI_ORIGIN_REG, &info.VI_WIDTH_REG, &info.VI_INTR_REG,
        &info.VI_V_CURRENT_LINE_REG, &info.VI_TIMING_REG, &info.VI_V_SYNC_REG, &info.VI_H_SYNC_REG,
        &info.VI_LEAP_REG, &info.VI_H_START_REG, &info.VI_V_START_REG, &info.VI_V_BURST_REG,
        &info.VI_X_SCALE_REG, &info.VI_Y_SCALE_REG};
    for (size_t i = 0; i < sizeof slots / sizeof slots[0]; ++i) *slots[i] = &regs[i];
    st_configure(&info);
    st_rom_open();
    int fed = 0;
    for (int i = 2; i < argc; ++i) {
        std::string dir = argv[i];
        const bool vi_only = dir.rfind("vi:", 0) == 0;
        if (vi_only) dir = dir.substr(3);
        std::vector<uint8_t> ram = read_file(dir + "\\rdram.bin"), sp = read_file(dir + "\\dmem.bin");
        std::vector<uint8_t> json = read_file(dir + "\\frame.json");
        if (ram.size() != (8u << 20) || sp.size() != sizeof dmem || json.empty()) {
            fprintf(stderr, "%s: needs an 8 MiB rdram.bin, a 4 KiB dmem.bin and frame.json\n", dir.c_str());
            return 2;
        }
        memcpy(rdram, ram.data(), ram.size());
        memcpy(dmem, sp.data(), sp.size());
        *info.VI_ORIGIN_REG = json_number(std::string(json.begin(), json.end()), "origin");
        if (vi_only) st_update_screen(nullptr);
        else st_process_dlist();
        ++fed;
    }
    st_close(120000);
    printf("fed %d frames\n", fed);
    return 0;
}
