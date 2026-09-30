/* Recorded world coordinates in the live area's frame. DDD's instant portal
 * moves the player between two translated copies of its swimming tunnel; the
 * ghost crosses on its own recorded frame. Other scene changes still refuse. */
#pragma once
#include "ghost_capture.h"

namespace ghost_scene {
inline bool translation(int16_t recorded_level, int16_t recorded_area,
                        int16_t live_level, int16_t live_area, float &x) {
    x = 0;
    if (recorded_level != live_level) return false;
    if (recorded_area == live_area) return true;
    // n64decomp/sm64 06ec56df7f951f88da05f468cdcacecba496145a,
    // levels/ddd/script.c: area 1 -> 2 (-8192, 0, 0), reciprocal +8192.
    if (recorded_level != 23) return false;
    if (recorded_area == 1 && live_area == 2) x = -8192.0f;
    else if (recorded_area == 2 && live_area == 1) x = 8192.0f;
    else return false;
    return true;
}
inline bool compatible(const gc_sample &recorded, const gc_sample &live) {
    float delta_x;
    if (!translation(recorded.level, recorded.area, live.level, live.area, delta_x)) return false;
    if (delta_x == 0) return true;
    if (!(recorded.valid_fields & GC_VALID_POSE)) return false;
    // Only the rendered connector exists in both loaded areas. Intersect the
    // pinned area_1/2 and area_2/2 model floor, ceiling and walls in area-1
    // coordinates (+8192 for area 2). The common extrusion includes the deep
    // shafts, beveled bend and right duct; its bounding box includes empty air
    // and the area-1-only left duct, so a box alone cannot admit a root.
    const float root_x = recorded.pose.position[0] + (recorded.area == 2 ? 8192.0f : 0.0f);
    const auto *p = recorded.pose.position;
    if (!(root_x >= 2151.0f && root_x <= 9037.0f && p[1] >= -4607.0f && p[1] <= 1229.0f
            && p[2] >= -511.0f && p[2] <= 512.0f)) return false;
    struct Point { double x, y; };
    constexpr Point tunnel[] = {{2151,-4607},{3174,-4607},{3174,-511},{5019,-511},{5019,-2449},
        {5326,-2756},{9037,-2756},{9037,-1732},{6042,-1732},{6042,205},{5018,1229},{3175,1229},{2151,205}};
    const double x=root_x,y=p[1];
    bool inside=false;
    for (unsigned i=0,j=12;i<13;j=i++) {
        const auto &a=tunnel[j],&b=tunnel[i];
        const double cross=(x-a.x)*(b.y-a.y)-(y-a.y)*(b.x-a.x);
        if (cross==0 && ((x>=a.x && x<=b.x)||(x>=b.x && x<=a.x))
                && ((y>=a.y && y<=b.y)||(y>=b.y && y<=a.y))) return true;
        if ((a.y>y)!=(b.y>y) && x<(b.x-a.x)*(y-a.y)/(b.y-a.y)+a.x) inside=!inside;
    }
    return inside;
}
inline void translate(gc_sample &recorded, int16_t live_area, float x) {
    if (x == 0) return;
    if (recorded.valid_fields & GC_VALID_POSE) recorded.pose.position[0] += x;
    if (recorded.valid_fields & GC_VALID_CAMERA) {
        recorded.camera.position[0] += x;
        recorded.camera.focus[0] += x;
    }
    if (recorded.valid_fields & GC_VALID_HELD) recorded.held.holp[0] += x;
    recorded.area = live_area;
    // Root/shadow matrices and particle translations are recorded VIEW space.
    // Keep them intact: removing the translated recorded camera moves their
    // world positions exactly once, including a scaled-modelview Mario root.
}
inline bool rebase(gc_sample &recorded, const gc_sample &live) {
    float x;
    if (!compatible(recorded, live)) return false;
    translation(recorded.level, recorded.area, live.level, live.area, x);
    translate(recorded, live.area, x);
    return true;
}
// A bridged refusal can still carry known particles and their camera. Its
// earlier shown pose supplies connector admission; the current row must have
// that same recorded scene. The pose bridge itself never crosses a scene.
inline bool rebase_components(gc_sample &recorded, const gc_sample &shown, const gc_sample &live) {
    if (recorded.level != shown.level || recorded.area != shown.area || !compatible(shown, live)) return false;
    float x;
    translation(recorded.level, recorded.area, live.level, live.area, x);
    translate(recorded, live.area, x);
    return true;
}
}
