/* Real source clock, origin, worker Session and FOLLOW selection. Only model
 * geometry and the capture API's input queue are substituted. No emulator. */
#define main playback_fixture_main
#include "ghost_playback_host.cpp"
#undef main
#include "../../plugin/gfxwrap/ghost_ipc.cpp"
#include <deque>
#include <fstream>
#include <memory>

namespace fixture {
std::deque<gs_record> queue;
uint32_t configuration=2, epoch=1;
int32_t __cdecl configure(const gc_layout*) {++configuration;return 1;}
void __cdecl disable() {}
int32_t __cdecl read(gs_record* out,uint32_t bytes) {
    if(queue.empty())return 0;
    if(bytes!=sizeof(*out))return -1;
    *out=queue.front();queue.pop_front();return 1;
}
gs_stats __cdecl stats() {
    gs_stats s{};s.bytes=sizeof(s);s.version=GS_VERSION;s.epoch=epoch;
    s.configuration=configuration;s.enabled=1;return s;
}
const gs_api source={sizeof(gs_api),GS_VERSION,GS_CAP_POSE|GS_CAP_WORLD_PASS,0,
    configure,disable,read,stats};
gcc_config config() {
    gcc_config c{};c.version=GCC_VERSION;c.near_zero=30;c.wrap_ceiling=65000;
    c.area_window=3;c.teleport_window=3;c.level_tail=60;c.warp_window=120;
    c.teleport_action=0x1337;c.castle_count=1;c.castle_levels[0]=6;
    c.warp_count=1;c.warp_ops[0]=1;return c;
}
}
namespace ghost_stream {const gs_api* api(){return &fixture::source;}}
extern "C" int32_t sg_model_validate_rom(const uint8_t*,size_t){return SG_OK;}

