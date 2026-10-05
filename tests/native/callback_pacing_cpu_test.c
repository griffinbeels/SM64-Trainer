/* Calibrates the shipped callback producer without GL, emulator, capture,
 * retained pictures or a polling consumer. The real writer is deliberately
 * blocked to exercise bounded snapshots and loss accounting. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static HANDLE write_entered, release_write;
static volatile LONG final_marker_only;
static int fail_cycles;
static uint64_t cycle_reads, time_reads;
static BOOL WINAPI counted_cycles(HANDLE thread, PULONG64 cycles) {
    ++cycle_reads;
    if (fail_cycles) { SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    return QueryThreadCycleTime(thread,cycles);
}
static BOOL WINAPI counted_times(HANDLE thread, LPFILETIME created, LPFILETIME exited,
                                 LPFILETIME kernel, LPFILETIME user) {
    ++time_reads;
    return GetThreadTimes(thread,created,exited,kernel,user);
}
static BOOL WINAPI blocked_write(HANDLE file,LPCVOID bytes,DWORD size,LPDWORD written,LPOVERLAPPED overlap) {
    if (!InterlockedCompareExchange(&final_marker_only,0,0)
            || strstr((const char *)bytes,"final_snapshot_marker")) {
        SetEvent(write_entered);
        WaitForSingleObject(release_write,INFINITE);
    }
    return WriteFile(file,bytes,size,written,overlap);
}
static void self_dir(wchar_t *out,size_t count) {
    GetCurrentDirectoryW((DWORD)count,out);wcsncat_s(out,count,L"\\",_TRUNCATE);
}
#define WriteFile blocked_write
#define QueryThreadCycleTime counted_cycles
#define GetThreadTimes counted_times
#include "../../plugin/gfxwrap/diagnostics.h"
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
static cp_cpu_sample cpu(uint32_t tid, uint64_t cycles, uint64_t user) {
    cp_cpu_sample sample={0};sample.tid=tid;sample.cycles=cycles;sample.user_100ns=user;sample.valid=1;
    return sample;
}
static int cpu_pairs(void) {
    cp_meter meter={0};cp_cpu_sample a=cpu(11,100,0),b=cpu(11,200,10);
    cp_observe_cpu(&meter,1000,1010,1000,&a,&b);
    a=cpu(11,500,50);b=cpu(11,700,70);cp_observe_cpu(&meter,1110,1130,1000,&a,&b);
    a=cpu(11,750,70);b=cpu(11,775,70);cp_observe_cpu(&meter,1150,1155,1000,&a,&b);
    CHECK(meter.gap.max_cpu.begin_qpc==1000 && meter.gap.max_cpu.end_qpc==1110
        && meter.gap.max_cpu.cycles==400 && meter.gap.max_cpu.cpu_100ns==50);
    CHECK(meter.gap_outside_cpu.begin_qpc==1010 && meter.gap_outside_cpu.end_qpc==1110
        && meter.gap_outside_cpu.cycles==300 && meter.gap_outside_cpu.cpu_100ns==40);
    CHECK(meter.duration.max_cpu.begin_qpc==1110 && meter.duration.max_cpu.end_qpc==1130
        && meter.duration.max_cpu.cycles==200);
    cp_reset(&meter);a=cpu(11,800,70);b=cpu(11,810,70);cp_observe_cpu(&meter,9000,9001,1000,&a,&b);
    CHECK(meter.gap.count==2 && meter.generation==2 && meter.gap.max_cpu.generation==1);
    a=cpu(11,900,80);a.valid=0;a.reason=CP_CPU_QUERY_FAILED;a.error=5;
    b=cpu(11,910,80);cp_observe_cpu(&meter,9005,9006,1000,&a,&b);
    a=cpu(11,950,80);b=cpu(11,960,80);cp_observe_cpu(&meter,10000,10001,1000,&a,&b);
    CHECK(!meter.gap.max_cpu.valid && meter.gap.max_cpu.reason==CP_CPU_QUERY_FAILED
        && meter.gap.max_cpu.error==5 && meter.gap.max_cpu.begin_qpc==9005);
    a=cpu(22,100,0);b=cpu(22,110,0);cp_observe_cpu(&meter,12000,12001,1000,&a,&b);
    CHECK(!meter.gap.max_cpu.valid && meter.gap.max_cpu.reason==CP_CPU_THREAD_CHANGED
        && meter.cpu_thread_changes==1 && meter.duration.count==7);
    a=cpu(22,10,0);b=cpu(22,9,0);cp_observe_cpu(&meter,12010,12011,1000,&a,&b);
    CHECK(meter.cpu_invalid==2 && !meter.previous_cpu_begin.valid
        && meter.previous_cpu_begin.reason==CP_CPU_COUNTER_REVERSED);
    return 0;
}
static int runtime_cpu_boundaries(void) {
    char line[4096];memset(line,'x',2000);size_t used=2000;
    cp_cpu_pair largest;memset(&largest,0xff,sizeof largest);
    callback_cpu_format(line,sizeof line,&used,"duration",&largest);
    callback_cpu_format(line,sizeof line,&used,"gap",&largest);
    callback_cpu_format(line,sizeof line,&used,"outside",&largest);
    CHECK(used+1<sizeof line); /* all integer widths, plus the existing base record */
    callback_diagnostic_t note={"InvalidCPU"};
    SetLastError(777);fail_cycles=1;
    const int64_t begin=callback_diagnostic_begin(&note);
    CHECK(GetLastError()==777);fail_cycles=0;
    LARGE_INTEGER end;QueryPerformanceCounter(&end);callback_diagnostic_end(&note,begin,end.QuadPart);
    CHECK(!note.pacing.duration.max_cpu.valid && note.pacing.duration.max_cpu.error==ERROR_ACCESS_DENIED);
    const int64_t later=callback_diagnostic_begin(&note);QueryPerformanceCounter(&end);
    callback_diagnostic_end(&note,later,end.QuadPart);
    CHECK(!note.pacing.gap.max_cpu.valid && note.pacing.gap.max_cpu.reason==CP_CPU_QUERY_FAILED);
    callback_diagnostic_t overlap={"Overlap"};
    const int64_t outer=callback_diagnostic_begin(&overlap),inner=callback_diagnostic_begin(&overlap);
    QueryPerformanceCounter(&end);callback_diagnostic_end(&overlap,inner,end.QuadPart);
    QueryPerformanceCounter(&end);callback_diagnostic_end(&overlap,outer,end.QuadPart);
    CHECK(overlap.cpu_owner==0 && !overlap.pacing.duration.max_cpu.valid
        && overlap.pacing.duration.max_cpu.reason==CP_CPU_OVERLAP);
    callback_diagnostic_t reset={"ResetCPU"};
    const int64_t old=callback_diagnostic_begin(&reset);callback_diagnostic_reset(&reset);
    QueryPerformanceCounter(&end);callback_diagnostic_end(&reset,old,end.QuadPart);
    CHECK(!reset.pacing.duration.max_cpu.valid && reset.cpu_owner==0);
    callback_diagnostic_t contended={"ContendedCPU"};
    const int64_t waiting=callback_diagnostic_begin(&contended);
    AcquireSRWLockExclusive(&contended.pacing_commit);
    QueryPerformanceCounter(&end);callback_diagnostic_end(&contended,waiting,end.QuadPart);
    callback_diagnostic_reset(&contended);
    CHECK(contended.pacing_busy==1 && contended.pending_resets==1 && contended.cpu_owner==0);
    ReleaseSRWLockExclusive(&contended.pacing_commit);
    const int64_t resumed=callback_diagnostic_begin(&contended);QueryPerformanceCounter(&end);
    callback_diagnostic_end(&contended,resumed,end.QuadPart);
    CHECK(contended.pacing.samples==2 && contended.pacing.invalid==1
        && contended.pacing.cpu_commit_busy==1 && contended.pacing.gap.count==0);
    return 0;
}
static void cpu_action(unsigned kind) {
    if (kind==1) { Sleep(100);return; }
    LARGE_INTEGER at;QueryPerformanceCounter(&at);
    const int64_t until=at.QuadPart+g_diagnostic_frequency*(kind==2?50:100)/1000;
    do { QueryPerformanceCounter(&at); } while (at.QuadPart<until);
    if (kind==2) Sleep(50);
}
static int send_cpu(callback_diagnostic_t *note) {
    /* Try-only producer submission may legitimately meet its writer draining.
     * This test retries a retained snapshot; production never waits here. */
    for (unsigned n=0;n<1000;++n) {
        if (callback_pacing_send(note->name,&note->pacing,note->pacing.last_qpc)) return 1;
        Sleep(1);
    }
    return 0;
}
static int calibrate_cpu(void) {
    static const char *gap_names[]={"CPUBusyGap","CPUSleepGap","CPUMixedGap"};
    static const char *duration_names[]={"CPUBusyDuration","CPUSleepDuration","CPUMixedDuration"};
    static const char *controls[]={"busy","sleep","mixed"};
    uint64_t gap_cycles[3],duration_cycles[3];
    for (unsigned kind=0;kind<3;++kind) {
        callback_diagnostic_t gap={0},duration={0};
        gap.name=gap_names[kind];duration.name=duration_names[kind];
        LARGE_INTEGER end;
        const int64_t first=callback_diagnostic_begin(&gap);QueryPerformanceCounter(&end);
        const int64_t first_end=end.QuadPart;callback_diagnostic_end(&gap,first,first_end);
        cpu_action(kind);
        const int64_t next=callback_diagnostic_begin(&gap);QueryPerformanceCounter(&end);
        callback_diagnostic_end(&gap,next,end.QuadPart);
        const int64_t began=callback_diagnostic_begin(&duration);cpu_action(kind);
        QueryPerformanceCounter(&end);callback_diagnostic_end(&duration,began,end.QuadPart);
        const cp_cpu_pair *g=&gap.pacing.gap.max_cpu,*o=&gap.pacing.gap_outside_cpu;
        const cp_cpu_pair *d=&duration.pacing.duration.max_cpu;
        CHECK(g->valid && o->valid && d->valid && g->tid==GetCurrentThreadId());
        CHECK(g->begin_qpc==(uint64_t)first && g->end_qpc==(uint64_t)next
            && o->begin_qpc==(uint64_t)first_end && o->end_qpc==(uint64_t)next);
        CHECK(d->begin_qpc==(uint64_t)began && d->end_qpc==(uint64_t)end.QuadPart);
        gap_cycles[kind]=o->cycles;duration_cycles[kind]=d->cycles;
        printf("{\"case\":\"cpu_calibration\",\"control\":\"%s\",\"gap_begin_qpc\":%llu,"
            "\"gap_end_qpc\":%llu,\"outside_cycles\":%llu,\"duration_cycles\":%llu,"
            "\"gap_cpu_100ns\":%llu,\"duration_cpu_100ns\":%llu}\n",controls[kind],
            (unsigned long long)first,(unsigned long long)next,gap_cycles[kind],duration_cycles[kind],
            g->cpu_100ns,d->cpu_100ns);
        CHECK(send_cpu(&gap));
        CHECK(send_cpu(&duration));
    }
    CHECK(gap_cycles[0]>gap_cycles[1]*100 && duration_cycles[0]>duration_cycles[1]*100);
    CHECK(gap_cycles[2]>gap_cycles[1]*100 && duration_cycles[2]>duration_cycles[1]*100);
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
    CHECK(distributions()==0 && cpu_pairs()==0);
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
    {
        const int64_t pending=callback_diagnostic_begin(&g_diagnostic_list);
        LARGE_INTEGER end;QueryPerformanceCounter(&end);
        callback_diagnostic_end(&g_diagnostic_list,pending,end.QuadPart);
        callback_pacing_flush_all(); /* full queue: final window explicitly stays unsent */
        CHECK(g_diagnostic_list.pacing.duration.count==1 && g_diagnostic_list.pacing.sent_samples==0);
    }
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
    CHECK(runtime_cpu_boundaries()==0);
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
    const uint64_t before_cycles=cycle_reads,before_times=time_reads;
    LARGE_INTEGER measured_begin,measured_end;QueryPerformanceCounter(&measured_begin);
    for(unsigned n=0;n<200000;++n) {
        const int64_t started=callback_diagnostic_begin(&overhead);
        LARGE_INTEGER finished;QueryPerformanceCounter(&finished);
        callback_diagnostic_end(&overhead,started,finished.QuadPart);
    }
    QueryPerformanceCounter(&measured_end);CHECK(overhead.pacing.samples==200000
        && overhead.pacing.cpu_samples==200000 && cycle_reads-before_cycles==400000
        && time_reads-before_times==400000);
    printf("{\"case\":\"observer_cost\",\"callbacks\":200000,\"cycle_reads\":400000,"
        "\"time_reads\":400000,\"mean_ns\":%.1f}\n",
        (double)(measured_end.QuadPart-measured_begin.QuadPart)*1000000000.0/(double)f/200000.0);
    CHECK(calibrate_cpu()==0);
    {
        LARGE_INTEGER began,ended;QueryPerformanceCounter(&began);
        AcquireSRWLockExclusive(&g_diagnostic_list.pacing_commit);
        callback_pacing_flush_all(); /* self-held guard must not block shutdown */
        ReleaseSRWLockExclusive(&g_diagnostic_list.pacing_commit);
        QueryPerformanceCounter(&ended);
        CHECK(ended.QuadPart-began.QuadPart<g_diagnostic_frequency/20);
    }
    /* Stale/in-flight reporting observes producer starts without waiting for
     * a completion; it must not claim a displayed-frame measurement. */
    const int64_t begin=callback_diagnostic_begin(&g_diagnostic_update);Sleep(550);
    CHECK(InterlockedCompareExchange64(&g_diagnostic_update.in_flight_qpc,0,0)==begin);
    LARGE_INTEGER end;QueryPerformanceCounter(&end);
    callback_diagnostic_end(&g_diagnostic_update,begin,end.QuadPart);
    for(unsigned n=0;n<2000 && g_pacing_count;++n) Sleep(1);
    CHECK(g_pacing_count==0);
    InterlockedExchange(&final_marker_only,1);
    ResetEvent(write_entered);ResetEvent(release_write);
    plugin_logf("calibration","final_snapshot_marker after the writer passed its pacing drain");
    CHECK(WaitForSingleObject(write_entered,2000)==WAIT_OBJECT_0);
    HANDLE writer;CHECK(DuplicateHandle(GetCurrentProcess(),g_log->thread,GetCurrentProcess(),&writer,SYNCHRONIZE,FALSE,0));
    diagnostics_stop();CHECK(WaitForSingleObject(writer,0)==WAIT_TIMEOUT);
    SetEvent(release_write);CHECK(WaitForSingleObject(writer,5000)==WAIT_OBJECT_0);CloseHandle(writer);
    CloseHandle(write_entered);CloseHandle(release_write);
    return 0;
#endif
}
