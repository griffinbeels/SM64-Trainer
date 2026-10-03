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

static void anchoring() {
    // A loop: out along +x for 60 frames, back along the same line (60..119),
    // so every early point has a later twin at the same place.
    Run run(240);
    for(uint32_t i=0;i<240;++i) {
        const float x=i<60?float(i)*10:i<120?float(119-i)*10:float(i-120)*10+2000;
        run.samples[i].pose.position[0]=x;
    }
    Track track(run);ghost_path::Anchor anchor{};
    float live[3]={100,100,0};
    check(ghost_path::locate(track,live,11,1,anchor,true,5) && anchor.record==10,"a new run starts from the ghost's own record");
    live[0]=300;check(ghost_path::locate(track,live,11,1,anchor,false,0) && anchor.record==30,"forward from the last match");
    // At x=300 again on the way back: the window ahead (30..210) holds the twin at 89.
    live[0]=300.4f;anchor.record=85;
    check(ghost_path::locate(track,live,11,1,anchor,false,0) && anchor.record==89,"a path crossing itself keeps its own pass");
    // Live Mario stands at record 200, 190 records (past 6 s) ahead of the last match.
    live[0]=2000+(200-120)*10;anchor.record=10;
    check(ghost_path::locate(track,live,11,1,anchor,false,0) && anchor.record==10+GP_PATH_SEARCH_AHEAD_FRAMES,
          "never further than 6 s ahead of the last match");
    anchor.record=200;live[0]=100;
    ghost_path::locate(track,live,11,1,anchor,false,0);
    check(anchor.record>=200-GP_PATH_SEARCH_BACK_FRAMES,"never further than 3 s back");
    ghost_path::Anchor fresh{};live[0]=2010;
    check(ghost_path::locate(track,live,11,1,fresh,false,0) && fresh.record==121,"no hint: the whole clip");
    // The ghost went through a door: live Mario follows into area 2, past the window.
    for(uint32_t i=200;i<240;++i)run.samples[i].area=2;
    Track doors(run);ghost_path::Anchor through{};through.known=true;through.record=10;through.level=11;through.area=1;
    live[0]=2000+(230-120)*10;
    check(ghost_path::locate(doors,live,11,2,through,false,0) && through.record==230,"a new scene the window lacks: the whole clip");
    through.record=10;through.area=1;live[0]=9999;
    check(!ghost_path::locate(doors,live,11,3,through,false,0) && through.record==10 && through.area==3,
          "a scene the ghost never visited keeps the place");
    check(!ghost_path::locate(doors,live,11,3,through,false,0) && through.record==10,"and searches the whole clip once only");
    float nowhere[3]={NAN,0,0};check(!ghost_path::locate(doors,nowhere,11,1,through,false,0),"an unknown live Mario keeps the place");
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
    ghost_path::Anchor anchor{};anchor.known=true;anchor.record=40;anchor.level=11;anchor.area=1;
    auto& f=frame_storage;
    counting=true;ghost_path::window(gapped,anchor,11,1,3000,f);counting=false;
    // Area 2 records are not drawn while live Mario is in area 1.
    check(f.count==90-10,"1 s behind through the last record of his scene");
    check(f.points[0].coordinate==10 && near(f.points[0].alpha,GP_PATH_BEHIND_ALPHA),"behind him at 25%");
    check(near(f.points[30].alpha,1),"from his place on, full strength");
    check(!(f.points[0].flags&ghost_path::joined) && (f.points[1].flags&ghost_path::joined),"the window starts its own line");
    check(!(f.points[65].flags&ghost_path::joined),"the executed warp breaks the line");
    check((f.points[64].flags&ghost_path::joined),"a pending warp alone does not");
    check(f.points[40].pressed==0x8000,"a press edge: A, and START marks nothing");
    check(f.points[41].pressed==0,"a held button is no new press");
    check(f.points[43].pressed==0xe000,"same-frame presses together");
    check(f.points[47].pressed==0,"after unknown pads nothing is guessed");
    check(near(f.points[20].speed,10),"speed: horizontal units per frame");
    // The window's strength: full to 3 s, fading to nothing by 5 s.
    check(near(ghost_path::strength(90,3000),1) && near(ghost_path::strength(120,3000),.5f) && ghost_path::strength(151,3000)==0,
          "3 s full, then 2 s of fade");
    check(near(ghost_path::strength(-30,3000),GP_PATH_BEHIND_ALPHA) && ghost_path::strength(-31,3000)==0,"1 s behind");
    anchor.area=2;anchor.record=95;
    ghost_path::window(gapped,anchor,11,2,3000,f);
    unsigned gap_at=0;for(uint32_t i=0;i<f.count;++i)if(f.points[i].coordinate==101)gap_at=i;
    check(gap_at && !(f.points[gap_at].flags&ghost_path::joined) && (f.points[gap_at-1].flags&ghost_path::joined),
          "across a clip gap the line breaks");
    check(f.points[0].coordinate==90 && !(f.points[0].flags&ghost_path::joined),"the area change breaks it: never across the level");
    // The longest window fits its storage.
    Run long_run(2000);Track long_track(long_run);
    ghost_path::Anchor middle{};middle.known=true;middle.record=1000;middle.level=11;middle.area=1;
    ghost_path::window(long_track,middle,11,1,GP_PATH_AHEAD_MS_MAX,f);
    check(f.count==(GP_PATH_BEHIND_MS+GP_PATH_AHEAD_MS_MAX+GP_PATH_FADE_MS)*30/1000+1 && f.count<=ghost_path::samples,
          "10 s ahead, its fade and 1 s behind fit GP_PATH_SAMPLES");
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
    const float tall=mesh.vertices[mesh.first[icons]+2].offset[1]-mesh.vertices[mesh.first[icons]].offset[1];
    check(near(tall,GP_PATH_BUTTON_PIXELS/120.f,1e-5f),"12 pixels of a 240-line screen at any distance");
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

int main() {
    try {
        requests();records();averaging();anchoring();windows();meshes();
        std::printf("{\"ok\":true,\"checks\":%u,\"allocations\":%u,\"samples\":%u,\"max_vertices\":%u,"
            "\"frame_bytes\":%u,\"mesh_bytes\":%u}\n",checks,allocations.load(),ghost_path::samples,
            ghost_path::max_vertices,unsigned(sizeof(ghost_path::Frame)),unsigned(sizeof(ghost_path::Mesh)));
        return 0;
    } catch(const std::exception& e) {
        std::fprintf(stderr,"FAILED after %u checks: %s\n",checks,e.what());return 1;
    }
}
