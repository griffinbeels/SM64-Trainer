/* The Zilmar graphics plugin spec, version 1.3 -- the twenty-odd functions
 * Project64 1.6 looks up in a graphics DLL, and the two structs it passes.
 * Copied from the spec header GLideN64 ships (src/windows/Project64-plugin-spec/
 * 1_3/Video.h); PJ64 1.6's own loader (Plugin.cpp, LoadGFXDll) refuses a DLL
 * missing any of the REQUIRED set: GetDllInfo, CloseDLL, ChangeWindow,
 * DrawScreen, InitiateGFX, MoveScreen, ProcessDList, RomClosed, RomOpen,
 * UpdateScreen, ViStatusChanged, ViWidthChanged, and for version 0x0103 also
 * ProcessRDPList, CaptureScreen, ShowCFB. Everything else is optional. */
#pragma once
#include <windows.h>

#define PLUGIN_TYPE_GFX 2

typedef struct {
    WORD Version;          /* 0x0103 */
    WORD Type;             /* PLUGIN_TYPE_GFX */
    char Name[100];
    BOOL NormalMemory;
    BOOL MemoryBswaped;    /* PJ64 1.6 refuses FALSE */
} PLUGIN_INFO;

typedef struct {
    HWND hWnd;
    HWND hStatusBar;
    BOOL MemoryBswaped;
    unsigned char *HEADER;
    unsigned char *RDRAM;
    unsigned char *DMEM;
    unsigned char *IMEM;
    unsigned int *MI_INTR_REG;
    unsigned int *DPC_START_REG;
    unsigned int *DPC_END_REG;
    unsigned int *DPC_CURRENT_REG;
    unsigned int *DPC_STATUS_REG;
    unsigned int *DPC_CLOCK_REG;
    unsigned int *DPC_BUFBUSY_REG;
    unsigned int *DPC_PIPEBUSY_REG;
    unsigned int *DPC_TMEM_REG;
    unsigned int *VI_STATUS_REG;
    unsigned int *VI_ORIGIN_REG;
    unsigned int *VI_WIDTH_REG;
    unsigned int *VI_INTR_REG;
    unsigned int *VI_V_CURRENT_LINE_REG;
    unsigned int *VI_TIMING_REG;
    unsigned int *VI_V_SYNC_REG;
    unsigned int *VI_H_SYNC_REG;
    unsigned int *VI_LEAP_REG;
    unsigned int *VI_H_START_REG;
    unsigned int *VI_V_START_REG;
    unsigned int *VI_V_BURST_REG;
    unsigned int *VI_X_SCALE_REG;
    unsigned int *VI_Y_SCALE_REG;
    void (*CheckInterrupts)(void);
} GFX_INFO;

typedef struct {
    unsigned int addr;
    unsigned int size;
    unsigned int width;
    unsigned int height;
} FrameBufferInfo;

/* The whole exported surface as function-pointer types, so the wrapper and
 * the fake plugin resolve the same names the same way. */
typedef void (__cdecl *fn_void)(void);
typedef void (__cdecl *fn_hwnd)(HWND);
typedef void (__cdecl *fn_info)(PLUGIN_INFO *);
typedef BOOL (__cdecl *fn_initiate)(GFX_INFO);
typedef void (__cdecl *fn_move)(int, int);
typedef void (__cdecl *fn_capture)(char *);
typedef void (__cdecl *fn_fbread)(unsigned int);
typedef void (__cdecl *fn_fbwrite)(unsigned int, unsigned int);
typedef void (__cdecl *fn_fbinfo)(void *);
typedef void (__cdecl *fn_readscreen)(void **, long *, long *);

typedef struct {
    fn_info GetDllInfo;
    fn_void CloseDLL, ChangeWindow, DrawScreen, ProcessDList, ProcessRDPList;
    fn_void RomClosed, RomOpen, ShowCFB, UpdateScreen, ViStatusChanged, ViWidthChanged;
    fn_hwnd DllAbout, DllConfig, DllTest;
    fn_initiate InitiateGFX;
    fn_move MoveScreen;
    fn_capture CaptureScreen;
    fn_fbread FBRead;
    fn_fbwrite FBWrite;
    fn_fbinfo FBGetFrameBufferInfo;
    fn_readscreen ReadScreen;
} gfx_api_t;

#define RESOLVE_GFX_API(api, module) do { \
    (api).GetDllInfo = (fn_info)GetProcAddress((module), "GetDllInfo"); \
    (api).CloseDLL = (fn_void)GetProcAddress((module), "CloseDLL"); \
    (api).ChangeWindow = (fn_void)GetProcAddress((module), "ChangeWindow"); \
    (api).DrawScreen = (fn_void)GetProcAddress((module), "DrawScreen"); \
    (api).ProcessDList = (fn_void)GetProcAddress((module), "ProcessDList"); \
    (api).ProcessRDPList = (fn_void)GetProcAddress((module), "ProcessRDPList"); \
    (api).RomClosed = (fn_void)GetProcAddress((module), "RomClosed"); \
    (api).RomOpen = (fn_void)GetProcAddress((module), "RomOpen"); \
    (api).ShowCFB = (fn_void)GetProcAddress((module), "ShowCFB"); \
    (api).UpdateScreen = (fn_void)GetProcAddress((module), "UpdateScreen"); \
    (api).ViStatusChanged = (fn_void)GetProcAddress((module), "ViStatusChanged"); \
    (api).ViWidthChanged = (fn_void)GetProcAddress((module), "ViWidthChanged"); \
    (api).DllAbout = (fn_hwnd)GetProcAddress((module), "DllAbout"); \
    (api).DllConfig = (fn_hwnd)GetProcAddress((module), "DllConfig"); \
    (api).DllTest = (fn_hwnd)GetProcAddress((module), "DllTest"); \
    (api).InitiateGFX = (fn_initiate)GetProcAddress((module), "InitiateGFX"); \
    (api).MoveScreen = (fn_move)GetProcAddress((module), "MoveScreen"); \
    (api).CaptureScreen = (fn_capture)GetProcAddress((module), "CaptureScreen"); \
    (api).FBRead = (fn_fbread)GetProcAddress((module), "FBRead"); \
    (api).FBWrite = (fn_fbwrite)GetProcAddress((module), "FBWrite"); \
    (api).FBGetFrameBufferInfo = (fn_fbinfo)GetProcAddress((module), "FBGetFrameBufferInfo"); \
    (api).ReadScreen = (fn_readscreen)GetProcAddress((module), "ReadScreen"); \
} while (0)
