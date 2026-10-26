/* Run one RSP task through cxd4's interpreter (M4 stems probe): the ROM's own audio microcode.
 *
 * Built by stems_probe.py against a checkout of mupen64plus-rsp-cxd4 (CC0-1.0, pinned revision in
 * stems_probe.py): its interpreter core (su.c, vu/) only. The plugin module (module.c) is not
 * built: it reads a config file and reports problems through a console window that waits for a
 * key. This file stands in for it with no window and no file.
 *
 * Memory is Project64's word order, as Project64 hands it to its RSP plugin. The caller loads
 * the boot microcode into IMEM and the OSTask into DMEM 0xFC0 (what osSpTaskLoad does) and keeps
 * DMEM and IMEM from one task to the next, as the RSP does. stm_lle_run starts the RSP at IMEM 0
 * and returns when the task executes BREAK. */
#include <setjmp.h>
#include <string.h>
#include "module.h"
#include "su.h"

#define EXPORT __declspec(dllexport)

RSP_INFO RSP_INFO_NAME;
p_func GBI_phase;
short MFC0_count[32];

static int g_messages;
static unsigned int g_mi_intr, g_sp[10], g_dpc[8];
static jmp_buf g_escape;
static unsigned long long g_steps, g_budget;
static u32 g_recent[64];

NOINLINE void message(const char* body)
{
    (void)body;
    g_messages++;
}

/* Built with SP_EXECUTE_LOG: run_task calls this before every instruction. It bounds the task (a
 * microcode that never reaches BREAK would otherwise spin forever) and keeps the last words run. */
void step_SP_commands(u32 inst)
{
    g_recent[g_steps & 63] = inst;
    if (++g_steps > g_budget)
        longjmp(g_escape, 1);
}

EXPORT unsigned long long stm_lle_steps(u32 *recent)
{
    memcpy(recent, g_recent, sizeof(g_recent));
    return g_steps;
}

static void no_interrupts(void) {}
static void no_list(void) {}

EXPORT int stm_lle_run(unsigned char *dram, unsigned char *dmem, unsigned char *imem)
{
    RSP_INFO *info = &RSP_INFO_NAME;
    int i;

    memset(info, 0, sizeof(*info));
    info->MemorySwapped = 1;
    info->RDRAM = dram;
    info->DMEM = dmem;
    info->IMEM = imem;
    info->MI_INTR_REG = &g_mi_intr;
    info->SP_MEM_ADDR_REG = &g_sp[0];
    info->SP_DRAM_ADDR_REG = &g_sp[1];
    info->SP_RD_LEN_REG = &g_sp[2];
    info->SP_WR_LEN_REG = &g_sp[3];
    info->SP_STATUS_REG = &g_sp[4];
    info->SP_DMA_FULL_REG = &g_sp[5];
    info->SP_DMA_BUSY_REG = &g_sp[6];
    info->SP_PC_REG = &g_sp[7];
    info->SP_SEMAPHORE_REG = &g_sp[8];
    info->DPC_START_REG = &g_dpc[0];
    info->DPC_END_REG = &g_dpc[1];
    info->DPC_CURRENT_REG = &g_dpc[2];
    info->DPC_STATUS_REG = &g_dpc[3];
    info->DPC_CLOCK_REG = &g_dpc[4];
    info->DPC_BUFBUSY_REG = &g_dpc[5];
    info->DPC_PIPEBUSY_REG = &g_dpc[6];
    info->DPC_TMEM_REG = &g_dpc[7];
    info->CheckInterrupts = no_interrupts;
    info->ProcessDList = no_list;
    info->ProcessAList = no_list;
    info->ProcessRdpList = no_list;
    info->ShowCFB = no_list;

    DRAM = dram;
    DMEM = dmem;
    IMEM = imem;
    su_max_address = 0x7FFFFF;
    CR[0x0] = info->SP_MEM_ADDR_REG;
    CR[0x1] = info->SP_DRAM_ADDR_REG;
    CR[0x2] = info->SP_RD_LEN_REG;
    CR[0x3] = info->SP_WR_LEN_REG;
    CR[0x4] = info->SP_STATUS_REG;
    CR[0x5] = info->SP_DMA_FULL_REG;
    CR[0x6] = info->SP_DMA_BUSY_REG;
    CR[0x7] = info->SP_SEMAPHORE_REG;
    CR[0x8] = info->DPC_START_REG;
    CR[0x9] = info->DPC_END_REG;
    CR[0xA] = info->DPC_CURRENT_REG;
    CR[0xB] = info->DPC_STATUS_REG;
    CR[0xC] = info->DPC_CLOCK_REG;
    CR[0xD] = info->DPC_BUFBUSY_REG;
    CR[0xE] = info->DPC_PIPEBUSY_REG;
    CR[0xF] = info->DPC_TMEM_REG;
    GBI_phase = no_list;
    for (i = 0; i < 32; i++)
        conf[i] = 0;                  /* LLE for every task, no queue/trace extras */
    MF_SP_STATUS_TIMEOUT = 32767;
    memset(g_sp, 0, sizeof(g_sp));
    memset(g_dpc, 0, sizeof(g_dpc));
    g_mi_intr = 0;
    g_messages = 0;
    g_sp[7] = 0x04001000 & 0xFFF;     /* SP_PC: IMEM start, where osSpTaskLoad put the boot code */
    for (i = 0; i < 32; i++)
        MFC0_count[i] = 0;
    g_steps = 0;
    g_budget = 50000000;              /* an audio task runs well under a million instructions */
    if (setjmp(g_escape))
        return -2;
    run_task();
    if (!(g_sp[4] & 0x2))             /* SP_STATUS_BROKE: the task ended with BREAK */
        return -1;
    return g_messages;
}
