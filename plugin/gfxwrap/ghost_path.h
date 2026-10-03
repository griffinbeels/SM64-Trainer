/* The ghost's path: one recorded trajectory drawn in the world ahead of live
 * Mario (his ask, 2026-10-03: "iRacing style path markers on the ground ...
 * if he's in the air, it matches his air position"). CPU only: no GL, heap,
 * threads or waits; every array is fixed. Playback picks the window from one
 * clip (Recorded, average, follow, glide, window); the compositor turns a Frame into
 * a Mesh (build) and draws it (ghost_path_gl.h). Tuning: ghost_tuning.h. */
#pragma once
#include "ghost_capture.h"
#include "ghost_annotation_assets.h"
#include "ghost_scene.h"
#include "ghost_tuning.h"
#include <cmath>
#include <cstdint>
#include <cstring>

namespace ghost_path {
constexpr uint32_t samples=GP_PATH_SAMPLES;
constexpr double frame_ms=1000.0/30.0;
// ACT_FLAG_AIR and ACT_FLAG_SWIMMING (n64decomp include/sm64.h): either one
// draws him in the air, at his own height; neither, on the floor.
constexpr uint32_t air_actions=0x800u|0x2000u;
// The buttons a press marks, in Usamune's held-button table order
// (ghost_annotation::button_masks) without START: A, B, Z, R and the C buttons.
// table: each one's row in that table, which is its icon's atlas cell.
constexpr uint16_t buttons[]={0x8000,0x4000,0x2000,0x10,0x8,0x4,0x2,0x1};
constexpr uint8_t table[]={0,1,3,4,5,6,7,8};
constexpr uint16_t marked=0x8000|0x4000|0x2000|0x10|0x8|0x4|0x2|0x1;

// The request's path fields (IPC valid_set and playback valid_request share
// this). The switches and the ghost index are checked on every request; the
// tuning ranges whenever the path is on, so a request that leaves the whole
// path block zero (a capture-only command) still means "no path". playing:
// the request plays clip_count ghosts; only then must path_clip name one.
template<class Request> bool valid_request(const Request& r,bool playing,uint32_t clip_count) {
    if(r.path_enabled>1 || r.path_buttons>1 || r.path_source>GP_PATH_AVERAGE || r.path_clip>=GP_GHOSTS
            || (playing && r.path_enabled && r.path_clip>=clip_count))return false;
    return !r.path_enabled || (r.path_ahead_ms>=GP_PATH_AHEAD_MS_MIN && r.path_ahead_ms<=GP_PATH_AHEAD_MS_MAX
        && r.path_width>=GP_PATH_WIDTH_MIN && r.path_width<=GP_PATH_WIDTH_MAX
        && r.path_opacity>=0 && r.path_opacity<=1
        && r.path_spacing_ms>=GP_PATH_SPACING_MS_MIN && r.path_spacing_ms<=GP_PATH_SPACING_MS_MAX);
}

inline bool finite3(const float* v) {return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);}
inline float length3(const float* v) {return std::sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);}
inline bool normalize3(float* v) {
    const float n=length3(v);
    if(!(n>1e-6f) || !std::isfinite(n))return false;
    for(unsigned i=0;i<3;++i)v[i]/=n;
    return true;
}
inline void cross3(const float* a,const float* b,float* out) {
    out[0]=a[1]*b[2]-a[2]*b[1];out[1]=a[2]*b[0]-a[0]*b[2];out[2]=a[0]*b[1]-a[1]*b[0];
}
inline float dot3(const float* a,const float* b) {return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}

