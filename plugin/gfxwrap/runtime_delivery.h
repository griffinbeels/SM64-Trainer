#pragma once
#include "gpu_delivery.h"
#ifdef __cplusplus
extern "C" {
#endif
// Call after successful gd_configure and sa_configure, before control_start.
int __cdecl rc_bind_delivery(const gd_limits*);
#ifdef __cplusplus
}
#endif