struct Run {
    Clip clip;
    gipc_status clock{};
    std::unique_ptr<Session> session;
    std::unique_ptr<gipc_page> page=std::make_unique<gipc_page>();
    gipc_request demand{};
    gcc_clock source_clock{};
    Renderer renderer;
    uint64_t sequence=0;
    uint32_t timer=1000,rom=7;
    gc_sample last{};
    // Real order: the renderer draws a list from its own live sample; the
    // worker reads that record only after the list, so its command can first
    // apply to the NEXT list. Counted: frames that should show a ghost but
    // show none.
    bool worker_after_render=false;
    unsigned ghostless=0,wrong=0,frames=0;
    explicit Run(bool segment=false,bool legs=false):clip(segment?72:71,401) {
        clip.header.level=24;clip.header.origin_raw=segment?20:0;
        clip.header.clock_kind=segment?GCLIP_SEGMENT_CLOCK:GCLIP_STAR_CLOCK;
        for(uint32_t i=0;i<clip.frames.size();++i) {
            auto& s=clip.frames[i].sample;s.level=24;
            s.area=legs && i>=40?2:1;
            s.igt=uint16_t(legs && i>=40?i-40:i+clip.header.origin_raw);
            s.pose.position[0]=float(i);s.clock.raw_igt=s.igt;s.clock.elapsed_frames=1000+i;
            s.valid_fields|=GC_VALID_SOUND;s.sound.count=1;s.sound.bits[0]=5000+i; // record i sounds 5000+i
        }
        clock.qpc_frequency=1000;clock.qpc_anchor=1000;clock.utc_anchor=1000;
        opened=rom;++fixture::epoch;
        session=std::make_unique<Session>(clock);
        demand.owner_pid=GetCurrentProcessId();demand.generation=1;
        FILETIME birth{},unused[3]{};GetProcessTimes(GetCurrentProcess(),&birth,unused,unused+1,unused+2);
        demand.owner_created_lo=birth.dwLowDateTime;demand.owner_created_hi=birth.dwHighDateTime;
        demand.token=123;demand.revision=1;demand.heartbeat=1;demand.capture_enabled=1;
        demand.rom_generation=rom;demand.layout.clock=fixture::config();
        session->demand(demand,1);session->demand(demand,1);
        session->rom_bytes.resize(8u<<20);
        std::vector<uint8_t> clip_bytes(sizeof(clip.header)+sizeof(gclip_frame)*clip.frames.size());
        std::memcpy(clip_bytes.data(),&clip.header,sizeof(clip.header));
        std::memcpy(clip_bytes.data()+sizeof(clip.header),clip.frames.data(),sizeof(gclip_frame)*clip.frames.size());
        demand.play_enabled=1;demand.model_id=1;demand.opacity=1;demand.stop_at_star=!segment;
        demand.star_action_count=1;demand.star_actions[0]=0x1302;
        demand.clip_count=1;demand.clips[0].clip_id[0]=segment?72:71;
        // Each Run is its own tracker session and arm: every Run restarts the
        // same global timers, so a restore parked by an earlier Run's close
        // must not be handed to this one.
        static uint64_t sessions=0;
        demand.origin={uint32_t(segment?GO_SEGMENT:GO_STAR),fixture::configuration,fixture::epoch,1000,1000,sessions+=100,9};
        check(ghost_assets::sha256(session->rom_bytes,session->cartridge_digest),"hash original ROM");
        session->cartridge_verified=true;
        check(ghost_assets::sha256(clip_bytes,demand.clips[0].clip_sha256),"hash clip file");
        wchar_t path[GIPC_PATH_CHARS]{};
        check(GetFullPathNameW(L"recovery.rom",GIPC_PATH_CHARS,path,nullptr)!=0,"private ROM path");
        std::memcpy(demand.rom_path,path,sizeof(path));
        std::ofstream(path,std::ios::binary).write(reinterpret_cast<const char*>(session->rom_bytes.data()),session->rom_bytes.size());
        check(GetFullPathNameW(L"recovery.ghost",GIPC_PATH_CHARS,path,nullptr)!=0,"private clip path");
        std::memcpy(demand.clips[0].clip_path,path,sizeof(path));
        std::ofstream(path,std::ios::binary).write(reinterpret_cast<const char*>(clip_bytes.data()),clip_bytes.size());
        demand.revision=2;session->loaded=demand;session->rom_ready=true;
        Session::Asset asset;asset.header=clip.header;asset.bytes=std::move(clip_bytes);
        std::memcpy(asset.id,demand.clips[0].clip_id,16);std::memcpy(asset.sha256,demand.clips[0].clip_sha256,32);
        session->assets.push_back(std::move(asset));
        auto config=fixture::config();check(gcc_init(&source_clock,&config)!=0,"source clock init");
    }
    void step(uint16_t raw,int expected,int area=1,uint32_t action=1,float pose_tag=-1) {
            gc_sample live{};live.bytes=sizeof(live);live.version=GC_VERSION;live.epoch=fixture::epoch;
            live.sequence=live.dlist_serial=++sequence;live.qpc=1000+int64_t(sequence-1);
            live.global_timer=timer++;live.level=24;live.area=int16_t(area);live.igt=raw;live.live_action=action;
            live.valid_fields=GC_VALID_CLOCK|GC_VALID_ACTION|GC_VALID_WARP|GC_VALID_CAMERA|GC_VALID_POSE;
            live.pose=clip.frames[0].sample.pose;live.pose.position[0]=pose_tag<0?float(raw):pose_tag;
            gcc_input input{};input.fields=GCC_REQUIRED;input.epoch=live.epoch;input.sequence=sequence;
            input.global_timer=live.global_timer;input.raw_igt=raw;input.level=24;input.area=area;input.action=action;
            gcc_step(&source_clock,&input,&live.clock);
            gs_record record{};record.bytes=sizeof(record);record.version=GS_VERSION;
            record.configuration=fixture::configuration;record.sample=live;
            const auto worker=[&]{
                fixture::queue.push_back(record);session->publish(*page,demand);
                ++demand.heartbeat;
                // As the worker loop does: a demand that asks for files loads them
                // (a switched-off set was released) and is asked again at once.
                if(session->demand(demand,demand.generation)){session->load_assets();session->demand(demand,demand.generation);}
            };
            if(!worker_after_render)worker();
            ghost::WorldPass world{};world.epoch=live.epoch;world.context_generation=1;world.list_serial=live.dlist_serial;
            bool drawn=false;ghost_compositor::Pose poses[GP_GHOSTS]{};uint32_t count=0;gp_draw_token token{};
            renderer.run([&]{ghost_playback::safe_boundary(rom,fixture::configuration);
                drawn=ghost_playback::evaluate(live,world,poses,count,token);});
            if(worker_after_render)worker();
            gp_status status{};ghost_playback::api()->status(&status,sizeof(status));
            ++frames;
            if(worker_after_render && (drawn!=(expected>=0) || (drawn && selected_x!=float(expected)))) {
                // Counted rather than thrown, so the old path's numbers are measurable.
                std::fprintf(stderr,"%s: raw=%u expected=%d selected=%.0f playback=%u\n",
                    drawn?"wrong":"ghostless",raw,expected,drawn?selected_x:-1.0f,status.decision);
                ++(drawn?wrong:ghostless);last=live;return;
            }
            if(drawn!=(expected>=0) || (drawn && selected_x!=float(expected))) {
                std::fprintf(stderr,"raw=%u expected=%d drawn=%d selected=%.0f IPC=%u/%u playback=%u continuity=%llu\n",
                    raw,expected,drawn,selected_x,session->state,session->reason,status.decision,
                    static_cast<unsigned long long>(live.clock.continuity_id));
                throw std::runtime_error("FOLLOW must restore the saved timeline position");
            }
            check(drawn==(expected>=0),"expected exact selection or refusal");last=live;
    }
    void retire(uint32_t why) {
        session->revoke(why);
        renderer.run([]{ghost_playback::retire_on_context_loss();});
        check(ghost_playback::api()->collect()==GP_OK,"collect witness model");
        check(ghost_playback::api()->release_worker()==GP_OK,"release witness worker");
    }
    void reopen(bool same_rom=true,bool same_session=true) {
        // Real Session close/destruction/recreation, asset read+SHA verification,
        // model retirement and source reconfiguration. No retained Session data.
        retire(GIPC_NO_ROM);check(recovery_cache.timing.bound(),"ROM close parks live checkpoints");
        session.reset();opened=++rom;++fixture::epoch;gcc_reset(&source_clock);
        if(!same_rom) {
            std::fstream file(reinterpret_cast<const wchar_t*>(demand.rom_path),std::ios::binary|std::ios::in|std::ios::out);
            file.seekp(65536);file.put('X');check(bool(file),"different cartridge fixture");
        }
        session=std::make_unique<Session>(clock);++demand.generation;++demand.token;++demand.revision;
        demand.rom_generation=rom;demand.play_enabled=0;
        session->demand(demand,demand.generation);session->demand(demand,demand.generation);
        demand.origin.configuration=fixture::configuration;demand.origin.epoch=fixture::epoch;
        if(!same_session)++demand.origin.session_id;
        demand.play_enabled=1;++demand.revision;
        check(session->demand(demand,demand.generation),"reopen requests actual asset reload");
        session->load_assets();
        check(session->rom_ready && session->assets.size()==1 && session->failed!=demand.revision,
            "reopen validates actual clip/ROM files");
        session->demand(demand,demand.generation);
    }
    ~Run(){retire(GIPC_OK);}
};