// One record of a path clip: everything the window needs from it, in the
// clip's own (recorded) world coordinates.
struct Recorded {
    float position[3]{};      // Mario's root
    float floor[3]{},normal[3]{}; // the recorded shadow's centre and plane: the floor under him
    uint64_t coordinate=0;    // the clip's elapsed-frame coordinate
    int16_t level=0,area=0;
    uint16_t buttons=0;
    bool posed=false,air=false,shadowed=false,padded=false;
    bool warping=false;       // a warp is pending (pending_warp_op)
    bool warped=false;        // the clock saw a warp, retry or level load reach this record
};
// The recorded shadow in world space: its executed matrix with the RECORDED
// camera removed (inverse: ghost_pose::recorded_camera_inverse), over the nine
// vertices the game laid on the floor in a 3x3 grid (shadow.c), so the centre
// is vertex 4 and the floor's normal crosses the two diagonals.
inline bool shadow_plane(const gc_shadow& shadow,const float inverse[16],float floor[3],float normal[3]) {
    float world[16];
    for(unsigned c=0;c<4;++c)for(unsigned r=0;r<4;++r)
        world[c*4+r]=inverse[r]*shadow.matrix[c*4]+inverse[4+r]*shadow.matrix[c*4+1]
            +inverse[8+r]*shadow.matrix[c*4+2]+inverse[12+r]*shadow.matrix[c*4+3];
    float corner[GC_SHADOW_VERTICES][3];
    for(unsigned v=0;v<GC_SHADOW_VERTICES;++v) {
        const float x=shadow.position[v][0],y=shadow.position[v][1],z=shadow.position[v][2];
        for(unsigned r=0;r<3;++r)corner[v][r]=world[r]*x+world[4+r]*y+world[8+r]*z+world[12+r];
    }
    float a[3],b[3];
    for(unsigned r=0;r<3;++r) {a[r]=corner[8][r]-corner[0][r];b[r]=corner[6][r]-corner[2][r];}
    cross3(a,b,normal);
    if(!normalize3(normal) || !finite3(corner[4]))return false;
    if(normal[1]<0)for(unsigned r=0;r<3;++r)normal[r]=-normal[r];
    std::memcpy(floor,corner[4],sizeof(corner[4]));
    return true;
}
// recorded_inverse: the recorded camera's inverse when the record carries a
// shadow (null: no floor under him is known).
inline void record(const gc_sample& s,uint64_t coordinate,const float* recorded_inverse,Recorded& out) {
    out={};out.coordinate=coordinate;out.level=s.level;out.area=s.area;
    out.posed=(s.valid_fields&GC_VALID_POSE) && finite3(s.pose.position);
    if(out.posed)std::memcpy(out.position,s.pose.position,sizeof(out.position));
    const uint32_t action=(s.valid_fields&GC_VALID_ACTION)?s.live_action:out.posed?s.pose.action:0;
    out.air=(action&air_actions)!=0;
    out.warping=(s.valid_fields&GC_VALID_WARP) && s.pending_warp_op;
    const uint32_t t=s.clock.transition;
    out.warped=t==GCC_LEVEL || t==GCC_RETRY || t==GCC_AREA_WARP || t==GCC_TELEPORT;
    out.padded=(s.valid_fields&GC_VALID_PAD)!=0;out.buttons=out.padded?s.pad.buttons:0;
    if(out.posed && recorded_inverse && (s.valid_fields&GC_VALID_SHADOW))
        out.shadowed=shadow_plane(s.shadow,recorded_inverse,out.floor,out.normal);
}
// path_source average: the time-aligned mean of every playing ghost (others:
// each one's record at the fastest's elapsed frame, unposed when it has none),
// only where every one is within GP_PATH_AVERAGE_GATE of the fastest in its
// scene; the fastest's own point elsewhere. The floor under him moves across
// with the mean. Returns whether this record was averaged.
inline bool average(Recorded& fastest,const Recorded* others,uint32_t count) {
    if(!fastest.posed || !count)return false;
    float sum[3]={fastest.position[0],fastest.position[1],fastest.position[2]};
    for(uint32_t i=0;i<count;++i) {
        const auto& o=others[i];
        if(!o.posed || o.level!=fastest.level || o.area!=fastest.area)return false;
        float apart[3];for(unsigned r=0;r<3;++r)apart[r]=o.position[r]-fastest.position[r];
        if(!(dot3(apart,apart)<=GP_PATH_AVERAGE_GATE*GP_PATH_AVERAGE_GATE))return false;
        for(unsigned r=0;r<3;++r)sum[r]+=o.position[r];
    }
    const float n=float(count+1);
    float mean[3];for(unsigned r=0;r<3;++r)mean[r]=sum[r]/n;
    if(fastest.shadowed) {fastest.floor[0]+=mean[0]-fastest.position[0];fastest.floor[2]+=mean[2]-fastest.position[2];}
    std::memcpy(fastest.position,mean,sizeof(mean));
    return true;
}

// Live Mario's progress along the path: a record of the path clip that only
// moves forward, and where the drawn window is (gliding to that record).
struct Progress {
    bool known=false;uint32_t record=0;
    double shown=0;      // the window's anchor in the clip's frames; glides to record's coordinate
    uint32_t stalled=0;  // lists since the path ahead last came within the capture of live Mario
};
/* Track: the path clip as playback sees it.
 *   uint32_t size() const;                       its records
 *   uint64_t coordinate(uint32_t) const;         a record's elapsed-frame coordinate (non-decreasing)
 *   bool where(uint32_t,int16_t level,int16_t area,float out[3]) const;
 *                                                a posed record's root in that live scene, or false
 *   const Recorded& at(uint32_t);                the record as drawn (averaged when asked)
 * Whether live Mario is on the path at record i: within GP_PATH_CAPTURE of
 * it, height counting half (a jump a little higher or lower than the ghost's
 * is still its path). d: the weighted distance squared. */
