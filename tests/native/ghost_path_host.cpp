/* The ghost's path, CPU only (ghost_path.h): which recorded frames the window
 * holds and how its mesh is laid out. No GL, model, emulator or heap use by
 * the code under test (operator new is counted while it runs). */
#include "ghost_path.h"
#include "ghost_playback.h"
#include "ghost_ipc.h"
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <stdexcept>
#include <fstream>
#include <iterator>
#include <string>
#include <algorithm>
#include <vector>

static bool counting=false;
static std::atomic<unsigned> allocations{0};
void* operator new(size_t n) {
    if(counting)allocations.fetch_add(1);
    if(void* p=std::malloc(n?n:1))return p;
    throw std::bad_alloc();
}
void* operator new[](size_t n) {return ::operator new(n);}
void operator delete(void* p) noexcept {std::free(p);}
void operator delete[](void* p) noexcept {std::free(p);}
void operator delete(void* p,size_t) noexcept {std::free(p);}
void operator delete[](void* p,size_t) noexcept {std::free(p);}
static unsigned checks=0;
static void check(bool ok,const char* what) {++checks;if(!ok)throw std::runtime_error(what);}
static bool near(float a,float b,float tolerance=1e-4f) {return std::fabs(a-b)<=tolerance;}

// A recorded run: one record a frame, posed, on the ground in WDW (level 11).
struct Run {
    std::vector<gc_sample> samples;
    explicit Run(uint32_t count,float speed=10) : samples(count) {
        for(uint32_t i=0;i<count;++i) {
            auto& s=samples[i];s.bytes=sizeof(s);s.version=GC_VERSION;s.level=11;s.area=1;
            s.valid_fields=GC_VALID_POSE|GC_VALID_ACTION|GC_VALID_WARP|GC_VALID_PAD;
            s.live_action=0x04000440; // ACT_WALKING: neither air nor swimming
            s.pose.position[0]=float(i)*speed;s.pose.position[1]=100;s.pose.position[2]=0;
            s.clock.transition=GCC_STEADY;
        }
    }
};
// Track over a Run: coordinate = index, optionally averaged against others.
struct Track {
    const Run& run;
    std::vector<ghost_path::Recorded> cache;
    explicit Track(const Run& r):run(r),cache(r.samples.size()) {
        for(uint32_t i=0;i<r.samples.size();++i)ghost_path::record(r.samples[i],i,nullptr,cache[i]);
    }
    uint32_t size() const {return uint32_t(run.samples.size());}
    uint64_t coordinate(uint32_t i) const {return i;}
    bool where(uint32_t i,int16_t level,int16_t area,float out[3]) const {
        const auto& s=run.samples[i];float dx=0;
        if(!(s.valid_fields&GC_VALID_POSE) || !ghost_scene::translation(s.level,s.area,level,area,dx))return false;
        out[0]=s.pose.position[0]+dx;out[1]=s.pose.position[1];out[2]=s.pose.position[2];return true;
    }
    const ghost_path::Recorded& at(uint32_t i) {return cache[i];}
};
static ghost_path::Frame frame_storage;
static ghost_path::Mesh mesh_storage;

static void requests() {
    gp_request r{};r.path_enabled=1;r.path_ahead_ms=3000;r.path_width=40;r.path_opacity=.85f;r.path_spacing_ms=100;
    check(ghost_path::valid_request(r,false,0),"the card's defaults are a valid request");
    const gp_request zero{};check(ghost_path::valid_request(zero,true,1),"a zero path block is no path, not an error");
    auto bad=r;bad.path_ahead_ms=0;check(!ghost_path::valid_request(bad,false,0),"a path that is on needs its tuning");
    for(uint32_t ms:{GP_PATH_AHEAD_MS_MIN,GP_PATH_AHEAD_MS_MAX}) {bad=r;bad.path_ahead_ms=ms;check(ghost_path::valid_request(bad,false,0),"look-ahead limits");}
    for(uint32_t ms:{GP_PATH_AHEAD_MS_MIN-1,GP_PATH_AHEAD_MS_MAX+1}) {bad=r;bad.path_ahead_ms=ms;check(!ghost_path::valid_request(bad,false,0),"look-ahead past its limits");}
    for(float w:{GP_PATH_WIDTH_MIN,GP_PATH_WIDTH_MAX}) {bad=r;bad.path_width=w;check(ghost_path::valid_request(bad,false,0),"width limits");}
    for(float w:{GP_PATH_WIDTH_MIN-.5f,GP_PATH_WIDTH_MAX+.5f,NAN,INFINITY}) {bad=r;bad.path_width=w;check(!ghost_path::valid_request(bad,false,0),"width past its limits");}
    for(float o:{-.01f,1.01f,NAN}) {bad=r;bad.path_opacity=o;check(!ghost_path::valid_request(bad,false,0),"strength past 0..1");}
    for(uint32_t ms:{GP_PATH_SPACING_MS_MIN-1,GP_PATH_SPACING_MS_MAX+1}) {bad=r;bad.path_spacing_ms=ms;check(!ghost_path::valid_request(bad,false,0),"spacing past its limits");}
    bad=r;bad.path_enabled=0;bad.path_width=NAN;check(ghost_path::valid_request(bad,false,0),"off, its tuning is unread");
    bad=r;bad.path_enabled=2;check(!ghost_path::valid_request(bad,false,0),"on/off is 0 or 1");
    bad=r;bad.path_buttons=2;check(!ghost_path::valid_request(bad,false,0),"buttons on/off is 0 or 1");
    bad=r;bad.path_source=2;check(!ghost_path::valid_request(bad,false,0),"source is fastest or average");
    bad=r;bad.path_enabled=1;bad.path_clip=1;
    check(!ghost_path::valid_request(bad,true,1),"a playing path names one of the playing ghosts");
    check(ghost_path::valid_request(bad,true,2),"the second of two ghosts can be the fastest");
    check(ghost_path::valid_request(bad,false,0),"a request that plays nothing keeps its path switch");
    bad.path_clip=GP_GHOSTS;check(!ghost_path::valid_request(bad,false,0),"path_clip is a clips[] index");
    gipc_request ipc{};ipc.path_enabled=1;ipc.path_ahead_ms=3000;ipc.path_width=40;ipc.path_opacity=.85f;ipc.path_spacing_ms=100;
    check(ghost_path::valid_request(ipc,false,0),"the IPC request shares the rule");
    ipc.path_spacing_ms=GP_PATH_SPACING_MS_MAX+1;check(!ghost_path::valid_request(ipc,false,0),"and its ranges");
}