int main() {
    try {
        {
            Run run;
            for(uint16_t raw=0;raw<=200;++raw)run.step(raw,raw);
            const auto revision=run.demand.revision;
            run.step(150,150); // Unchanged command; actual GCC continuity rewinds.
            for(int i=0;i<30;++i)run.step(150,150); // Both pause menus hold IGT.
            run.step(151,151);run.step(180,180); // Forward load to an observed state.
            run.step(150,150);run.step(151,151); // The same state can be loaded repeatedly.
            check(run.demand.revision==revision,"only heartbeat changed during recovery");
            run.timer=1150;++fixture::epoch;gcc_reset(&run.source_clock);
            run.demand.origin.epoch=fixture::epoch;++run.demand.revision;
            run.step(150,150);run.step(151,151); // Capture epoch changed on full clock rewind.
            // His grab beats the ghost: it falls where it stood (row 151) and
            // keeps falling through tuning and the tracker's suspension.
            run.step(152,151,1,0x1302);run.step(153,151);
            ++run.demand.revision;run.demand.opacity=.5f;run.step(154,151);
            run.demand.owner_suspended=1;++run.demand.revision;run.step(155,151);
            check(run.session->recovery.bound(),"tracker completion hides without deleting restore history");
            run.step(156,151); // Suspended between arms: its fall plays on.
            run.step(150,150); // A restore while the tracker is between arms draws at once.
            run.demand.owner_suspended=0;++run.demand.revision;
            run.step(150,150); // Restoration, unlike tuning, revives the completed run.
            // Tracker's state_loaded event hands the witnessed restore to its new arm.
            run.demand.origin.arm_id=99;run.demand.origin.global_timer=run.last.global_timer;
            run.demand.origin.utc=1000+double(run.last.qpc-1000)/1000;
            ++run.demand.revision;run.step(151,151);
            run.reopen();run.timer=1150;run.step(150,150);run.step(151,151);
            run.reopen(false);run.timer=1150;run.step(150,-1);
            check(!run.session->recovery.bound(),"different full ROM digest cannot inherit playback history");
        }
        {
            Run run;
            run.step(0,0);
            run.demand.play_enabled=0;++run.demand.revision;
            for(uint16_t raw=1;raw<=200;++raw)run.step(raw,-1);
            run.demand.play_enabled=1;++run.demand.revision;
            run.step(201,201);
            run.step(150,150); // State saved before ghost tracking was enabled.
            run.step(0,0);run.step(1,1);
        }
        {
            Run run;
            for(uint16_t raw=0;raw<=80;++raw)run.step(raw,raw);
            run.reopen(true,false);run.timer=1050;run.step(50,-1);
            check(!run.session->recovery.bound(),"new tracker session cannot inherit playback history");
        }
        for(bool segment:{false,true}) {
            Run run(segment,true);const uint16_t start=segment?20:0;
            // Template enters area2 at coordinate40; LIVE enters at coordinate80.
            for(uint16_t raw=start;raw<=start+80;++raw)run.step(raw,raw-start<40?raw-start:-1);
            for(uint16_t raw=0;raw<=50;++raw)run.step(raw,80+raw,2);
            run.step(35,115,2);run.step(45,125,2);
            run.reopen();run.timer=1000+81+35;run.step(35,115,2);
            if(segment) {
                for(uint16_t raw=36;raw<=50;++raw)run.step(raw,80+raw,2);
                for(uint16_t raw=0;raw<=50;++raw)run.step(raw,-1,1);
                run.step(35,-1,1); // Same exact live state named coordinates15 and165.
                check(!run.session->have_origin,"ambiguous live checkpoints refuse recovery");
                run.step(45,-1,1,1,999); // Matching raw IGT with unrelated graphics state is insufficient.
                check(!run.session->have_origin,"unrelated state cannot rebind timing");
            }
        }
        unsigned blink_loads=0,blink_frames=0,blink_ghostless=0,blink_wrong=0,blink_sounded=0,blink_mismatched=0;
        for(bool segment:{false,true}) {
            // State loads with the worker a list behind the renderer, as live.
            Run run(segment);const int start=segment?20:0;
            for(int raw=start;raw<=start+200;++raw)run.step(uint16_t(raw),raw-start);
            run.worker_after_render=true;run.ghostless=run.wrong=run.frames=0;
            ghost_playback::set_sound_sink(&test_sink);
            const auto load=[&](int raw,int hold) {
                ++blink_loads;
                run.step(uint16_t(raw),raw-start);
                // After a load the ghost sounds each record it passes, once:
                // the worker's confirming command a list later re-bases nothing.
                sound_count=0;
                for(int i=1;i<=hold;++i)run.step(uint16_t(raw+i),raw+i-start);
                bool exact=sound_count==unsigned(hold);
                for(int i=0;exact && i<hold;++i)exact=sound_log[i]==uint32_t(5000+raw-start+1+i);
                ++(exact?blink_sounded:blink_mismatched);
            };
            load(start+150,3);  // rewind to an earlier state
            load(start+120,0);  // again, straight away
            load(start+180,3);  // forward to a state seen before the rewinds
            load(start+150,30); // the same state again, then play on
            if(!segment) {
                // His star grab beats the ghost: it falls where it stood (row
                // 180); loading back revives it at once.
                run.step(uint16_t(start+181),180,1,0x1302);run.step(uint16_t(start+182),180);
                // The live grab closes the attempt: the owner suspends (as
                // NativeGhost.note does) before he loads the state again.
                run.demand.owner_suspended=1;++run.demand.revision;
                run.step(uint16_t(start+183),180);run.step(uint16_t(start+184),180);
                ++blink_loads;run.step(uint16_t(start+150),150); // drawn on the load frame, still suspended
                run.step(uint16_t(start+151),151);
                // The owner re-arms from the state_loaded anchor of the load
                // frame (a poll later): the worker hands the restore to the new arm.
                run.demand.owner_suspended=0;run.demand.origin.arm_id+=100;
                run.demand.origin.global_timer=run.last.global_timer-1;
                run.demand.origin.utc=1000+double(run.last.qpc-1-1000)/1000;++run.demand.revision;
                for(int raw=152;raw<=156;++raw)run.step(uint16_t(start+raw),raw);
            } else {
                // A forward load to a state never witnessed stays hidden on
                // the renderer too; a known state afterwards draws at once.
                run.step(uint16_t(start+300),-1);run.step(uint16_t(start+301),-1);
                load(start+150,3);
                // A completed segment suspends the owner: no native star end
                // hides it, the suspension does, until a load revives it.
                run.demand.owner_suspended=1;++run.demand.revision;
                run.step(uint16_t(start+154),154); // the command reaches the next list
                run.step(uint16_t(start+155),-1);run.step(uint16_t(start+156),-1);
                ++blink_loads;run.step(uint16_t(start+140),140);run.step(uint16_t(start+141),141);
                run.demand.owner_suspended=0;run.demand.origin.arm_id+=100;
                run.demand.origin.global_timer=run.last.global_timer-1;
                run.demand.origin.utc=1000+double(run.last.qpc-1-1000)/1000;++run.demand.revision;
                for(int raw=142;raw<=145;++raw)run.step(uint16_t(start+raw),raw);
            }
            blink_frames+=run.frames;blink_ghostless+=run.ghostless;blink_wrong+=run.wrong;
            ghost_playback::set_sound_sink(nullptr);
        }
        check(blink_mismatched==0,"after every state load each passed record sounds once");
        unsigned stress_loads=0,stress_frames=0,stress_ghostless=0,stress_wrong=0,restart_hidden=0;
        for(bool segment:{false,true}) {
            // The live break, 2026-09-23: mid-run the recorder's capture
            // restarted (a GPU capture failure reconnects it: a new channel
            // owner token, capture namespace and source clock in the same
            // trainer process), and the ghost stayed hidden until the next
            // attempt. Then hundreds of rapid state loads, Usamune style (the
            // global timer runs on, IGT holds a few frames) and PJ64 style (the
            // global timer rewinds), backward, forward and repeated.
            Run run(segment);const int start=segment?20:0;
            uint32_t first_timer[401]{};
            for(int raw=start;raw<=start+380;++raw) {first_timer[raw-start]=run.timer;run.step(uint16_t(raw),raw-start);}
            run.worker_after_render=true;run.ghostless=run.wrong=run.frames=0;
            ++run.demand.token;++run.demand.revision;++fixture::epoch;gcc_reset(&run.source_clock);
            run.demand.origin.configuration=fixture::configuration+1;run.demand.origin.epoch=fixture::epoch;
            // Play on after the restart. The run's origin was captured before
            // it, so only the parked checkpoints can place the ghost again: a
            // star by its own IGT at once, a segment at its next witnessed load.
            int raw=start+381;
            if(!segment) {
                unsigned hidden=0;bool back=false;
                for(int i=0;i<10;++i,++raw) {
                    const auto before=run.ghostless;run.step(uint16_t(raw),raw-start);
                    if(run.ghostless!=before)++hidden;else back=i>=5;
                }
                check(back,"a star ghost returns after a capture restart without a new attempt");
                restart_hidden=hidden;run.ghostless-=hidden;
            } else {
                for(int i=0;i<10;++i,++raw)run.step(uint16_t(raw),-1); // unwitnessed: hidden, not wrong
                run.ghostless=0;
            }
            uint32_t seed=12345u+uint32_t(segment);
            const auto next=[&](uint32_t bound){seed=seed*1103515245u+12345u;return (seed>>8)%bound;};
            for(int load=0;load<300;++load) {
                // A segment load to near-zero IGT reads as a level retry, which
                // the tracker owns (it re-arms), so segment loads stay above it.
                const int to=int(segment?11+next(349):next(360));const bool rewind_timer=next(4)==0;
                if(rewind_timer)run.timer=first_timer[to];
                ++stress_loads;
                const int hold=int(next(5)),play=int(next(7));
                run.step(uint16_t(start+to),to);
                for(int i=0;i<hold;++i)run.step(uint16_t(start+to),to);          // IGT holds after a load
                for(int i=1;i<=play;++i)run.step(uint16_t(start+to+i),to+i);    // then play on
            }
            stress_frames+=run.frames;stress_ghostless+=run.ghostless;stress_wrong+=run.wrong;
        }
        std::printf("{\"ok\":true,\"checks\":%u,\"render_allocations\":%u,\"render_frees\":%u,"
            "\"blink\":{\"loads\":%u,\"frames\":%u,\"ghostless_frames\":%u,\"wrong_frames\":%u},"
            "\"stress\":{\"loads\":%u,\"frames\":%u,\"ghostless_frames\":%u,\"wrong_frames\":%u,"
            "\"hidden_after_capture_restart\":%u}}\n",
            checks,allocations.load(),destructions.load(),blink_loads,blink_frames,blink_ghostless,blink_wrong,
            stress_loads,stress_frames,stress_ghostless,stress_wrong,restart_hidden);return 0;
    } catch(const std::exception& e) {std::fprintf(stderr,"%s\n",e.what());return 1;}
}