template<class Track> bool on_path(const Track& track,uint32_t i,const float live[3],int16_t level,int16_t area,float& d) {
    float at[3];
    if(!track.where(i,level,area,at))return false;
    const float x=at[0]-live[0],y=(at[1]-live[1])*0.5f,z=at[2]-live[2];
    d=x*x+y*y+z*z;
    return d<=GP_PATH_CAPTURE*GP_PATH_CAPTURE;
}
// The first run of records on the path near live Mario, from record first to
// coordinate until: its nearest record. A later pass of the same place lies
// past records off his capture, so it is never this run.
template<class Track> bool first_run(const Track& track,uint32_t first,uint64_t until,const float live[3],
                                     int16_t level,int16_t area,uint32_t& best) {
    const uint32_t n=track.size();
    bool run=false;float closest=INFINITY;
    for(uint32_t i=first;i<n && track.coordinate(i)<=until;++i) {
        float d=0;
        if(on_path(track,i,live,level,area,d)) {run=true;if(d<closest) {closest=d;best=i;}}
        else if(run)break;
    }
    return run;
}
/* Moves progress forward to live Mario (GP_PATH_* in ghost_tuning.h). A new
 * progress starts at hint, the ghost's own record this list, else at the first
 * run of the clip near him (its first pass), searched again every
 * GP_PATH_STALL_FRAMES lists until he is on the path. Returns whether he is on
 * the path this list; off it the progress holds. */
template<class Track> bool follow(const Track& track,const float live[3],int16_t level,int16_t area,
                                  Progress& p,bool hinted,uint32_t hint) {
    const uint32_t n=track.size();
    if(!n || !finite3(live))return false;
    if(p.known && p.record>=n)p={};
    uint32_t best=0;
    if(!p.known) {
        bool found=hinted && hint<n;
        if(found)best=hint;
        else if(p.stalled++%GP_PATH_STALL_FRAMES==0)found=first_run(track,0,UINT64_MAX,live,level,area,best);
        if(!found)return false;
        p.known=true;p.record=best;p.shown=double(track.coordinate(best));p.stalled=0;
    }
    const uint64_t reach=p.stalled<GP_PATH_STALL_FRAMES?GP_PATH_REACH_FRAMES
        :GP_PATH_CATCHUP_FRAMES+uint64_t(p.stalled-GP_PATH_STALL_FRAMES)*GP_PATH_CATCHUP_GROWTH;
    if(first_run(track,p.record,track.coordinate(p.record)+reach,live,level,area,best)) {
        p.stalled=0;
        if(best>p.record)p.record=best;
        return true;
    }
    if(p.stalled<UINT32_MAX)++p.stalled;
    return false;
}
// Once a list, the drawn window moves toward the progress: GP_PATH_GLIDE of
// the way (at most GP_PATH_GLIDE_MAX frames, at least one), never back (a
// new progress has snapped).
template<class Track> void glide(const Track& track,Progress& p) {
    if(!p.known || p.record>=track.size())return;
    const double goal=double(track.coordinate(p.record));
    if(!(p.shown<goal)) {p.shown=goal;return;}
    const double gap=goal-p.shown;
    p.shown+=std::fmax(std::fmin(gap*GP_PATH_GLIDE,double(GP_PATH_GLIDE_MAX)),std::fmin(gap,1.0));
}

