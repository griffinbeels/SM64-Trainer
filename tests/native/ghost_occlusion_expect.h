/* The player-first fade as the witnesses state it, independently of the
 * shader: a real perspective (clip w = distance ahead of the camera), a
 * marked live-Mario region, and the documented rule at a pixel centre.
 * Shared by the body/shadow witness and the particle witness. */
#pragma once
#include "ghost_occlusion.h"
#include <algorithm>
#include <cmath>
#include <cstring>

namespace occlusion_expect {
constexpr float NEAR_PLANE=10,FAR_PLANE=10000;
// The camera at the origin looking down -z: clip = (x, y, a z + b, -z).
inline void perspective(float m[16]) {
    std::memset(m,0,16*sizeof(float));
    m[0]=m[5]=1;m[10]=-(FAR_PLANE+NEAR_PLANE)/(FAR_PLANE-NEAR_PLANE);m[11]=-1;
    m[14]=-2*FAR_PLANE*NEAR_PLANE/(FAR_PLANE-NEAR_PLANE);
}
// Live Mario's marked region: centred in the right half, a quarter screen radius.
inline ghost_occlusion::Region player(float w) {
    ghost_occlusion::Region r{};r.enabled=r.has_player=1;
    r.center[0]=.5f;r.center[1]=0;r.radius[0]=r.radius[1]=.25f;r.player_w=w;return r;
}
inline float smooth(float e0,float e1,float x) {
    const float t=(std::min)(1.f,(std::max)(0.f,(x-e0)/(e1-e0)));return t*t*(3-2*t);
}
inline float fade(const ghost_occlusion::Region& r,uint32_t x,uint32_t y,float w,uint32_t width,uint32_t height) {
    using namespace ghost_occlusion;
    if(!r.enabled)return 1;
    float result=smooth(NEAR_HIDDEN,NEAR_FULL,w);
    if(r.has_player) {
        const float nx=(2*(x+.5f)/width-1-r.center[0])/r.radius[0],ny=(2*(y+.5f)/height-1-r.center[1])/r.radius[1];
        const float cover=1-smooth(REGION_INNER,REGION_OUTER,std::sqrt(nx*nx+ny*ny));
        result*=1-PLAYER_STRENGTH*cover*smooth(0,FRONT_BAND,r.player_w-w);
    }
    return result;
}
}
