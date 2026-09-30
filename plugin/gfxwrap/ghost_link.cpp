/* Built from source beside GLideN64, never a binary hook. */
#include "GLFunctions.h"
#include "ghost_link.h"
#include "ghost_stream.h"
#include "ghost_draw.h"
#include "ghost_ipc.h"
#include "ghost_audio.h"
#include "ghost_playback.h"
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
// Ghost sound: playback hands recorded sounds to the audio thread, whose
// engine the IPC worker starts from its verified ROM and waveOut.
bool __cdecl audio_start(const uint8_t *rom, size_t bytes) {
    return ghost_audio::start(rom, bytes, ghost_audio::wave_out()) == ghost_audio::Start::ok;
}
void __cdecl audio_stop() { ghost_audio::stop(); }
const ghost_ipc::AudioHooks audio_hooks{audio_start, audio_stop};
const struct AudioWiring {
    AudioWiring() { ghost_playback::set_sound_sink(ghost_audio::sink()); ghost_ipc::set_audio(&audio_hooks); }
} audio_wiring;
uint32_t context_generation = 0;
bool context_current = false, observing = false;
// The hooks' own cost, per list: driver state queries (only when the
// dispatch has not observed the value yet) and time inside the hooks. The
// per-draw hooks used to query the draw FBO, viewport and depth range on
// every z-buffered draw, ghost or not: a pipeline sync each when the driver
// threads its GL. Published per list for the status page, with the whole
// list's time, the renderer's own flush at the seal and ghost_draw's stages.
uint32_t hook_queries = 0, list_serial = 0;
int64_t hook_ticks = 0, flush_ticks = 0, list_start = 0, qpc_frequency = 0;
struct HookTime {
    int64_t &into; LARGE_INTEGER start{};
    explicit HookTime(int64_t &ticks = hook_ticks) : into(ticks) { QueryPerformanceCounter(&start); }
    ~HookTime() { LARGE_INTEGER end{}; QueryPerformanceCounter(&end); into += end.QuadPart - start.QuadPart; }
};
uint32_t micros(int64_t ticks) {
    if (!qpc_frequency) { LARGE_INTEGER f{}; QueryPerformanceFrequency(&f); qpc_frequency = f.QuadPart; }
    const int64_t value = qpc_frequency > 0 ? ticks * 1000000 / qpc_frequency : 0;
    return value < 0 ? 0u : value > UINT32_MAX ? UINT32_MAX : uint32_t(value);
}
replay_gl::RasterState tracked() {
    replay_gl::RasterState state{};
    replay_gl::Raster(&state);
    return state;
}
bool supported_rom() {
    unsigned char bytes[PRACTICE_ROM_HEADER_BYTES]{};
    SIZE_T got = 0;
    return HEADER && ReadProcessMemory(GetCurrentProcess(), HEADER, bytes, sizeof(bytes), &got)
        && got == sizeof(bytes) && practice_rom(bytes);
}
}
namespace ghost_link {
FrameBuffer* raster_buffer() {
    auto& buffers = frameBufferList();
    auto* buffer = config.frameBufferEmulation.enable ? buffers.getCurrent() : buffers.getScreenSizeBuffer();
    if (!buffer || !buffer->m_pTexture || !uint32_t(buffer->m_FBO)) return nullptr;
    // The renderer's own last draw binding, as its dispatch saw it bound.
    const auto state = tracked();
    uint32_t draw = state.draw_framebuffer;
    if (!state.draw_known) {
        GLint value = 0; glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &value); ++hook_queries;
        draw = uint32_t(value);
    }
    return draw == uint32_t(buffer->m_FBO) ? buffer : nullptr;
}
uint64_t attachments(const FrameBuffer *buffer) {
    if (!buffer || !buffer->m_pTexture) return 0;
    const auto *depth = buffer->m_pDepthBuffer;
    const uint32_t depth_name = !depth ? 0u : depth->m_pDepthBufferTexture
        ? uint32_t(depth->m_pDepthBufferTexture->name) : uint32_t(depth->m_depthRenderbuffer);
    return (uint64_t(uint32_t(buffer->m_pTexture->name)) << 32) | depth_name;
}
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
    hook_queries = 0; hook_ticks = 0; flush_ticks = 0;
    { LARGE_INTEGER now{}; QueryPerformanceCounter(&now); list_start = now.QuadPart; }
    HookTime timed;
    if (!context_current || !ghost_stream::requested()) {
        ghost_draw::begin(ghost_ipc::rom_generation(), 0, nullptr);
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
    observing = context_current && ghost_stream::begin(RDRAM,bytes,context_generation,now.QuadPart,gSP.segment);
    ghost_draw::begin(ghost_ipc::rom_generation(), ghost_stream::api()->stats().configuration,
                      observing ? ghost_stream::sample() : nullptr);
    return observing;
}
void end_list(bool completed) {
    {
        HookTime timed;
        if (observing) ghost_stream::end(completed && context_current);
        ghost_draw::finish(completed && context_current ? ghost_stream::candidate() : nullptr);
        observing = false;
    }
    gs_hook_cost cost{};
    ghost_draw::take_cost(cost);
    cost.gl_queries += hook_queries; cost.micros = micros(hook_ticks); cost.list = ++list_serial;
    cost.flush_micros = micros(flush_ticks);
    LARGE_INTEGER now{}; QueryPerformanceCounter(&now); cost.list_micros = micros(now.QuadPart - list_start);
    ghost_stream::hooks(cost);
}
void projection() {
    if (observing) ghost_stream::projection(&gSP.matrix.projection[0][0]);
}
void display_list(uint32_t dl, uint32_t physical_dl) {
    if (!observing) return;
    HookTime timed;
    if (!RSP.LLE && GBI.getMicrocodeType()==F3D && !GBI.isNoN()
            && RSP.PC[RSP.PCi]>=8)
        ghost_stream::display_list(RSP.PC[RSP.PCi]-8,dl,physical_dl,RSP.PCi,
                                  &gSP.matrix.modelView[gSP.matrix.modelViewi][0][0]);
}
void geometry(uint32_t before, uint32_t after) {
    if (!observing) return;
    HookTime timed;
    if ((before & G_ZBUFFER) && !(after & G_ZBUFFER)) {
        // PipeSync is a no-op upstream. Flush BOTH triangles and deferred rects
        // while the world state is still current, before sealing its branch.
        auto &drawer = dwnd().getDrawer();
        HookTime flushed(flush_ticks); // inside the hook's time too, and apart
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
bool begin_layer(uint32_t pass) {
    // No ghost will draw this list: every XLU draw leaves at once, before any
    // framebuffer lookup.
    if (!observing || !ghost_draw::drawing()) return false;
    HookTime timed;
    if (!ghost_stream::requested() || !(gSP.geometryMode & G_ZBUFFER) || RSP.LLE
            || GBI.getMicrocodeType() != F3D || GBI.isNoN() || config.frameBufferEmulation.N64DepthCompare
            || gDP.otherMode.cycleType > G_CYC_2CYCLE)
        return false;
    // A draw that writes depth after the layer opened moves the scene the
    // layer was measured against; the list keeps no layer rather than a stale one.
    if (gDP.otherMode.depthUpdate) { if (pass == 0) ghost_draw::break_layer(); return false; }
    if (!gDP.otherMode.depthCompare) return false;
    auto *buffer = raster_buffer();
    return buffer && !buffer->m_isDepthBuffer && buffer->m_pDepthBuffer
        && ghost_draw::begin_layer(pass, uint32_t(buffer->m_FBO), attachments(buffer));
}
void end_layer() { HookTime timed; ghost_draw::end_layer(); }
void close_layer() {
    if (!ghost_draw::layer_open()) return;
    HookTime timed; ghost_draw::close_layer();
}
void triangles(uint32_t count) {
    if (!observing || !count || !(gSP.geometryMode & G_ZBUFFER)) return;
    HookTime timed;
    auto *buffer = raster_buffer();
    ghost::RasterTarget target{};
    // Supported command-generation contract is deliberately specific. Missing
    // framebuffer/depth/ucode evidence becomes an invalid world, not a guess.
    if (!RSP.LLE && GBI.getMicrocodeType() == F3D && !GBI.isNoN() && buffer && buffer->m_pTexture
            && !buffer->m_isDepthBuffer && buffer->m_pDepthBuffer) {
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
        // The renderer's own viewport and depth range, as its dispatch set
        // them; a driver query only before the dispatch has seen one.
        const auto state = tracked();
        GLdouble range[2]{state.depth_range[0], state.depth_range[1]};
        if (state.viewport_known) for (unsigned i = 0; i < 4; ++i) target.viewport[i] = state.viewport[i];
        else { glGetIntegerv(GL_VIEWPORT,target.viewport); ++hook_queries; }
        if (!state.depth_known) { glGetDoublev(GL_DEPTH_RANGE,range); ++hook_queries; }
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
        // Metal Mario's texture generation reads this; the ghost must too.
        ghost::Lookat lookat{};
        for (unsigned i = 0; i < 3; ++i) {
            lookat.x[i] = gSP.lookat.xyz[0][i]; lookat.y[i] = gSP.lookat.xyz[1][i];
        }
        lookat.enabled = gSP.lookatEnable ? 1u : 0u;
        ghost_stream::lookat(lookat);
    }
    ghost_stream::triangles(target,count,true);
    ghost_draw::observe(target);
}
}

#pragma comment(linker,"/EXPORT:SM64GhostSourceV3=_SM64GhostSourceV3")
extern "C" const gs_api *__cdecl SM64GhostSourceV3(uint32_t version, uint32_t bytes) {
    return version == GS_VERSION && bytes == sizeof(gs_api) ? ghost_stream::api() : nullptr;
}