// One drawn point of the window, in the live scene's world coordinates.
enum Flag : uint8_t {
    air=1, shadowed=2, padded=4,
    joined=8 // one line with the point before it: no warp, scene change or gap between
};
struct Point {
    float position[3],floor[3],normal[3];
    uint64_t coordinate;
    float alpha;    // the window's strength here
    float speed;    // horizontal units per frame
    uint16_t pressed; // buttons whose press edge (off to on) is this frame
    uint8_t flags,reserved;
};
// What the compositor draws this list: the window and how to draw it.
struct Frame {
    uint32_t count=0;
    Point points[samples];
    float view_projection[16]{}; // the live camera's, as every ghost's pose
    uint32_t flip_y=1;
    float eye[3]{};              // the live camera
    float width=40,opacity=0.85f;
    uint32_t spacing_ms=100;
    bool buttons=false;          // path_buttons
    bool chevrons=false,icons=false; // the ROM's chevron and Usamune's icons are in the atlas
};
inline bool valid(const Frame& f) {
    if(f.count>samples || f.flip_y>1 || !(f.width>=GP_PATH_WIDTH_MIN && f.width<=GP_PATH_WIDTH_MAX)
            || !(f.opacity>=0 && f.opacity<=1) || f.spacing_ms<GP_PATH_SPACING_MS_MIN
            || f.spacing_ms>GP_PATH_SPACING_MS_MAX || !finite3(f.eye))return false;
    for(float v:f.view_projection)if(!std::isfinite(v))return false;
    for(uint32_t i=0;i<f.count;++i) {
        const auto& p=f.points[i];
        if(!finite3(p.position) || !finite3(p.floor) || !finite3(p.normal) || !(p.alpha>=0 && p.alpha<=1)
                || !(p.speed>=0) || !std::isfinite(p.speed) || (p.pressed&~marked))return false;
    }
    return true;
}
// The window's strength dt frames from the anchor: full for ahead_ms, then
// fading to nothing over GP_PATH_FADE_MS; GP_PATH_BEHIND_ALPHA behind him,
// fading out over the last GP_PATH_BEHIND_FADE_MS.
inline float strength(double dt_frames,uint32_t ahead_ms) {
    const double ms=dt_frames*frame_ms;
    if(ms<0) {
        const double left=ms+double(GP_PATH_BEHIND_MS);
        if(left<-1e-6)return 0.0f;
        return left>=double(GP_PATH_BEHIND_FADE_MS)?GP_PATH_BEHIND_ALPHA
            :float(GP_PATH_BEHIND_ALPHA*std::fmax(left,0.0)/double(GP_PATH_BEHIND_FADE_MS));
    }
    if(ms<=double(ahead_ms)+1e-6)return 1.0f;
    const double faded=1.0-(ms-double(ahead_ms))/double(GP_PATH_FADE_MS);
    return faded>0?float(faded):0.0f;
}
// The records the window holds around the anchor, joined where the clip shows
// one unbroken line: both posed, the same recorded scene, consecutive frames,
// no warp between (a pending warp that executed, or the clock's own warp,
// retry or level load). Never smoothed: every point is a recorded frame.
template<class Track> void window(Track& track,const Progress& progress,int16_t level,int16_t area,
                                  uint32_t ahead_ms,Frame& out) {
    out.count=0;
    const uint32_t n=track.size();
    if(!progress.known || progress.record>=n)return;
    const double at=progress.shown;
    const double behind=double(GP_PATH_BEHIND_MS)/frame_ms,ahead=double(ahead_ms+GP_PATH_FADE_MS)/frame_ms;
    uint32_t first=progress.record;
    while(first>0 && progress.record-first<samples/2 && double(track.coordinate(first-1))>=at-behind-1e-9)--first;
    Recorded previous{};bool have_previous=false;
    if(first>0) {previous=track.at(first-1);have_previous=true;}
    bool previous_drawable=false;
    float shift=0;
    if(have_previous)previous_drawable=previous.posed
        && ghost_scene::translation(previous.level,previous.area,level,area,shift);
    for(uint32_t i=first;i<n && out.count<samples;++i) {
        const Recorded& r=track.at(i);
        if(double(r.coordinate)>at+ahead+1e-9)break;
        float dx=0;
        const bool drawable=r.posed && ghost_scene::translation(r.level,r.area,level,area,dx);
        if(drawable) {
            auto& p=out.points[out.count++];
            std::memcpy(p.position,r.position,sizeof(p.position));p.position[0]+=dx;
            if(r.shadowed) {std::memcpy(p.floor,r.floor,sizeof(p.floor));p.floor[0]+=dx;
                std::memcpy(p.normal,r.normal,sizeof(p.normal));}
            else {std::memcpy(p.floor,p.position,sizeof(p.floor));p.normal[0]=p.normal[2]=0;p.normal[1]=1;}
            p.coordinate=r.coordinate;
            p.alpha=strength(double(r.coordinate)-at,ahead_ms);
            p.speed=0;p.pressed=0;p.reserved=0;
            p.flags=uint8_t((r.air?air:0)|(r.shadowed?shadowed:0)|(r.padded?padded:0));
            // Joined only to a point of this window (the record before the
            // window gives a press edge, never a segment).
            const bool consecutive=have_previous && r.coordinate>=previous.coordinate && r.coordinate-previous.coordinate<=1;
            if(previous_drawable && i>first && consecutive && previous.level==r.level && previous.area==r.area
                    && !(previous.warping && !r.warping) && !r.warped)p.flags|=joined;
            if(consecutive && previous.padded && r.padded)
                p.pressed=uint16_t(r.buttons&~previous.buttons&marked);
        }
        previous=r;have_previous=true;previous_drawable=drawable;
    }
    // Speed: horizontal displacement per frame to the next joined point (the
    // last of a line keeps the one before it).
    for(uint32_t i=0;i<out.count;++i) {
        auto& p=out.points[i];
        const Point* a=nullptr;const Point* b=nullptr;
        if(i+1<out.count && (out.points[i+1].flags&joined)) {a=&p;b=&out.points[i+1];}
        else if(i>0 && (p.flags&joined)) {a=&out.points[i-1];b=&p;}
        if(!a)continue;
        const float x=b->position[0]-a->position[0],z=b->position[2]-a->position[2];
        const uint64_t frames=b->coordinate>a->coordinate?b->coordinate-a->coordinate:1;
        p.speed=std::sqrt(x*x+z*z)/float(frames);
    }
}

