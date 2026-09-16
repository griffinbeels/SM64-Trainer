/* C wrapper composition; original plugin exports remain the owner of gameplay. */
#pragma once
#include "zilmar.h"
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef uint32_t (__cdecl *wr_read_vi)(void*);
typedef int (__cdecl *wr_read_ram)(void*,uint32_t,uint32_t,uint8_t*);
int __cdecl wr_configure(HMODULE,const gfx_api_t*,wr_read_vi,wr_read_ram);
void __cdecl wr_suspend(void);
#ifdef __cplusplus
}
#endif
