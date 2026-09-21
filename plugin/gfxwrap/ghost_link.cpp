/* Built from source beside GLideN64, never a binary hook. */
#include "GLFunctions.h"
#include "ghost_link.h"
#include "ghost_stream.h"
#include "ghost_draw.h"
#include "ghost_ipc.h"
#include "link_dispatch.h"
#include "practice_rom.h"
#include "N64.h"
#include "RSP.h"
#include "GBI.h"
#include "gSP.h"
#include "gDP.h"
#include "VI.h"
#include "Config.h"
#include "DisplayWindow.h"
#include "FrameBuffer.h"
#include "DepthBuffer.h"
#include "Graphics/Context.h"

namespace {
uint32_t context_generation = 0;
bool context_current = false, observing = false;
bool supported_rom() {
    unsigned char bytes[PRACTICE_ROM_HEADER_BYTES]{};
    SIZE_T got = 0;
    return HEADER && ReadProcessMemory(GetCurrentProcess(), HEADER, bytes, sizeof(bytes), &got)
        && got == sizeof(bytes) && practice_rom(bytes);
}
}
namespace ghost_link {
void rom(bool opened) {
    const bool practice = opened && supported_rom();
    observing = false; ghost_stream::rom(practice); ghost_ipc::rom(practice, practice ? HEADER : nullptr);
}
void context_created() {
    context_current = true; context_generation = 0;
}
void context_lost() {
    if (observing) ghost_stream::end(false);
    ghost_stream::cancel();
    ghost_draw::context_lost();
    context_current = false; observing = false;
}
bool begin_list() {
    if (!context_current || !ghost_stream::requested()) {
        ghost_draw::begin(ghost_ipc::rom_generation(), 0);
        ghost_stream::cancel(); observing = false; return false;
    }
    renderer::Identity identity{}; snapshot::RestoreBindings ignored{};
    if (!replay_gl::CandidateBindings(&identity, &ignored)) {
        ghost_draw::cancel(); ghost_stream::cancel(); observing = false; return false;
    }
    context_generation = identity.context_generation;
    LARGE_INTEGER now{}; QueryPerformanceCounter(&now);
    // Upstream RDRAMSize is the inclusive maximum byte index, not a byte count.
    const uint32_t bytes = RDRAMSize == 0x7fffff || RDRAMSize == 0x3fffff ? RDRAMSize + 1 : 0;
    observing = context_current && ghost_stream::begin(RDRAM,bytes,context_generation,now.QuadPart);
    ghost_draw::begin(ghost_ipc::rom_generation(), ghost_stream::api()->stats().configuration);
    return observing;
}
void end_list(bool completed) {
    if (observing) ghost_stream::end(completed && context_current);
    ghost_draw::finish(completed && context_current ? ghost_stream::candidate() : nullptr);
    observing = false;
}
void projection() {
    if (observing) ghost_stream::projection(&gSP.matrix.projection[0][0]);
}
void geometry(uint32_t before, uint32_t after) {
    if (!observing) return;
    if ((before & G_ZBUFFER) && !(after & G_ZBUFFER)) {
        // PipeSync is a no-op upstream. Flush BOTH triangles and deferred rects
        // while the world state is still current, before sealing its branch.
        auto &drawer = dwnd().getDrawer();
        drawer.drawTriangles(); drawer.flush();
    }
    if (ghost_stream::geometry((before & G_ZBUFFER) != 0,(after & G_ZBUFFER) != 0)) {
        const auto *sample = ghost_stream::sample();
        const auto *world = ghost_stream::candidate();
        if (sample && world) ghost_draw::seal(*sample, *world);
    } else if (!ghost_stream::candidate()) ghost_draw::cancel();
}
void color_target_changed() {
    if (observing) { ghost_stream::color_target_changed(); ghost_draw::cancel(); }
}
void clear_target() {
    if (observing) { ghost_stream::color_target_changed(); ghost_draw::cancel(); }
}
bool begin_hud() {
    return observing && ghost_stream::candidate() && ghost_draw::begin_hud();
}
void end_hud() { ghost_draw::end_hud(); }
void triangles(uint32_t count) {
    if (!observing || !count || !(gSP.geometryMode & G_ZBUFFER)) return;
    auto *buffer = frameBufferList().getCurrent();
    ghost::RasterTarget target{};
    // Supported command-generation contract is deliberately specific. Missing
    // framebuffer/depth/ucode evidence becomes an invalid world, not a guess.
    if (!RSP.LLE && GBI.getMicrocodeType() == F3D && !GBI.isNoN() && buffer && buffer->m_pTexture
            && !buffer->m_isDepthBuffer && buffer->m_pDepthBuffer) {
        GLint draw = 0; glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&draw);
        if (static_cast<uint32_t>(draw) != static_cast<uint32_t>(buffer->m_FBO)) {
            ghost_stream::triangles(target,count,true); return;
        }
        const auto *depth = buffer->m_pDepthBuffer;
        target.color = static_cast<uint32_t>(buffer->m_FBO);
        target.depth = depth->m_pDepthBufferTexture
            ? static_cast<uint32_t>(depth->m_pDepthBufferTexture->name)
            : static_cast<uint32_t>(depth->m_depthRenderbuffer);
        target.width = buffer->m_pTexture->realWidth;
        target.height = buffer->m_pTexture->realHeight;
        target.samples = config.video.multisampling > 0 ? config.video.multisampling : 1;
        if (config.frameBufferEmulation.N64DepthCompare) {
            target.depth_mode = 2; // explicitly unimplemented framebuffer-fetch/MRT route
            if (graphics::Context::ImageTextures && !graphics::Context::FramebufferFetch
                    && depth->m_pDepthImageZTexture) {
                target.depth_mode = 1;
                target.depth = static_cast<uint32_t>(depth->m_pDepthImageZTexture->name);
            }
        }
        glGetIntegerv(GL_VIEWPORT,target.viewport);
        GLdouble range[2]{}; glGetDoublev(GL_DEPTH_RANGE,range);
        // Mirror the shader's actual depth path. With fragment-depth writes
        // disabled, fixed GL interpolation never applies the N64 viewport Z.
        if (!config.generalEmulation.enableFragmentDepthWrite && !target.depth_mode) {
            target.depth_scale[0] = static_cast<float>((range[1]-range[0])*0.5);
            target.depth_scale[1] = static_cast<float>((range[1]+range[0])*0.5);
        } else {
            target.depth_scale[0] = static_cast<float>((range[1]-range[0])*gSP.viewport.vscale[2]);
            target.depth_scale[1] = static_cast<float>((range[1]+range[0]-1)*gSP.viewport.vscale[2]
                + gSP.viewport.vtrans[2]);
        }
        target.screen_scale[0] = target.screen_scale[1] = 1.0f;
        // gSPProcessVertex applies these before its triangle shader flips Y.
        if (dwnd().isAdjustScreen() && gDP.colorImage.width > VI.width * 98 / 100) {
            target.screen_scale[0] = dwnd().getAdjustScale();
            if (gSP.matrix.projection[3][2] == -1.f) {
                // This unusual W correction needs a separate clip-space term.
                // Keep the occurrence explicit until that mapping is supported.
                target.depth_mode = UINT32_MAX;
            }
        }
        if (gSP.viewport.vscale[0] < 0) target.screen_scale[0] *= -1;
        if (gSP.viewport.vscale[1] < 0) target.screen_scale[1] *= -1;
    }
    ghost_stream::triangles(target,count,true);
    ghost_draw::observe(target);
}
}

#pragma comment(linker,"/EXPORT:SM64GhostSourceV2=_SM64GhostSourceV2")
extern "C" const gs_api *__cdecl SM64GhostSourceV2(uint32_t version, uint32_t bytes) {
    return version == GS_VERSION && bytes == sizeof(gs_api) ? ghost_stream::api() : nullptr;
}