// One absolute scale in every level (path research, 2026-10-03): 0 grey,
// 32 a full run, 48 the dive and long-jump cap, 64 and faster white, linear
// between, lightness rising the whole way.
inline void speed_color(float speed,float rgb[3]) {
    struct Stop {float speed;uint8_t r,g,b;};
    constexpr Stop stops[]={{0,0x6E,0x6E,0x6E},{32,0x38,0xD4,0x5E},{48,0x9B,0xF0,0xA8},{64,0xFF,0xFF,0xFF}};
    const float s=std::isfinite(speed) && speed>0?speed:0;
    for(unsigned i=1;i<4;++i)if(s<=stops[i].speed || i==3) {
        const auto& a=stops[i-1];const auto& b=stops[i];
        const float t=std::fmin(1.0f,(s-a.speed)/(b.speed-a.speed));
        rgb[0]=(a.r+(b.r-a.r)*t)/255.0f;rgb[1]=(a.g+(b.g-a.g)*t)/255.0f;rgb[2]=(a.b+(b.b-a.b)*t)/255.0f;
        return;
    }
}

// The mesh: world positions, plus a screen offset (normalized device units,
// x right and y up on the finished picture) that the vertex shader scales by
// the vertex's w, which keeps a button icon one size at any distance.
enum Mode : uint32_t {solid=0, chevron=1, icon=2, dot=3};
struct Vertex {float position[3],offset[2],uv[2],rgba[4],mode;};
static_assert(sizeof(Vertex)==48,"path vertex layout");
// Layers, drawn in this order; each but the icons covers a pixel once.
enum Layer : uint32_t {casing, ribbon, air_ribbon, marks, icons, layers};
constexpr uint32_t max_icons=64;
// Per point at most: casing 6 + ribbon 6 (ground) or ribbon 6 + dot 6
// (air), and a tick 6 at a takeoff or landing.
constexpr uint32_t max_vertices=samples*18+max_icons*6;
struct Mesh {
    Vertex vertices[max_vertices];
    uint32_t first[layers]{},count[layers]{},total=0;
    // Scratch: each point's direction along the line and across it on its floor.
    float along[samples][3],across[samples][3];
};
inline float near_fade(const Frame& f,const float p[3]) {
    float apart[3];for(unsigned r=0;r<3;++r)apart[r]=p[r]-f.eye[r];
    const float d=length3(apart);
    const float t=(d-GP_PATH_NEAR_HIDDEN)/(GP_PATH_NEAR_FULL-GP_PATH_NEAR_HIDDEN);
    return t<=0?0.0f:t>=1?1.0f:t;
}
struct Writer {
    Mesh& mesh;const Frame& frame;
    bool room(uint32_t n) const {return mesh.total+n<=max_vertices;}
    void vertex(const float p[3],float u,float v,const float rgb[3],float alpha,Mode mode,float ox=0,float oy=0,
                const float* fade_at=nullptr) {
        auto& out=mesh.vertices[mesh.total++];
        std::memcpy(out.position,p,sizeof(out.position));
        out.offset[0]=ox;out.offset[1]=oy;out.uv[0]=u;out.uv[1]=v;
        out.rgba[0]=rgb[0];out.rgba[1]=rgb[1];out.rgba[2]=rgb[2];
        out.rgba[3]=alpha*frame.opacity*near_fade(frame,fade_at?fade_at:p);
        out.mode=float(mode);
    }
    // Two triangles over a0 b0 a1 b1 (a then b across, 0 then 1 along).
    void quad(const float a0[3],const float b0[3],const float a1[3],const float b1[3],float v0,float v1,
              const float rgb0[3],const float rgb1[3],float alpha0,float alpha1,Mode mode) {
        if(!room(6))return;
        vertex(a0,0,v0,rgb0,alpha0,mode);vertex(b0,1,v0,rgb0,alpha0,mode);vertex(a1,0,v1,rgb1,alpha1,mode);
        vertex(a1,0,v1,rgb1,alpha1,mode);vertex(b0,1,v0,rgb0,alpha0,mode);vertex(b1,1,v1,rgb1,alpha1,mode);
    }
};
inline bool on_ground(const Point& p) {return !(p.flags&air);}
// The horizontal direction the line runs at point i, from the joined
// segments on either side; a point standing still keeps the last one known.
inline bool direction(const Frame& f,uint32_t i,float out[3]) {
    float in[3]{},next[3]{};bool has_in=false,has_out=false;
    const auto& p=f.points[i];
    if(p.flags&joined) {const auto& a=f.points[i-1];in[0]=p.position[0]-a.position[0];in[2]=p.position[2]-a.position[2];
        has_in=normalize3(in);}
    if(i+1<f.count && (f.points[i+1].flags&joined)) {const auto& b=f.points[i+1];
        next[0]=b.position[0]-p.position[0];next[2]=b.position[2]-p.position[2];has_out=normalize3(next);}
    if(has_in && has_out) {for(unsigned r=0;r<3;++r)out[r]=in[r]+next[r];if(normalize3(out))return true;
        std::memcpy(out,next,sizeof(next));return true;}
    if(has_out) {std::memcpy(out,next,sizeof(next));return true;}
    if(has_in) {std::memcpy(out,in,sizeof(in));return true;}
    return false;
}
// The chevron phase at a coordinate: one 8-row cell per spacing_ms of recorded
// time, relative to the window's first point (exact in double, then small).
inline float phase(const Frame& f,uint64_t coordinate) {
    const double cells_per_frame=frame_ms/double(f.spacing_ms);
    const double base=double(f.points[0].coordinate)*cells_per_frame;
    return float(base-std::floor(base)+double(int64_t(coordinate-f.points[0].coordinate))*cells_per_frame);
}
/* Ground ribbon: a flat strip path width wide on the floor at his root, lifted
 * GP_PATH_LIFT along the floor's normal and tilted to its recorded shadow plane,
 * carrying the chevrons (v: phase) tinted by speed, over a dark casing.
 * Air (or swimming): a thin strip facing the camera at his root, a faint dot
 * on the floor under him every GP_PATH_DOT_FRAMES frames, and a bar across the
 * line where he leaves the ground and where he lands. Button presses
 * (frame.buttons): Usamune's icons above his root, side by side across the
 * line's direction on screen, one size on screen. */
