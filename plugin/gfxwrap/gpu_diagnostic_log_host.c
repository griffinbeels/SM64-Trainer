/* CPU-only production-field formatter witness. No DLL, window or GPU startup. */
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
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
static void diagnostics_watch_stalls(int64_t request_qpc) { printf("watch %lld\n", request_qpc); }
#include "gpu_delivery_log.h"
/* One note of each startup kind, with stamps 10 MHz apart by known amounts. */
static int notes(void) {
    gd_diagnostic d={0};d.bytes=sizeof d;d.version=GD_DIAGNOSTIC_VERSION;
    d.epoch=3;d.qpc_frequency=10000000;
    gd_prepare_diagnostic *p=&d.prepare;
    p->request_qpc=1000000;p->surface_qpc=1160000;
    p->context_begin=1200000;p->context_window=1210000;p->context_temporary=1220000;
    p->context_shared=1230000;p->context_temporary_deleted=2730000;p->context_current=2740000;
    p->snapshots=2750000;p->sampler=2760000;
    p->bridge_begin=2770000;p->bridge_device=3770000;p->bridge_opened=3780000;
    p->bridge_transfer=3790000;p->bridge_registered=3800000;p->end_qpc=3810000;
    d.kind=GD_KIND_REQUEST;runtime_diagnostic(&d);
    d.kind=GD_KIND_PREPARE;runtime_diagnostic(&d);
    d.warm.begin_qpc=500000;d.warm.legacy_qpc=800000;d.warm.end_qpc=810000;d.warm.parts=1;
    d.kind=GD_KIND_WARM;runtime_diagnostic(&d);
    d.anchor_begin_qpc=1170000;d.anchor_end_qpc=1180000;
    d.kind=GD_KIND_ANCHOR;runtime_diagnostic(&d);
    return lines==4?0:3;
}
int main(int argc,char**argv) {
    if(argc==2 && !strcmp(argv[1],"--notes"))return notes();
    gd_diagnostic d={0};d.bytes=sizeof d;d.version=GD_DIAGNOSTIC_VERSION;
    d.healthy=1;d.epoch=17;d.qpc_frequency=1000;d.window_started_qpc=1000;d.observed_qpc=6000;
    d.accounted_occurrence=300;d.published_token=150;d.previous_emit_ticks=1;
    d.snapshot_bytes=12345678;d.bridge_bytes=8765432;
    d.sample_calls=151;d.sample_reuses=9;d.publish_busy=9;
    d.peak_pending=5;d.offer_count=8;d.peak_bridges=2;
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
