/* Run SM64's audio command lists through mupen64plus-rsp-hle's ABI1 code (M4 stems probe).
 *
 * Built by stems_probe.py against a checkout of mupen64plus-rsp-hle's src/ (GPL-2.0-or-later,
 * pinned revision in stems_probe.py); none of its files are copied here. alist_audio.c is
 * included so this file can reach its static command table and run the list one command at a
 * time: the stock alist_process runs the same table in the same order.
 *
 * RDRAM is Project64's word order (little-endian 32-bit words), as mupen64plus keeps it.
 * stm_run executes one command list. With `rollback`, a nonzero byte for command i restores the
 * four mix buffers (dry left/right, wet left/right) after that command: the note that command
 * belongs to still decodes, resamples and saves its state exactly as in the full mix, but adds
 * nothing to the shared output, so the list renders a mix without it. */
#include <stdarg.h>
#include <string.h>
#include "alist_audio.c"

#define EXPORT __declspec(dllexport)

static int g_messages;

void HleVerboseMessage(void* user_defined, const char *message, ...) { (void)user_defined; (void)message; }
void HleInfoMessage(void* user_defined, const char *message, ...) { (void)user_defined; (void)message; }
void HleErrorMessage(void* user_defined, const char *message, ...) { (void)user_defined; (void)message; g_messages++; }
void HleWarnMessage(void* user_defined, const char *message, ...) { (void)user_defined; (void)message; g_messages++; }
void HleCheckInterrupts(void* user_defined) { (void)user_defined; }
void HleProcessDlistList(void* user_defined) { (void)user_defined; }
void HleProcessAlistList(void* user_defined) { (void)user_defined; }
void HleProcessRdpList(void* user_defined) { (void)user_defined; }
void HleShowCFB(void* user_defined) { (void)user_defined; }
int HleForwardTask(void* user_defined) { (void)user_defined; return -1; }
/* hle.c's; the stock entry points (alist_process_audio*) call it, stm_run does not. */
void rsp_break(struct hle_t* hle, unsigned int setbits) { (void)hle; (void)setbits; }

/* synthesis.c: DMEM_ADDR_LEFT_CH 0x4c0 .. DMEM_ADDR_WET_RIGHT_CH 0x880 + DEFAULT_LEN_1CH 0x140 */
enum { MIX_FIRST = DMEM_BASE + 0x4c0, MIX_BYTES = 0x500 };

static struct hle_t g_hle;
static unsigned char g_dmem[0x1000], g_imem[0x1000];

static const acmd_callback_t ABI1[0x10] = {
    SPNOOP,         ADPCM ,         CLEARBUFF,      ENVMIXER,
    LOADBUFF,       RESAMPLE,       SAVEBUFF,       SEGMENT,
    SETBUFF,        SETVOL,         DMEMMOVE,       LOADADPCM,
    MIXER,          INTERLEAVE,     POLEF,          SETLOOP
};

/* The audio DMEM the lists run in (0x1000 bytes, Project64 word order). It persists from one list
 * to the next as the RSP's DMEM does: some commands read bytes an earlier list left there. */
EXPORT unsigned char *stm_dmem(void)
{
    return g_hle.alist_buffer;
}

/* Returns the count of HLE warnings/errors (0 for a clean run), or -1 for a list outside RDRAM. */
EXPORT int stm_run(unsigned char *dram, unsigned int dram_bytes, unsigned int alist, unsigned int bytes,
                   const unsigned char *rollback)
{
    struct hle_t *hle = &g_hle;
    unsigned char saved[MIX_BYTES];
    unsigned int i, count = bytes / 8;
    const uint32_t *list;

    alist &= 0xffffff;
    if (alist + bytes > dram_bytes || (alist & 7) || (bytes & 7))
        return -1;
    hle->dram = dram;
    hle->dmem = g_dmem;
    hle->imem = g_imem;
    g_messages = 0;
    clear_segments(hle);
    list = (const uint32_t *)(dram + alist);
    for (i = 0; i < count; i++) {
        uint32_t w1 = list[2 * i], w2 = list[2 * i + 1];
        unsigned int acmd = (w1 >> 24) & 0x7f;
        if (rollback && rollback[i])
            memcpy(saved, hle->alist_buffer + MIX_FIRST, MIX_BYTES);
        if (acmd < 0x10)
            ABI1[acmd](hle, w1, w2);
        else
            g_messages++;
        if (rollback && rollback[i])
            memcpy(hle->alist_buffer + MIX_FIRST, saved, MIX_BYTES);
    }
    return g_messages;
}
