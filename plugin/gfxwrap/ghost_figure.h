/* One recorded row drawn the way a saved ghost is drawn in the game: its
 * body and held object under the live camera, its recorded shadow and
 * particles, tint and fades (draw), and the name and inputs riding on it
 * (annotate). Saved playback (ghost_playback.cpp) and the scene viewer's
 * ghosts (ghost_viewer.cpp) both draw through these, so a ghost looks and is
 * labeled the same wherever it appears. No state, no GL: callers own the
 * storage a pose borrows and pass their settings. */
#pragma once
#include "ghost_clip.h"
#include "ghost_tuning.h"
#include "ghost_pose.h"
#include "ghost_scene.h"
#include "ghost_occlusion.h"
#include "ghost_proximity.h"
#include "ghost_particle_pose.h"
#include "ghost_annotation.h"
#include <cmath>
#include <cstring>

namespace ghost_figure {
// A row's coordinate: game frames since the clip's origin, plus the origin's
// raw IGT on a star clock. prepare_clip proved the bounds for every row.
inline uint64_t coordinate(const gclip_header &h, const gclip_frame *frames, uint32_t index) {
    return frames[index].sample.clock.elapsed_frames-h.origin_elapsed
        +(h.clock_kind==GCLIP_STAR_CLOCK?h.origin_raw:0);
}
// A refused row at most GP_BRIDGE_FRAMES inside one unbroken run of proven
// poses, in one scene: the proven row before it, which the ghost shows
// instead. Null: the ghost hides on this row (where it blinks). count is the
// clip's indexed rows.
inline const gclip_frame *bridged(const gclip_header &h, const gclip_frame *frames, uint32_t count, uint32_t at) {
    const auto posed=[&](uint32_t i){return (frames[i].sample.valid_fields&GC_VALID_POSE)!=0;};
    uint32_t before=at,after=at;
    while(before>0 && !posed(before-1) && at-before<GP_BRIDGE_FRAMES)--before;
    if(before==0 || !posed(before-1))return nullptr;
    --before;
    while(after+1<count && !posed(after+1) && after-before<GP_BRIDGE_FRAMES)++after;
    if(after+1>=count || !posed(after+1))return nullptr;
    ++after;
    if(after-before-1>GP_BRIDGE_FRAMES)return nullptr;
    const auto &first=frames[before].sample,&last=frames[after].sample;
    if(last.sequence-first.sequence!=after-before
            || coordinate(h,frames,after)-coordinate(h,frames,before)!=after-before)return nullptr;
    for(uint32_t i=before;i<=after;++i)
        if(frames[i].sample.level!=first.level || frames[i].sample.area!=first.area)return nullptr;
    return &frames[before];
}
// What the ghost settings ask of every drawn ghost (gp_request's fields).
struct Look {
    float opacity=1;            // before this ghost's own fade
    float hue_degrees=0;        // 0 keeps the model's colours (tint off)
    bool proximity=false;       // the overlap fade near live Mario
    float proximity_distance=0;
    bool occlusion_fade=false;  // player first: fade what hides live Mario
    bool shadow=false;          // the recorded shadow
};
// Caller-owned storage the drawn pose borrows until its next draw.
struct Storage {
    ghost_pose::Scratch &scratch;
    ghost_compositor::ShadowVertex *shadow_vertices; // 24
    uint8_t *shadow_texture;                         // 256
    ghost_compositor::ParticleVertex *particles;     // ghost_compositor::particle_vertices
};
enum class Drawn { ready, wrong_scene, refused };
// The recorded (or defeated) sample with every world-space part mapped into
// the live area's coordinates. rom_layout is the clip's capture layout,
// whichever cartridge runs now; position receives the rebased root (the
// picture's and the sound's) once the scene is compatible.
inline Drawn draw(sg_model *model, uint32_t rom_layout, const gs_record &recorded, const gc_sample &particles_from,
                  const gc_sample &live, const ghost::WorldPass &world, const Look &look, float fade,
                  Storage &storage, float position[3], ghost_compositor::Pose &out) {
    gs_record mapped=recorded;
    if(!ghost_scene::rebase(mapped.sample,live))return Drawn::wrong_scene;
    std::memcpy(position,mapped.sample.pose.position,3*sizeof(float));
    const float opacity=look.opacity*fade;
    // The overlap fade rides with the player-first region, not the opacity:
    // the shaders give the two ONE floor (ghost_occlusion.h).
    const float overlap=look.proximity && (live.valid_fields&GC_VALID_POSE)
        ? ghost_proximity::factor(live.pose.position,live.pose.scale,
              mapped.sample.pose.position,mapped.sample.pose.scale,look.proximity_distance)
        : 1.0f;
    if(ghost_pose::evaluate(model,mapped,live,world,opacity,storage.scratch,out,rom_layout)!=ghost_pose::Result::ok) {
        out={};return Drawn::refused;
    }
    out.hue_degrees=look.hue_degrees;
    // Player first: the same live Mario region for every ghost this frame,
    // under the live view-projection its body and shadow are drawn with.
    ghost_occlusion::region(out.view_projection,look.occlusion_fade,
        (live.valid_fields&GC_VALID_POSE)!=0,live.pose.position,live.pose.scale,out.occlusion);
    out.occlusion.overlap=overlap;
    if(look.shadow && (mapped.sample.valid_fields&GC_VALID_SHADOW)
            && !ghost_pose::shadow(model,mapped.sample,out.view_projection,storage.shadow_vertices,
                                   storage.shadow_texture,out.shadow)) {
        out={};return Drawn::refused;
    }
    // Recorded particles never refuse their ghost; unknown frames are skipped.
    sg_particle_view particles{};
    if((particles_from.valid_fields&GC_VALID_PARTICLES) && sg_model_get_particles(model,&particles)==SG_OK) {
        gc_sample mapped_particles=particles_from;
        if(ghost_scene::rebase_components(mapped_particles,recorded.sample,live))
            ghost_particle_pose::place(model,particles,mapped_particles,live.camera,out.view_projection,
                                       storage.particles,out.particles);
    }
    return Drawn::ready;
}
// What the ghost settings ask of its name and inputs (gp_request's fields).
struct Names {
    bool names=false, inputs=false, hud=false, values=false;
    uint8_t background=0;           // gp_name_background
    float background_opacity=0;     // 0 draws no box
};
// The name and inputs that ride on a drawn ghost. scale: the shown row's
// vertical scale; slot: its place in the set (the corner stack's order);
// recorded: the row is the recording's own, not a held end, dance or fall,
// so its inputs show; selected: the row whose pad is current (a refused pose
// can borrow older geometry, never a button hold); artwork and font: the
// prepared atlas's Usamune art and dialogue font.
inline void annotate(const char (&label)[GP_LABEL_BYTES], const float position[3], float scale, uint8_t slot,
                     const Names &names, bool recorded, const gc_sample &selected, bool artwork, uint8_t font,
                     ghost_compositor::Pose &pose) {
    auto& out=pose.annotation;
    std::memcpy(out.label,label,sizeof(out.label));
    std::memcpy(out.position,position,sizeof(out.position));
    out.scale=scale;
    if(!(out.scale>0))out.scale=1;
    // The top of the body as drawn this frame (his own vertices, not a held
    // object's): the name sits on it, crouching, sliding or diving with him.
    out.top=NAN;
    for(uint32_t i=0;i<pose.body_count && i<pose.count;++i) {
        const float y=pose.vertices[i].position[1];
        if(std::isfinite(y) && !(y<=out.top))out.top=y;
    }
    out.names=names.names;
    out.background=names.background;
    out.background_opacity=names.background_opacity;
    out.slot=slot;
    out.hud=names.hud && recorded;
    out.inputs=names.inputs && recorded;
    out.values=names.values;
    out.artwork=artwork;
    out.font=font;
    if((out.inputs || out.hud) && (selected.valid_fields&GC_VALID_PAD)) {out.known=true;out.pad=selected.pad;}
}
}