inline void build(const Frame& f,Mesh& mesh) {
    mesh.total=0;
    for(uint32_t l=0;l<layers;++l)mesh.first[l]=mesh.count[l]=0;
    if(!f.count)return;
    Writer w{mesh,f};
    const float half=f.width*0.5f;
    float last[3]={0,0,1};
    for(uint32_t i=0;i<f.count;++i) {
        const auto& p=f.points[i];
        float along[3]{};
        if(direction(f,i,along))std::memcpy(last,along,sizeof(last));
        else std::memcpy(along,last,sizeof(along));
        // Across the line, then onto the floor's plane.
        float across[3]={-along[2],0,along[0]};
        const float lean=dot3(p.normal,across);
        for(unsigned r=0;r<3;++r)across[r]-=p.normal[r]*lean;
        if(!normalize3(across)) {across[0]=-along[2];across[1]=0;across[2]=along[0];}
        std::memcpy(mesh.along[i],along,sizeof(along));std::memcpy(mesh.across[i],across,sizeof(across));
    }
    float color[samples][3];
    for(uint32_t i=0;i<f.count;++i)speed_color(f.points[i].speed,color[i]);
    const float casing_rgb[3]={0x1E/255.0f,0x1E/255.0f,0x1E/255.0f},white[3]={1,1,1};
    auto edge=[&](uint32_t i,float scale,float side,float out[3]) {
        const auto& p=f.points[i];
        for(unsigned r=0;r<3;++r)out[r]=p.position[r]+p.normal[r]*GP_PATH_LIFT+mesh.across[i][r]*half*scale*side;
    };
    auto ground_pair=[&](uint32_t i) {
        return i>0 && (f.points[i].flags&joined) && on_ground(f.points[i]) && on_ground(f.points[i-1]);
    };
    // Casing, then the ribbon over it.
    for(uint32_t layer=casing;layer<=ribbon;++layer) {
        mesh.first[layer]=mesh.total;
        const float scale=layer==casing?GP_PATH_CASING_WIDTH:1.0f;
        for(uint32_t i=1;i<f.count;++i) {
            if(!ground_pair(i))continue;
            float a0[3],b0[3],a1[3],b1[3];
            edge(i-1,scale,-1,a0);edge(i-1,scale,1,b0);edge(i,scale,-1,a1);edge(i,scale,1,b1);
            const auto& p0=f.points[i-1];const auto& p1=f.points[i];
            if(layer==casing)
                w.quad(a0,b0,a1,b1,0,0,casing_rgb,casing_rgb,p0.alpha*GP_PATH_CASING_ALPHA,p1.alpha*GP_PATH_CASING_ALPHA,solid);
            else w.quad(a0,b0,a1,b1,phase(f,p0.coordinate),phase(f,p1.coordinate),color[i-1],color[i],
                        p0.alpha,p1.alpha,f.chevrons?chevron:solid);
        }
        mesh.count[layer]=mesh.total-mesh.first[layer];
    }
    // The air ribbon: every joined segment with an airborne end, facing the camera.
    mesh.first[air_ribbon]=mesh.total;
    const float thin=f.width*GP_PATH_AIR_WIDTH*0.5f;
    for(uint32_t i=1;i<f.count;++i) {
        const auto& p0=f.points[i-1];const auto& p1=f.points[i];
        if(!(p1.flags&joined) || (on_ground(p0) && on_ground(p1)))continue;
        float t[3];for(unsigned r=0;r<3;++r)t[r]=p1.position[r]-p0.position[r];
        if(!normalize3(t))continue;
        float side[2][3];bool ok=true;
        for(unsigned k=0;k<2;++k) {
            const auto& p=k?p1:p0;float view[3];
            for(unsigned r=0;r<3;++r)view[r]=f.eye[r]-p.position[r];
            cross3(t,view,side[k]);ok=ok && normalize3(side[k]);
        }
        if(!ok)continue;
        float a0[3],b0[3],a1[3],b1[3];
        for(unsigned r=0;r<3;++r) {
            a0[r]=p0.position[r]-side[0][r]*thin;b0[r]=p0.position[r]+side[0][r]*thin;
            a1[r]=p1.position[r]-side[1][r]*thin;b1[r]=p1.position[r]+side[1][r]*thin;
        }
        w.quad(a0,b0,a1,b1,0,0,color[i-1],color[i],p0.alpha,p1.alpha,solid);
    }
    mesh.count[air_ribbon]=mesh.total-mesh.first[air_ribbon];
    // Marks: the floor dots under the air ribbon, and the takeoff and landing bars.
    mesh.first[marks]=mesh.total;
    for(uint32_t i=0;i<f.count;++i) {
        const auto& p=f.points[i];
        const bool flying=!on_ground(p);
        if(flying && (p.flags&shadowed) && p.coordinate%GP_PATH_DOT_FRAMES==0) {
            float a[3]={1,0,0},b[3];
            const float lean=dot3(p.normal,a);for(unsigned r=0;r<3;++r)a[r]-=p.normal[r]*lean;
            if(!normalize3(a)) {a[0]=0;a[1]=0;a[2]=1;}
            cross3(p.normal,a,b);
            const float r0=f.width*GP_PATH_DOT_WIDTH*0.5f;
            float c[3],q[4][3];
            for(unsigned r=0;r<3;++r)c[r]=p.floor[r]+p.normal[r]*GP_PATH_LIFT;
            for(unsigned k=0;k<4;++k)for(unsigned r=0;r<3;++r)
                q[k][r]=c[r]+a[r]*r0*((k&1)?1.0f:-1.0f)+b[r]*r0*((k&2)?1.0f:-1.0f);
            if(w.room(6)) {
                const float alpha=p.alpha*GP_PATH_DOT_ALPHA;
                w.vertex(q[0],0,0,color[i],alpha,dot);w.vertex(q[1],1,0,color[i],alpha,dot);w.vertex(q[2],0,1,color[i],alpha,dot);
                w.vertex(q[2],0,1,color[i],alpha,dot);w.vertex(q[1],1,0,color[i],alpha,dot);w.vertex(q[3],1,1,color[i],alpha,dot);
            }
        }
        // Takeoff: the last ground point before the air; landing: the first after it.
        const bool takeoff=!flying && i+1<f.count && (f.points[i+1].flags&joined) && !on_ground(f.points[i+1]);
        const bool landing=!flying && i>0 && (p.flags&joined) && !on_ground(f.points[i-1]);
        if(takeoff || landing) {
            float a0[3],b0[3],a1[3],b1[3];
            for(unsigned r=0;r<3;++r) {
                const float centre=p.position[r]+p.normal[r]*(GP_PATH_LIFT+0.5f);
                const float x=mesh.across[i][r]*half,y=mesh.along[i][r]*GP_PATH_TICK_LENGTH*0.5f;
                a0[r]=centre-x-y;b0[r]=centre+x-y;a1[r]=centre-x+y;b1[r]=centre+x+y;
            }
            w.quad(a0,b0,a1,b1,0,0,white,white,p.alpha,p.alpha,solid);
        }
    }
    mesh.count[marks]=mesh.total-mesh.first[marks];
    // Button presses: an icon per pressed button, in table order, side by
    // side across the line's direction on the finished picture. Each is
    // GP_PATH_BUTTON_SIZE world units tall, shrinking with distance like the
    // names, and never taller than GP_PATH_BUTTON_MAX_SCREEN pixels of 240.
    // Picture units: 1/120 of the picture's height, the same length across.
    mesh.first[icons]=mesh.total;
    uint32_t drawn=0;
    const float* m=f.view_projection;
    const float px=std::sqrt(m[0]*m[0]+m[4]*m[4]+m[8]*m[8]),py=std::sqrt(m[1]*m[1]+m[5]*m[5]+m[9]*m[9]);
    auto project=[&](const float q[3],float out[3])->bool {
        float c[4];for(unsigned r=0;r<4;++r)c[r]=m[r]*q[0]+m[4+r]*q[1]+m[8+r]*q[2]+m[12+r];
        if(!(c[3]>1e-3f))return false;
        out[0]=c[0]/c[3];out[1]=c[1]/c[3];out[2]=c[3];return true;
    };
    for(uint32_t i=0;f.buttons && f.icons && px>0 && py>0 && i<f.count;++i) {
        const auto& p=f.points[i];
        if(!p.pressed || !(p.alpha>0))continue;
        float anchor[3]={p.position[0],p.position[1]+GP_PATH_BUTTON_HEIGHT,p.position[2]};
        float here[3],there[3],row[2]={1,0};
        float ahead[3];for(unsigned r=0;r<3;++r)ahead[r]=p.position[r]+mesh.along[i][r]*50.0f;
        ahead[1]+=GP_PATH_BUTTON_HEIGHT;
        if(!project(anchor,here))continue;
        if(project(ahead,there)) {
            // A quarter turn from the line's direction: across it.
            const float dx=(there[0]-here[0])*120.0f*py/px,dy=(there[1]-here[1])*120.0f;
            const float n=std::sqrt(dx*dx+dy*dy);
            if(n>1e-3f) {row[0]=-dy/n;row[1]=dx/n;}
            if(row[0]<0 || (row[0]==0 && row[1]<0)) {row[0]=-row[0];row[1]=-row[1];}
        }
        const float height=std::fmin(GP_PATH_BUTTON_SIZE*py/here[2]*120.0f,GP_PATH_BUTTON_MAX_SCREEN);
        unsigned pressed=0;for(auto mask:buttons)pressed+=(p.pressed&mask)!=0;
        const float pitch=height*13.0f/12.0f,side=height*0.5f;
        unsigned k=0;
        for(unsigned b=0;b<8;++b) {
            if(!(p.pressed&buttons[b]))continue;
            if(drawn==max_icons || !w.room(6))break;
            const float shift=(float(k++)-float(pressed-1)*0.5f)*pitch;
            const float cx=row[0]*shift,cy=row[1]*shift;
            // The icon's atlas cell (ghost_annotation_assets), row 0 at the top.
            using namespace ghost_annotation;
            const float u0=float(table[b]*16)/atlas_width,u1=float(table[b]*16+16)/atlas_width;
            const float v_top=cell::button_y/atlas_height,v_bottom=(cell::button_y+16)/atlas_height;
            const float corner[4][4]={{-side,-side,u0,v_bottom},{side,-side,u1,v_bottom},{-side,side,u0,v_top},{side,side,u1,v_top}};
            constexpr unsigned order[6]={0,1,2,2,1,3};
            for(unsigned o:order) {
                const auto& c=corner[o];
                w.vertex(anchor,c[2],c[3],white,p.alpha,icon,(cx+c[0])/120.0f*px/py,(cy+c[1])/120.0f);
            }
            ++drawn;
        }
    }
    mesh.count[icons]=mesh.total-mesh.first[icons];
}
}