static void records() {
    Run run(4);
    auto& air=run.samples[1];air.live_action=0x03000880; // ACT_JUMP: ACT_FLAG_AIR
    auto& swim=run.samples[2];swim.live_action=0x300024E0; // ACT_WATER_IDLE-like: ACT_FLAG_SWIMMING
    ghost_path::Recorded r{};
    ghost_path::record(run.samples[0],7,nullptr,r);check(r.posed && !r.air && r.coordinate==7,"walking is on the ground");
    ghost_path::record(air,0,nullptr,r);check(r.air,"ACT_FLAG_AIR is in the air");
    ghost_path::record(swim,0,nullptr,r);check(r.air,"ACT_FLAG_SWIMMING draws at his own height too");
    auto unknown=run.samples[3];unknown.valid_fields&=~uint32_t(GC_VALID_ACTION);unknown.pose.action=0x03000880;
    ghost_path::record(unknown,0,nullptr,r);check(r.air,"without the live action the pose's action decides");
    auto unposed=run.samples[3];unposed.valid_fields&=~uint32_t(GC_VALID_POSE);
    ghost_path::record(unposed,0,nullptr,r);check(!r.posed,"a refused pose is no point");
    auto warping=run.samples[3];warping.pending_warp_op=1;ghost_path::record(warping,0,nullptr,r);check(r.warping,"pending warp");
    auto warped=run.samples[3];warped.clock.transition=GCC_AREA_WARP;ghost_path::record(warped,0,nullptr,r);check(r.warped,"the clock's warp");
    // The shadow: a 3x3 grid on a floor tilted 45 degrees about x, under an
    // identity recorded camera, its executed matrix a translation.
    auto shaded=run.samples[3];shaded.valid_fields|=GC_VALID_SHADOW;
    auto& m=shaded.shadow.matrix;std::memset(m,0,sizeof(m));m[0]=m[5]=m[10]=m[15]=1;m[12]=500;m[13]=20;m[14]=-300;
    for(unsigned v=0;v<9;++v) {
        const int16_t x=int16_t((int(v%3)-1)*50),z=int16_t((int(v/3)-1)*50);
        shaded.shadow.position[v][0]=x;shaded.shadow.position[v][1]=z;shaded.shadow.position[v][2]=z;
    }
    const float identity[16]={1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    ghost_path::record(shaded,0,identity,r);
    check(r.shadowed && near(r.floor[0],500) && near(r.floor[1],20) && near(r.floor[2],-300),"the floor under him is the shadow's centre");
    check(near(r.normal[0],0) && near(r.normal[1],std::sqrt(.5f)) && near(r.normal[2],-std::sqrt(.5f)),"its normal crosses the diagonals, upward");
    ghost_path::record(shaded,0,nullptr,r);check(!r.shadowed,"no recorded camera: no floor");
}

static void averaging() {
    ghost_path::Recorded fastest{},others[2]{};
    fastest.posed=true;fastest.level=11;fastest.area=1;fastest.position[0]=0;fastest.shadowed=true;
    fastest.floor[0]=0;fastest.floor[1]=-50;
    for(auto& o:others) {o.posed=true;o.level=11;o.area=1;}
    others[0].position[0]=90;others[1].position[0]=-30;others[1].position[2]=60;
    auto mean=fastest;check(ghost_path::average(mean,others,2),"three ghosts on one line average");
    check(near(mean.position[0],20) && near(mean.position[2],20) && near(mean.floor[0],20) && near(mean.floor[2],20)
        && near(mean.floor[1],-50),"their time-aligned mean, the floor moved across with it");
    others[0].position[0]=200.5f;mean=fastest;
    check(!ghost_path::average(mean,others,2) && near(mean.position[0],0),"one ghost past the 200-unit gate: the fastest's point");
    others[0].position[0]=200;mean=fastest;check(ghost_path::average(mean,others,2),"exactly at the gate still averages");
    others[0].posed=false;mean=fastest;check(!ghost_path::average(mean,others,2),"a ghost with no record there: the fastest's point");
    others[0].posed=true;others[0].area=2;mean=fastest;check(!ghost_path::average(mean,others,2),"another scene: the fastest's point");
    mean=fastest;check(!ghost_path::average(mean,others,0),"one ghost alone is its own path");
}

// Progress along the path: forward only, the first pass of a place first,
// held off the route, caught up after a shortcut, and a window that glides.
static void progressing() {
    // A loop: out along +x for 60 frames, back along the same line (60..119),
    // so every early point has a later twin at the same place.
    Run run(240);
    for(uint32_t i=0;i<240;++i) {
        const float x=i<60?float(i)*10:i<120?float(119-i)*10+5:float(i-120)*10+2000;
        run.samples[i].pose.position[0]=x;
    }
    Track track(run);ghost_path::Progress p{};
    float live[3]={100,100,0};
    check(ghost_path::follow(track,live,11,1,p,true,5) && p.record==10,"a new run starts from the ghost's own record, then moves to him");
    check(p.shown==5,"a new progress puts the window there at once");
    live[0]=300;check(ghost_path::follow(track,live,11,1,p,false,0) && p.record==30,"forward to him");
    // Near x=300 the clip passes twice (records 30 and 89, at 305): the first pass holds.
    live[0]=300.4f;check(ghost_path::follow(track,live,11,1,p,false,0) && p.record==30,"the first pass of a place, not its later twin");
    // He follows the route all the way out and back: progress keeps up, frame for frame.
    bool kept=true;
    for(uint32_t k=31;k<120;++k) {
        live[0]=track.cache[k].position[0];
        kept=kept && ghost_path::follow(track,live,11,1,p,false,0) && p.record==k;
    }
    check(kept,"following the route, progress keeps up with him exactly");
    // Back toward the start (record 10's place): the line ahead holds, never back.
    live[0]=100;ghost_path::follow(track,live,11,1,p,false,0);check(p.record==119,"progress never moves backward");
    // Off the route it holds, then a shortcut 100 frames ahead is caught up with.
    live[0]=2000;live[2]=5000;
    for(uint32_t k=0;k<GP_PATH_STALL_FRAMES;++k)check(!ghost_path::follow(track,live,11,1,p,false,0),"off the route");
    check(p.record==119,"off the route, progress holds");
    live[2]=0;live[0]=track.cache[219].position[0];
    check(ghost_path::follow(track,live,11,1,p,false,0) && p.record==219,"after a stall the reach widens to catch a shortcut");
    // The widened reach holds both passes of x=200 (records 20 and 99, at
    // 205): the first pass is taken though the later one is nearer him.
    ghost_path::Progress wide{};wide.known=true;wide.stalled=GP_PATH_STALL_FRAMES;live[0]=203;
    check(ghost_path::follow(track,live,11,1,wide,false,0) && wide.record==20,"a place's first pass, though its later pass is nearer");
    // Without that stall, a place more than a second of the ghost's run ahead is not his.
    ghost_path::Progress q{};q.known=true;q.record=120;q.shown=120;
    live[0]=track.cache[120+GP_PATH_REACH_FRAMES+20].position[0];
    check(!ghost_path::follow(track,live,11,1,q,false,0) && q.record==120,"never more than the reach ahead in one list");
    // No hint: the first pass of the whole clip near him.
    ghost_path::Progress fresh{};live[0]=300;
    check(ghost_path::follow(track,live,11,1,fresh,false,0) && fresh.record==30,"no hint: the clip's first pass near him");
    ghost_path::Progress lost{};live[0]=9999;
    check(!ghost_path::follow(track,live,11,1,lost,false,0) && !lost.known,"nowhere near: not yet");
    live[0]=300;bool found=false;unsigned lists=0;
    while(!found && lists<GP_PATH_STALL_FRAMES+1) {found=ghost_path::follow(track,live,11,1,lost,false,0);++lists;}
    check(found && lost.record==30 && lists<=GP_PATH_STALL_FRAMES,"searched again within GP_PATH_STALL_FRAMES lists");
    float nowhere[3]={NAN,0,0};check(!ghost_path::follow(track,nowhere,11,1,p,false,0) && p.record==219,"an unknown live Mario holds it");
    // Another scene: only that scene's records count.
    for(uint32_t i=200;i<240;++i)run.samples[i].area=2;
    Track doors(run);ghost_path::Progress through{};live[0]=track.cache[230].position[0];
    check(ghost_path::follow(doors,live,11,2,through,false,0) && through.record==230,"his scene's records only");
    // The window glides: a catch-up slides over several lists, never past, never back.
    ghost_path::Progress g{};g.known=true;g.record=100;g.shown=60;
    double before=g.shown;bool forward=true;unsigned steps=0;
    while(g.shown<100 && steps<100) {ghost_path::glide(track,g);forward=forward && g.shown>before && g.shown<=100;before=g.shown;++steps;}
    check(forward && steps>3 && steps<20,"a 40-frame correction slides over a handful of lists");
    check(g.shown==100,"and lands exactly");
    g.record=101;ghost_path::glide(track,g);check(g.shown==101,"ordinary running is never behind");
    g.shown=150;ghost_path::glide(track,g);check(g.shown==101,"never ahead of the progress");
}

static void windows() {
    Run run(400);
    // Frame 50: press A (with START, which marks nothing); 51 holds it; 52
    // releases; 53 presses A again with B and Z. 54-56 have no known pad.
    run.samples[50].pad.buttons=0x8000|0x1000;run.samples[51].pad.buttons=0x8000;run.samples[53].pad.buttons=0xe000;
    for(uint32_t i=54;i<=56;++i){run.samples[i].valid_fields&=~uint32_t(GC_VALID_PAD);run.samples[i].pad={};}
    run.samples[57].pad.buttons=0x8000;
    // A warp: pending from 70, executed between 74 and 75 (he lands elsewhere).
    for(uint32_t i=70;i<=74;++i)run.samples[i].pending_warp_op=0x10;
    for(uint32_t i=75;i<400;++i)run.samples[i].pose.position[2]=5000;
    // An area change at 90, a clip gap (coordinate jumps) at 100.
    for(uint32_t i=90;i<400;++i)run.samples[i].area=2;
    Track track(run);
    struct Gap : Track {using Track::Track;uint64_t coordinate(uint32_t i) const {return i<100?i:i+1;}
        const ghost_path::Recorded& at(uint32_t i) {cache[i].coordinate=coordinate(i);return cache[i];}};
    Gap gapped(run);
    ghost_path::Progress at{};at.known=true;at.record=40;at.shown=40;
    auto& f=frame_storage;
    counting=true;ghost_path::window(gapped,at,11,1,1500,f);counting=false;
    // 0.5 s behind (15 frames) through the last record of his scene (area 2 from 90).
    check(f.count==90-25,"0.5 s behind through the last record of his scene");
    check(f.points[0].coordinate==25 && f.points[0].alpha==0,"the very back has faded out");
    check(near(f.points[10].alpha,GP_PATH_BEHIND_ALPHA),"behind him at 25%");
    check(near(f.points[15].alpha,1),"from his place on, full strength");
    check(!(f.points[0].flags&ghost_path::joined) && (f.points[1].flags&ghost_path::joined),"the window starts its own line");
    check(!(f.points[50].flags&ghost_path::joined),"the executed warp breaks the line");
    check((f.points[49].flags&ghost_path::joined),"a pending warp alone does not");
    check(f.points[25].pressed==0x8000,"a press edge: A, and START marks nothing");
    check(f.points[26].pressed==0,"a held button is no new press");
    check(f.points[28].pressed==0xe000,"same-frame presses together");
    check(f.points[32].pressed==0,"after unknown pads nothing is guessed");
    check(near(f.points[20].speed,10),"speed: horizontal units per frame");
    // The window's strength: full to 1.5 s, fading to nothing by 2.5 s; 0.5 s behind.
    check(near(ghost_path::strength(45,1500),1) && near(ghost_path::strength(60,1500),.5f) && ghost_path::strength(76,1500)==0,
          "1.5 s full, then 1 s of fade");
    check(near(ghost_path::strength(-5,1500),GP_PATH_BEHIND_ALPHA) && near(ghost_path::strength(-12,1500),.1f)
          && ghost_path::strength(-15,1500)==0 && ghost_path::strength(-16,1500)==0,"0.5 s behind, its back fading out");
    // A gliding window moves its strengths with it, between whole frames.
    at.shown=39.5;ghost_path::window(gapped,at,11,1,1500,f);
    check(near(f.points[15].alpha,1) && f.points[15].coordinate==40 && near(f.points[14].alpha,GP_PATH_BEHIND_ALPHA),
          "the window's place is the glide's, not the progress record's");
    at.record=95;at.shown=95;
    ghost_path::window(gapped,at,11,2,1500,f);
    unsigned gap_at=0;for(uint32_t i=0;i<f.count;++i)if(f.points[i].coordinate==101)gap_at=i;
    check(gap_at && !(f.points[gap_at].flags&ghost_path::joined) && (f.points[gap_at-1].flags&ghost_path::joined),
          "across a clip gap the line breaks");
    check(f.points[0].coordinate==90 && !(f.points[0].flags&ghost_path::joined),"the area change breaks it: never across the level");
    // The longest window fits its storage.
    Run long_run(2000);Track long_track(long_run);
    ghost_path::Progress middle{};middle.known=true;middle.record=1000;middle.shown=1000;
    ghost_path::window(long_track,middle,11,1,GP_PATH_AHEAD_MS_MAX,f);
    check(f.count==(GP_PATH_BEHIND_MS+GP_PATH_AHEAD_MS_MAX+GP_PATH_FADE_MS)*30/1000+1 && f.count<=ghost_path::samples,
          "10 s ahead, its fade and 0.5 s behind fit GP_PATH_SAMPLES");
    check(allocations==0,"the window allocates nothing");
}

// A frame for the mesh: ground, a jump (air), ground again.
static ghost_path::Frame& jump_frame() {
    auto& f=frame_storage;f=ghost_path::Frame{};
    f.width=40;f.opacity=1;f.spacing_ms=100;f.chevrons=true;f.eye[0]=0;f.eye[1]=2000;f.eye[2]=-3000;
    float* m=f.view_projection;m[0]=m[5]=m[10]=1;m[11]=1;m[15]=1000; // w = z + 1000
    f.count=30;
    for(uint32_t i=0;i<f.count;++i) {
        auto& p=f.points[i];p={};
        p.position[0]=float(i)*20;p.position[1]=i>=10 && i<20?100+float((i-10)*(19-i)):100;p.position[2]=0;
        p.floor[0]=p.position[0];p.floor[1]=100;p.normal[1]=1;
        p.coordinate=300+i;p.alpha=1;p.speed=20;
        p.flags=uint8_t((i?ghost_path::joined:0)|(i>=10 && i<20?ghost_path::air|ghost_path::shadowed:0));
    }
    return f;
}
static void meshes() {
    auto& f=jump_frame();auto& mesh=mesh_storage;
    counting=true;ghost_path::build(f,mesh);counting=false;
    check(allocations==0,"building the mesh allocates nothing");
    using namespace ghost_path;
    // Ground pairs: 1..9 and 21..29 = 18 segments; air: 10..20 = 11 segments.
    check(mesh.count[casing]==18*6 && mesh.count[ribbon]==18*6,"the ground ribbon and its casing, one quad a ground segment");
    check(mesh.count[air_ribbon]==11*6,"every segment with an airborne end is the air ribbon");
    // Dots every 3 frames under the air (coordinates 312, 315, 318), a takeoff and a landing bar.
    check(mesh.count[marks]==3*6+2*6,"floor dots every 3 frames and the takeoff and landing bars");
    check(mesh.count[icons]==0,"no presses, no icons");
    // The casing is 1.35 times the ribbon's width, both lifted onto the floor.
    const auto& c=mesh.vertices[mesh.first[casing]];const auto& c1=mesh.vertices[mesh.first[casing]+1];
    const auto& r=mesh.vertices[mesh.first[ribbon]];const auto& r1=mesh.vertices[mesh.first[ribbon]+1];
    check(near(std::fabs(c1.position[2]-c.position[2]),40*GP_PATH_CASING_WIDTH,1e-3f)
        && near(std::fabs(r1.position[2]-r.position[2]),40,1e-3f),"casing 1.35 x the 40-unit ribbon");
    check(near(r.position[1],100+GP_PATH_LIFT),"lifted along the floor's normal");
    check(near(c.rgba[3],GP_PATH_CASING_ALPHA) && near(c.rgba[0],0x1E/255.f),"a dark casing at 60%");
    // Chevrons: v rises one 8-row cell per spacing of recorded time.
    const auto& a=mesh.vertices[mesh.first[ribbon]];const auto& b=mesh.vertices[mesh.first[ribbon]+2];
    check(a.mode==float(chevron) && near(b.uv[1]-a.uv[1],float(frame_ms/100.0),1e-5f),"chevron v: one cell per 100 ms");
    f.spacing_ms=50;build(f,mesh);
    check(near(mesh.vertices[mesh.first[ribbon]+2].uv[1]-mesh.vertices[mesh.first[ribbon]].uv[1],float(frame_ms/50.0),1e-5f),
          "half the spacing, twice the chevrons");
    f.chevrons=false;build(f,mesh);check(mesh.vertices[mesh.first[ribbon]].mode==float(solid),"no verified chevron: a plain ribbon");
    // The air ribbon is a third as wide and faces the camera.
    const auto& q0=mesh.vertices[mesh.first[air_ribbon]];const auto& q1=mesh.vertices[mesh.first[air_ribbon]+1];
    float across[3]={q1.position[0]-q0.position[0],q1.position[1]-q0.position[1],q1.position[2]-q0.position[2]};
    check(near(length3(across),40*GP_PATH_AIR_WIDTH,1e-3f),"the air ribbon is a third of the width");
    float view[3]={f.eye[0]-180,f.eye[1]-100,f.eye[2]};normalize3(view); // from its first end, point 9
    check(near(dot3(across,view),0,1e-2f),"and faces the camera");
    // Speed tint: grey at rest, green at a full run, the dive cap, white past 64.
    float rgb[3];
    speed_color(0,rgb);check(near(rgb[0],0x6E/255.f) && near(rgb[1],0x6E/255.f),"0: grey");
    speed_color(32,rgb);check(near(rgb[0],0x38/255.f) && near(rgb[1],0xD4/255.f) && near(rgb[2],0x5E/255.f),"32: green");
    speed_color(48,rgb);check(near(rgb[1],0xF0/255.f),"48: the dive and long-jump cap");
    speed_color(80,rgb);check(near(rgb[0],1) && near(rgb[1],1) && near(rgb[2],1),"64 and past: white");
    speed_color(16,rgb);check(near(rgb[1],(0x6E+(0xD4-0x6E)*.5f)/255.f),"linear between anchors");
    // Near the camera the path fades out.
    f.eye[0]=0;f.eye[1]=100;f.eye[2]=0;f.chevrons=true;build(f,mesh);
    check(mesh.vertices[mesh.first[casing]].rgba[3]==0,"at the camera: nothing");
    // Presses: icons in table order across the line, above his root.
    f=jump_frame();f.buttons=true;f.icons=true;f.points[5].pressed=0x8000|0x2000|0x0001;build(f,mesh);
    check(mesh.count[icons]==3*6,"one icon a pressed button");
    const auto& i0=mesh.vertices[mesh.first[icons]];
    check(near(i0.position[1],100+GP_PATH_BUTTON_HEIGHT) && i0.mode==float(icon),"above his root");
    check(near(i0.uv[0],0) && near(mesh.vertices[mesh.first[icons]+6].uv[0],3*16/256.f)
        && near(mesh.vertices[mesh.first[icons]+12].uv[0],8*16/256.f),"Usamune's table order: A, Z, C-right");
    // The line runs along +x on screen, so the row runs across it: up and down.
    const float x0=(mesh.vertices[mesh.first[icons]].offset[0]+mesh.vertices[mesh.first[icons]+1].offset[0])*.5f;
    const float x2=(mesh.vertices[mesh.first[icons]+12].offset[0]+mesh.vertices[mesh.first[icons]+13].offset[0])*.5f;
    check(near(x0,x2,1e-3f),"side by side across the direction of travel");
    // World-sized: GP_PATH_BUTTON_SIZE units tall at the icon's distance
    // (w = z + 1000 here, the projection's y scale 1), capped near the camera.
    auto icon_height=[&](float z) {
        auto& near_frame=jump_frame();near_frame.buttons=near_frame.icons=true;
        for(uint32_t i=0;i<near_frame.count;++i)near_frame.points[i].position[2]=z;
        near_frame.points[5].pressed=0x8000;build(near_frame,mesh);
        return mesh.vertices[mesh.first[icons]+2].offset[1]-mesh.vertices[mesh.first[icons]].offset[1];
    };
    const float mid=icon_height(0),far=icon_height(3000),close=icon_height(-950);
    check(near(mid,GP_PATH_BUTTON_SIZE/1000.0f,1e-6f),"30 world units tall at its distance");
    check(near(far,GP_PATH_BUTTON_SIZE/4000.0f,1e-6f) && far<mid,"smaller the further away");
    check(near(close,GP_PATH_BUTTON_MAX_SCREEN/120.0f,1e-6f),"never taller than its cap near the camera");
    f=jump_frame();f.buttons=true;f.icons=true;f.points[5].pressed=0x8000|0x2000|0x0001;build(f,mesh);
    f.buttons=false;build(f,mesh);check(mesh.count[icons]==0,"off by default");
    f.buttons=true;f.icons=false;build(f,mesh);check(mesh.count[icons]==0,"no Usamune icons in the ROM: none");
    // The worst case fits: every point a takeoff or landing with a dot.
    auto& worst=frame_storage;worst=jump_frame();worst.count=ghost_path::samples;
    for(uint32_t i=0;i<worst.count;++i) {
        auto& p=worst.points[i];p=worst.points[i%30];p.position[0]=float(i)*20;p.coordinate=i*3;
        p.flags=uint8_t((i?joined:0)|((i/2)%2?air|shadowed:0));p.pressed=0xffff&marked;
    }
    worst.buttons=worst.icons=true;
    counting=true;build(worst,mesh);counting=false;
    check(mesh.total<=max_vertices && mesh.count[icons]==max_icons*6,"the worst window fits the preallocated mesh");
    check(valid(worst),"its frame is valid");
    worst.points[3].alpha=2;check(!valid(worst),"a frame out of range is refused");
    check(allocations==0,"nothing allocated");
}

// His recordings (tests/fixtures/ghost_path_revisits.bin.gz, decompressed by
// the test): in each pair the other attempt is live Mario, replayed frame by
// frame against the fastest attempt's path exactly as playback runs it each
// list (follow, glide, window). The nearest-record anchoring it replaced runs
// beside it on the same frames, so the measures are shown to see its failure.
#pragma pack(push,1)
struct FixRecord {uint32_t coordinate;int16_t x,y,z,level;uint8_t area,flags;uint16_t zero;};
#pragma pack(pop)
static_assert(sizeof(FixRecord)==16,"fixture record");
struct FixClip {std::string name;uint32_t attempt=0;std::vector<FixRecord> records;};
struct FixTrack {
    const FixClip& clip;std::vector<ghost_path::Recorded> cache;
    explicit FixTrack(const FixClip& c):clip(c),cache(c.records.size()) {
        for(size_t i=0;i<c.records.size();++i) {
            const auto& f=c.records[i];auto& r=cache[i];
            r.coordinate=f.coordinate;r.level=f.level;r.area=f.area;
            r.position[0]=f.x;r.position[1]=f.y;r.position[2]=f.z;
            r.posed=f.flags&1;r.air=(f.flags&2)!=0;r.warping=(f.flags&4)!=0;r.warped=(f.flags&8)!=0;
        }
    }
    uint32_t size() const {return uint32_t(cache.size());}
    uint64_t coordinate(uint32_t i) const {return cache[i].coordinate;}
    bool where(uint32_t i,int16_t level,int16_t area,float out[3]) const {
        const auto& r=cache[i];float dx=0;
        if(!r.posed || !ghost_scene::translation(r.level,r.area,level,area,dx))return false;
        out[0]=r.position[0]+dx;out[1]=r.position[1];out[2]=r.position[2];return true;
    }
    const ghost_path::Recorded& at(uint32_t i) const {return cache[i];}
    bool record_at(uint64_t c,uint32_t& i) const {
        uint32_t lo=0,hi=size();
        while(lo<hi){const uint32_t m=lo+(hi-lo)/2;if(coordinate(m)<c)lo=m+1;else hi=m;}
        i=lo;return lo<size() && coordinate(lo)==c;
    }
};
// The replaced rule (round b): the record nearest live Mario within 3 s back
// and 6 s ahead of the last match, equal distances to the nearer index.
static uint32_t nearest_record(const FixTrack& t,uint32_t from,const float live[3],int16_t level,int16_t area) {
    const uint64_t at=t.coordinate(from);uint32_t first=from,last=from;
    while(first>0 && t.coordinate(first-1)+90>=at)--first;
    while(last+1<t.size() && t.coordinate(last+1)<=at+180)++last;
    uint32_t best=from;float closest=INFINITY;
    for(uint32_t i=first;i<=last;++i) {
        float q[3];if(!t.where(i,level,area,q))continue;
        const float d=(q[0]-live[0])*(q[0]-live[0])+(q[1]-live[1])*(q[1]-live[1])+(q[2]-live[2])*(q[2]-live[2]);
        const auto apart=[from](uint32_t k){return k>from?k-from:from-k;};
        if(d<closest || (d==closest && apart(i)<apart(best))) {closest=d;best=i;}
    }
    return best;
}
// Two passes of one place in view: drawn points within 100 units of each
// other whose recorded times are 1.5 s or more apart.
// Where live Mario really is on the path: the monotone alignment of his run
// onto the path's (dynamic time warping), the path advancing 0 to 4 records a
// frame. Built apart from follow, as the oracle its measures are taken against.
static std::vector<uint32_t> aligned(const FixTrack& path,const FixTrack& live) {
    const uint32_t n=path.size(),m=live.size();
    std::vector<uint8_t> back(size_t(n)*m);std::vector<double> D(n,1e18),next(n);
    for(uint32_t j=0;j<m;++j) {
        const auto& r=live.at(j);
        for(uint32_t i=0;i<n;++i) {
            float cost=0;
            if(r.posed) {
                float q[3];
                if(path.where(i,r.level,r.area,q)) {
                    const float x=q[0]-r.position[0],y=(q[1]-r.position[1])*0.5f,z=q[2]-r.position[2];
                    cost=std::sqrt(x*x+y*y+z*z);
                } else cost=5000;
            }
            double best=j?1e18:(i?1e18:0.0);uint8_t step=0;
            for(uint8_t k=0;j && k<5 && k<=i;++k)if(D[i-k]<best) {best=D[i-k];step=k;}
            next[i]=best+cost;back[size_t(j)*n+i]=step;
        }
        D.swap(next);
    }
    uint32_t i=uint32_t(std::min_element(D.begin(),D.end())-D.begin());
    std::vector<uint32_t> out(m);
    for(uint32_t j=m;j-->0;) {out[j]=i;i-=back[size_t(j)*n+i];}
    return out;
}
struct Measure {
    unsigned frames=0,on_path=0,later=0,lag=0,jumps=0,two_pass=0;bool monotonic=true;
    double max_step=0,max_lead=-1e9,min_lead=1e9;uint64_t last=0;
};
// Two passes of one place in view: drawn points within 100 units of each
// other, 1.5 s or more apart, the line having left 200 units between them.
static bool passes_in(const ghost_path::Frame& f) {
    auto apart=[&](uint32_t i,uint32_t j){float d[3];for(unsigned r=0;r<3;++r)d[r]=f.points[i].position[r]-f.points[j].position[r];
        return std::sqrt(ghost_path::dot3(d,d));};
    for(uint32_t i=0;i<f.count;++i) {
        if(!(f.points[i].alpha>0))continue;
        bool left=false;
        for(uint32_t j=i+1;j<f.count;++j) {
            if(!(f.points[j].alpha>0))continue;
            const float d=apart(i,j);left=left || d>200;
            if(left && d<100 && f.points[j].coordinate>=f.points[i].coordinate+45)return true;
        }
    }
    return false;
}
static std::string revisits(const char* file) {
    std::ifstream in(file,std::ios::binary);std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(in)),{});
    check(bytes.size()>12 && !std::memcmp(bytes.data(),"GPATHRV1",8),"the revisit fixture");
    size_t at=8;auto take=[&](void* out,size_t n){check(at+n<=bytes.size(),"fixture length");std::memcpy(out,&bytes[at],n);at+=n;};
    uint32_t clips=0;take(&clips,4);std::vector<FixClip> all(clips);
    for(auto& c:all) {
        uint16_t length=0;take(&length,2);c.name.resize(length);take(c.name.data(),length);
        uint32_t count=0;take(&c.attempt,4);take(&count,4);c.records.resize(count);take(c.records.data(),count*sizeof(FixRecord));
    }
    std::string json="[";
    static ghost_path::Frame frame;
    for(size_t k=0;k+1<all.size();k+=2) {
        const FixTrack path(all[k]),live(all[k+1]);
        const auto oracle=aligned(path,live);
        ghost_path::Progress p{};Measure now,old;uint32_t anchor=0;bool anchored=false;
        for(uint32_t i=0;i<live.size();++i) {
            const auto& r=live.at(i);
            if(!r.posed)continue;
            uint32_t hint=0;const bool hinted=path.record_at(r.coordinate,hint);
            const uint32_t before=p.record;const double was=p.shown;const bool known=p.known;
            counting=true;
            if(ghost_path::follow(path,r.position,r.level,r.area,p,hinted,hint))++now.on_path;
            ghost_path::glide(path,p);
            if(p.known)ghost_path::window(path,p,r.level,r.area,1500,frame);
            counting=false;
            if(!p.known)continue;
            // The oracle's place for this frame, and whether he is on the route there.
            const uint32_t truth=oracle[i];
            float q[3];bool on_route=false;
            if(path.where(truth,r.level,r.area,q)) {
                const float x=q[0]-r.position[0],y=(q[1]-r.position[1])*0.5f,z=q[2]-r.position[2];
                on_route=x*x+y*y+z*z<=GP_PATH_CAPTURE*GP_PATH_CAPTURE;
            }
            ++now.frames;
            if(known && (p.record<before || p.shown<was))now.monotonic=false;
            if(known)now.max_step=std::fmax(now.max_step,p.shown-was);
            if(known && path.coordinate(p.record)>path.coordinate(before)+90)++now.jumps;
            const double lead=(double(path.coordinate(p.record))-double(path.coordinate(truth)))/30.0;
            now.max_lead=std::fmax(now.max_lead,lead);now.min_lead=std::fmin(now.min_lead,lead);
            if(lead>5)++now.later;
            if(lead<-5 && on_route)++now.lag;
            if(passes_in(frame))++now.two_pass;
            now.last=path.coordinate(p.record);
            // The replaced rule on the same frame, its window as long as it was
            // (5 s ahead in all).
            const uint32_t from=anchored?anchor:(hinted?hint:0);
            const uint32_t next=nearest_record(path,from,r.position,r.level,r.area);
            if(anchored) {
                if(next<anchor)old.monotonic=false;
                const uint64_t x=path.coordinate(anchor),y=path.coordinate(next);
                if((y>x?y-x:x-y)>90)++old.jumps;
            }
            anchor=next;anchored=true;++old.frames;
            const double old_lead=(double(path.coordinate(anchor))-double(path.coordinate(truth)))/30.0;
            old.max_lead=std::fmax(old.max_lead,old_lead);if(old_lead>5)++old.later;
            ghost_path::Progress fixed{};fixed.known=true;fixed.record=anchor;fixed.shown=double(path.coordinate(anchor));
            ghost_path::window(path,fixed,r.level,r.area,4000,frame);
            if(passes_in(frame))++old.two_pass;
        }
        check(now.frames>0,"the replay reached the path");
        char row[700];
        std::snprintf(row,sizeof(row),"%s{\"name\":\"%s\",\"path\":%u,\"live\":%u,\"frames\":%u,\"on_path\":%u,"
            "\"monotonic\":%s,\"max_step\":%.2f,\"jumps\":%u,\"max_lead_s\":%.2f,\"min_lead_s\":%.2f,\"later\":%u,"
            "\"lag\":%u,\"two_pass\":%u,\"end\":%llu,\"path_end\":%llu,\"old\":{\"monotonic\":%s,\"jumps\":%u,"
            "\"max_lead_s\":%.2f,\"later\":%u,\"two_pass\":%u}}",k?",":"",all[k].name.c_str(),all[k].attempt,
            all[k+1].attempt,now.frames,now.on_path,now.monotonic?"true":"false",now.max_step,now.jumps,now.max_lead,
            now.min_lead,now.later,now.lag,now.two_pass,(unsigned long long)now.last,
            (unsigned long long)path.coordinate(path.size()-1),old.monotonic?"true":"false",old.jumps,old.max_lead,
            old.later,old.two_pass);
        json+=row;
    }
    return json+"]";
}

int main(int argc,char** argv) {
    try {
        requests();records();averaging();progressing();windows();meshes();
        const std::string replays=argc>1?revisits(argv[1]):"[]";
        std::printf("{\"ok\":true,\"checks\":%u,\"allocations\":%u,\"samples\":%u,\"max_vertices\":%u,"
            "\"frame_bytes\":%u,\"mesh_bytes\":%u,\"revisits\":%s}\n",checks,allocations.load(),ghost_path::samples,
            ghost_path::max_vertices,unsigned(sizeof(ghost_path::Frame)),unsigned(sizeof(ghost_path::Mesh)),replays.c_str());
        return 0;
    } catch(const std::exception& e) {
        std::fprintf(stderr,"FAILED after %u checks: %s\n",checks,e.what());return 1;
    }
}
