#include "ghost_pass.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <limits>
using namespace ghost;
static const float projection[16] = {
    1.299038f,0,0,0, 0,1.732051f,0,0,
    0,0,-1.020202f,-1, 0,0,-202.0202f,0
};
static RasterTarget target() {
    return {7,8,1280,960,1,0,{0,0,1280,960},{0.5f,0.5f},{1,1}};
}
static void world(Pass &p) {
    p.begin(2,3,42,true); p.projection(projection);
    p.triangles(target(),27,true);
}
int main() {
    Pass p;
    // Same occurrence admits exactly the captured projection/mapping. HUD
    // switches to orthographic after sealing the world; this must stay valid.
    world(p); assert(!p.candidate());
    assert(p.geometry(true,false));
    float ortho[16] = {1,0,0,0, 0,1,0,0, 0,0,-1,0, 0,0,0,1};
    p.projection(ortho); p.triangles(target(),12,false);
    assert(p.end(true) && p.status()==PassStatus::Ready);
    assert(p.candidate()->triangles==27 && p.candidate()->list_serial==42);
    assert(p.candidate()->projection[11]==-1 && p.candidate()->target.viewport[2]==1280);
    assert(!p.end(true));
    // The renderer's lookat is taken with the world's first triangles; a
    // later change in the same list does not rewrite that world.
    {
        Pass q; q.begin(2,3,43,true); q.projection(projection);
        const Lookat first{{0,1,0},{1,0,0},1}, later{{1,0,0},{0,1,0},0};
        q.lookat(first); q.triangles(target(),3,true); q.lookat(later); q.triangles(target(),3,true);
        assert(q.geometry(true,false) && q.end(true));
        assert(q.candidate()->lookat.enabled==1 && q.candidate()->lookat.x[1]==1 && q.candidate()->lookat.y[0]==1);
    }
    // A second Z world invalidates the earlier provisional branch.
    world(p); assert(p.geometry(true,false)); p.geometry(false,true);
    assert(!p.end(true) && !p.candidate() && p.status()==PassStatus::MultipleWorlds);
    world(p); assert(p.geometry(true,false)); p.triangles(target(),1,true);
    assert(p.status()==PassStatus::MultipleWorlds && !p.candidate());
    // Target changes must revoke even without another triangle.
    world(p); assert(p.geometry(true,false)); p.color_target_changed();
    assert(!p.end(true) && !p.candidate() && p.status()==PassStatus::TargetChanged);
    world(p); RasterTarget moved=target(); moved.viewport[0]=1;
    p.triangles(moved,1,true); assert(p.status()==PassStatus::ViewportChanged);
    world(p); moved=target(); moved.depth=9;
    p.triangles(moved,1,true); assert(p.status()==PassStatus::TargetChanged);
    world(p); moved=target(); moved.depth_mode=2;
    p.triangles(moved,1,true); assert(p.status()==PassStatus::UnsupportedDepth);
    p.begin(2,3,43,true); p.projection(projection); moved=target(); moved.depth_mode=1;
    p.triangles(moved,1,true); assert(p.geometry(true,false) && p.end(true));
    world(p); moved=target(); moved.samples=4;
    p.triangles(moved,1,true); assert(p.status()==PassStatus::UnsupportedDepth);
    world(p); p.projection(ortho); assert(p.status()==PassStatus::ProjectionChanged);
    // Bad projection cannot become valid through stale previous-frame state.
    p.begin(2,3,43,true); p.triangles(target(),1,true);
    assert(!p.end(true) && p.status()==PassStatus::InvalidProjection);
    float bad[16]; for(unsigned i=0;i<16;++i) bad[i]=projection[i];
    bad[0]=std::numeric_limits<float>::quiet_NaN();
    p.begin(2,3,44,true); p.projection(bad); p.triangles(target(),1,true);
    assert(p.status()==PassStatus::InvalidProjection);
    // Normalized perspective + roll remain valid (no fixed FOV assumption).
    for(unsigned i=0;i<16;++i) bad[i]=projection[i]*0.125f;
    bad[0]=0; bad[1]=0.16238f; bad[4]=-0.2165f; bad[5]=0;
    p.begin(2,3,45,true); p.projection(bad); p.triangles(target(),1,true);
    assert(p.geometry(true,false) && p.end(true));
    world(p); assert(p.geometry(true,false)); assert(!p.end(false));
    assert(p.status()==PassStatus::Interrupted && !p.candidate());
    world(p); assert(!p.end(true) && p.status()==PassStatus::NoWorld);
    p.begin(2,3,46,false); p.projection(projection); p.triangles(target(),1,true);
    assert(!p.geometry(true,false) && !p.end(true) && !p.candidate());
    p.begin(0,3,47,true); assert(p.status()==PassStatus::InvalidIdentity);
    // A reusable historical pose cannot supply missing LIVE framebuffer/depth
    // evidence. This pass has no candidate for the draw/pose callback.
    p.begin(2,3,48,true); p.projection(projection); p.triangles({},1,true);
    assert(p.status()==PassStatus::InvalidIdentity && !p.candidate());
    assert(!p.geometry(true,false) && !p.end(true) && !p.candidate());
    p.reset(); assert(p.status()==PassStatus::Disabled && !p.candidate());
    std::puts("world-pass qualification passed");
}
