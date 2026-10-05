/* Calibrates the shipped callback producer without GL, emulator, capture,
 * retained pictures or a polling consumer. The real writer is deliberately
 * blocked to exercise bounded snapshots and loss accounting. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static HANDLE write_entered, release_write;
static BOOL WINAPI blocked_write(HANDLE file,LPCVOID bytes,DWORD size,LPDWORD written,LPOVERLAPPED overlap) {
    SetEvent(write_entered);
    WaitForSingleObject(release_write,INFINITE);
    return WriteFile(file,bytes,size,written,overlap);
}
static void self_dir(wchar_t *out,size_t count) {
    GetCurrentDirectoryW((DWORD)count,out);wcsncat_s(out,count,L"\\",_TRUNCATE);
}
#define WriteFile blocked_write
#include "diagnostics.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"callback pacing calibration line %d\n",__LINE__); return 1; } } while (0)

static int distributions(void) {
    cp_meter meter={0}; const uint64_t f=1000000;
    const unsigned delays[]={10,40,100,250};
    /* Both normal production rates are valid callback cadences, without
     * assigning a guessed monitor refresh rate or alarm label to either. */
    for(unsigned hz=30;hz<=60;hz+=30) {
        cp_meter normal={0};uint64_t at=f;
        for(unsigned n=0;n<1000;++n) {at+=f/hz;cp_observe(&normal,at,at+1000,f);}
        CHECK(normal.duration.count==1000 && normal.gap.count==999);
        CHECK(normal.gap.bins[hz==30?3:1]==999);
        at+=f/hz+40000;cp_observe(&normal,at,at+1000,f);
        CHECK(normal.gap.count==1000 && normal.gap.max_ticks==f/hz+40000);
    }
    uint64_t at=100*f;
    for(unsigned n=0;n<4;++n) {
        cp_observe(&meter,at,at+delays[n]*1000,f);at+=f;
    }
    CHECK(meter.samples==4 && meter.duration.count==4 && meter.gap.count==3);
    CHECK(meter.duration.bins[1]==1 && meter.duration.bins[5]==1
        && meter.duration.bins[8]==1 && meter.duration.bins[10]==1);
    CHECK(meter.duration.max_ticks==250000 && meter.duration.max_started_qpc==103*f);
    cp_window_sent(&meter); at+=f;
    /* 8192 callbacks, no consumer/poll between them, including a 40 ms gap. */
    for(unsigned n=0;n<8192;++n) {
        at+=n==4096?40000:16667;cp_observe(&meter,at,at+1000,f);
    }
    CHECK(meter.samples==8196 && meter.duration.count==8192 && meter.gap.count==8192);
    CHECK(meter.gap.bins[5]==1 && meter.gap.bins[11]==1);
    CHECK(meter.gap.bins[2]==8190);
    const uint64_t gap_count=meter.gap.count;
    cp_reset(&meter); at+=100*f;cp_observe(&meter,at,at+1000,f);
    CHECK(meter.resets==1 && meter.gap.count==gap_count && meter.duration.count==8193);
    cp_observe(&meter,at-1,at,f);CHECK(meter.invalid==1 && meter.samples==8198);
    printf("{\"case\":\"synthetic\",\"samples\":%llu,\"reset_boundaries\":1,\"invalid\":1,\"unpolled_callbacks\":8192}\n",meter.samples);
    return 0;
}
int main(void) {
#ifdef CALLBACK_PACING_BASELINE_HOST
    diagnostics_start();CHECK(g_log && g_diagnostic_frequency>0);
    callback_diagnostic_t baseline={"Baseline"};
    LARGE_INTEGER began,ended;QueryPerformanceCounter(&began);
    for(unsigned n=0;n<200000;++n) {
        const int64_t at=callback_diagnostic_begin(&baseline);
        LARGE_INTEGER finished;QueryPerformanceCounter(&finished);
        callback_diagnostic_end(&baseline,at,finished.QuadPart);
    }
    QueryPerformanceCounter(&ended);
    printf("{\"case\":\"baseline_cost\",\"callbacks\":200000,\"mean_ns\":%.1f}\n",
        (double)(ended.QuadPart-began.QuadPart)*1000000000.0/(double)g_diagnostic_frequency/200000.0);
    CHECK(baseline.pacing.samples==0 && baseline.calls==200000);
    diagnostics_stop();return 0;
#else
    CHECK(distributions()==0);
    write_entered=CreateEventW(NULL,TRUE,FALSE,NULL);release_write=CreateEventW(NULL,TRUE,FALSE,NULL);
    diagnostics_start();CHECK(g_log && g_diagnostic_frequency>0);
    plugin_logf("calibration","block writer");CHECK(WaitForSingleObject(write_entered,2000)==WAIT_OBJECT_0);
    /* Fill the structured queue while the real writer cannot drain. */
    callback_diagnostic_t note={"Calibration"};
    const uint64_t f=(uint64_t)g_diagnostic_frequency;
    for(unsigned n=0;n<CP_SNAPSHOTS;++n) {
        cp_observe(&note.pacing,(n+1)*f,(n+1)*f+f/1000,f);
        CHECK(callback_pacing_send(note.name,&note.pacing,(n+1)*f+f/1000));
    }
    CHECK(g_pacing_count==CP_SNAPSHOTS);
    for(unsigned n=0;n<32;++n) {
        cp_observe(&note.pacing,(n+CP_SNAPSHOTS+1)*f,(n+CP_SNAPSHOTS+1)*f+f/100,f);
        CHECK(!callback_pacing_send(note.name,&note.pacing,n*f));
    }
    CHECK(note.pacing.samples==CP_SNAPSHOTS+32 && note.pacing.duration.count==32
        && note.pacing.gap.count==32 && g_pacing_busy>=32);
    SetEvent(release_write);
    for(unsigned n=0;n<2000 && g_pacing_count;++n) Sleep(1);
    CHECK(g_pacing_count==0);
    CHECK(callback_pacing_send(note.name,&note.pacing,49*f));
    CHECK(note.pacing.duration.count==0 && note.pacing.samples==48);
    /* Real CPU delays go through the wrapper's exact begin/end functions. */
    const unsigned delays[]={10,40,100,250};
    callback_diagnostic_t live={"InjectedSleep"};
    for(unsigned n=0;n<4;++n) {
        const int64_t begin=callback_diagnostic_begin(&live);Sleep(delays[n]);
        LARGE_INTEGER end;QueryPerformanceCounter(&end);
        callback_diagnostic_end(&live,begin,end.QuadPart);
        CHECK(live.pacing.duration.max_ticks >= f*delays[n]/1000);
    }
    CHECK(live.pacing.samples==4 && live.pacing.duration.count==4 && live.pacing.gap.count==3);
    CHECK(callback_pacing_send(live.name,&live.pacing,live.pacing.last_qpc));
    printf("{\"case\":\"overflow\",\"samples\":48,\"snapshot_busy\":%ld,\"discarded_samples\":0}\n",(long)g_pacing_busy);
    callback_diagnostic_t overhead={"Overhead"};
    LARGE_INTEGER measured_begin,measured_end;QueryPerformanceCounter(&measured_begin);
    for(unsigned n=0;n<200000;++n) {
        const int64_t started=callback_diagnostic_begin(&overhead);
        LARGE_INTEGER finished;QueryPerformanceCounter(&finished);
        callback_diagnostic_end(&overhead,started,finished.QuadPart);
    }
    QueryPerformanceCounter(&measured_end);CHECK(overhead.pacing.samples==200000);
    printf("{\"case\":\"observer_cost\",\"callbacks\":200000,\"mean_ns\":%.1f}\n",
        (double)(measured_end.QuadPart-measured_begin.QuadPart)*1000000000.0/(double)f/200000.0);
    /* Stale/in-flight reporting observes producer starts without waiting for
     * a completion; it must not claim a displayed-frame measurement. */
    const int64_t begin=callback_diagnostic_begin(&g_diagnostic_update);Sleep(550);
    CHECK(InterlockedCompareExchange64(&g_diagnostic_update.in_flight_qpc,0,0)==begin);
    LARGE_INTEGER end;QueryPerformanceCounter(&end);
    callback_diagnostic_end(&g_diagnostic_update,begin,end.QuadPart);
    HANDLE writer;CHECK(DuplicateHandle(GetCurrentProcess(),g_log->thread,GetCurrentProcess(),&writer,SYNCHRONIZE,FALSE,0));
    diagnostics_stop();CHECK(WaitForSingleObject(writer,5000)==WAIT_OBJECT_0);CloseHandle(writer);
    CloseHandle(write_entered);CloseHandle(release_write);
    return 0;
#endif
}
