/* CPU-only production-field formatter witness. No DLL, window or GPU startup. */
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include "stamp_adapter.h"
#include "gpu_delivery_diagnostics.h"
static volatile LONG g_log_dropped;
static unsigned lines;
int __cdecl sa_read_refusal(sa_refusal *out) { (void)out; return 0; }
static void plugin_logf(const char *event,const char *format,...) {
    char details[720];va_list args;va_start(args,format);
    const int size=vsnprintf(details,sizeof details,format,args);va_end(args);
    if(size<0 || size>=sizeof details)ExitProcess(2);
    printf("2026-09-14T23:00:00.000Z pid=42 tid=7 build=cpu-formatter event=%s %s\n",event,details);
    ++lines;
}
#include "gpu_delivery_log.h"
int main(void) {
    gd_diagnostic d={0};d.bytes=sizeof d;d.version=GD_DIAGNOSTIC_VERSION;
    d.healthy=1;d.epoch=17;d.qpc_frequency=1000;d.window_started_qpc=1000;d.observed_qpc=6000;
    d.accounted_occurrence=300;d.published_token=150;d.previous_emit_ticks=1;
    d.snapshot_bytes=12345678;d.bridge_bytes=8765432;
    d.sample_calls=151;d.sample_reuses=9;d.publish_busy=9;
    for(unsigned n=0;n<GD_PHASE_COUNT;++n){
        d.phases[n].calls=10;d.phases[n].total_ticks=30;
        d.phases[n].max_ticks=3;d.phases[n].max_started_qpc=4000;
    }
    d.cadence.records=3;d.cadence.pictures=1;d.cadence.same_origin=2;
    d.cadence.intervals.calls=2;d.cadence.intervals.total_ticks=66;
    d.cadence.min_ticks=16;d.cadence.intervals.max_ticks=50;d.cadence.intervals.max_started_qpc=1016;
    d.cadence.buckets[1]=d.cadence.buckets[3]=1;
    runtime_diagnostic(&d);
    return lines==10?0:3;
}
