/* The REAL link_dispatch.cpp over a fake driver (link_fake_gl/): how the
 * bindings witness breaks and heals. Each scenario arms a fresh context the
 * way the staged renderer does (ContextCreated, then initGLFunctions'
 * InstallDispatch), moves the driver's true state behind the witness where
 * the scenario says, and prints one line the Python test reads. No context,
 * window or GPU. */
#include "fake_gl.h"
#include "link_dispatch.h"
#include "N64.h"
#include "practice_rom_fixture.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

unsigned char cartridge[0x40];
u8 *HEADER = cartridge;
extern "C" int __cdecl SM64ReplayContextEvidenceV1(rs_context_evidence *, uint32_t);

namespace {
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "link cpu line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
// A fresh driver and context, armed and dispatched as the renderer does it.
void arm(bool practice = true, bool with_active_texture = true) {
    practice_rom_fixture(cartridge, practice ? "SM64 USAMUNE v1.93u" : "SUPER MARIO 64", 'E');
    replay_gl::ContextLost();
    fake::reset(); fake::advance(5000); fake::load_table(with_active_texture);
    replay_gl::ContextCreated(fake::context, fake::dc, true);
    replay_gl::InstallDispatch();
}
bool qualifies(renderer::Identity *identity = nullptr, snapshot::RestoreBindings *bindings = nullptr) {
    renderer::Identity ignored{}; snapshot::RestoreBindings unused{};
    return replay_gl::CandidateBindings(identity ? identity : &ignored, bindings ? bindings : &unused);
}
// What surface() does on a BINDINGS refusal: rebuild once, then ask again.
bool surface_qualifies() { return qualifies() || (replay_gl::Reconstruct() && qualifies()); }
rs_bindings_cause cause() { rs_bindings_cause out{}; replay_gl::BindingsCause(&out); return out; }
rs_context_evidence evidence() {
    rs_context_evidence out{}; CHECK(SM64ReplayContextEvidenceV1(&out, sizeof out)); return out;
}

// Hypothesis (a): an error GLideN64's Release build left pending is read at
// the first capture gateway. Meanwhile the renderer keeps changing state the
// witness no longer records; the rebuild must report the DRIVER's state.
void first_gateway_error() {
    arm();
    CHECK(qualifies());
    fake::raise(GL_INVALID_OPERATION);
    CHECK(!replay_gl::BeginCapture() && !qualifies() && cause().site == RS_BINDINGS_GATEWAY_OPEN_ERROR);
    g_glActiveTexture(GL_TEXTURE0 + 3); glBindTexture(GL_TEXTURE_2D, 42);   // unobserved while invalid
    g_glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 9); glViewport(0, 0, 800, 600);
    g_glUseProgram(5); g_glBindVertexArray(6); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glEnable(GL_BLEND); glScissor(1, 2, 300, 200); glDepthMask(GL_FALSE);
    const unsigned before = fake::gl.queries;
    CHECK(replay_gl::Reconstruct());
    const unsigned queries = fake::gl.queries - before;
    renderer::Identity identity{}; snapshot::RestoreBindings bindings{};
    CHECK(qualifies(&identity, &bindings));
    unsigned unit = 99; GLuint texture = 0, other = 1;
    CHECK(replay_gl::ActiveUnit(&unit) && replay_gl::Texture2D(3, &texture) && !replay_gl::Texture2D(0, &other));
    replay_gl::RasterState raster{}; replay_gl::RenderState render{}; bool blend = false;
    CHECK(replay_gl::Raster(&raster) && replay_gl::Render(&render) && replay_gl::Enabled(GL_BLEND, &blend));
    // The pending answer no longer refuses the gateway (the Release build never
    // asks for it); the renderer's own read gets it exactly once, and that read
    // no longer breaks the rebuilt witness.
    const bool gateway = replay_gl::BeginCapture() && replay_gl::EndCapture();
    const GLenum first = glGetError(), second = glGetError();
    const bool still = qualifies();
    const auto e = evidence();
    std::printf("first_gateway_error rebuilt=1 read=%u texture=%u selector=0x%x unit=%u unit3=%u unit0_known=0 "
                "draw=%u viewport=%dx%d program=%u array=%u blend=0x%x,0x%x enabled_blend=%d depth_mask=%d "
                "scissor=%d,%d,%d,%d queries=%u renderer_error=0x%x then=0x%x still=%d gateway=%d "
                "site_after=%u attempts=%u successes=%u repaired=%u repaired_error=0x%x\n",
                bindings.read_framebuffer, bindings.texture_2d, bindings.source_read_buffer, unit, texture,
                raster.draw_framebuffer, raster.viewport[2], raster.viewport[3], render.program, render.vertex_array,
                render.blend[0], render.blend[1], int(blend), int(render.depth_mask), render.scissor[0],
                render.scissor[1], render.scissor[2], render.scissor[3], queries, first, second, int(still),
                int(gateway), cause().site, e.reconstruct_attempts, e.reconstruct_successes, e.reconstructed_site,
                e.reconstructed_gl_error);
}
// Our own scope's close error (a ghost's draw): one invalidation, one rebuild.
void close_error() {
    arm();
    CHECK(replay_gl::BeginCapture());
    fake::raise(GL_INVALID_OPERATION);
    const bool closed = replay_gl::EndCapture();
    const auto site = cause().site;
    const bool healed = surface_qualifies();
    std::printf("close_error closed=%d site=%u healed=%d renderer_error=0x%x\n",
                int(closed), site, int(healed), glGetError());
}
// A layer's own error is drained and never touches the source.
void layer_error() {
    arm();
    CHECK(replay_gl::BeginLayer());
    fake::raise(GL_INVALID_VALUE);
    const bool layer = replay_gl::EndLayer();
    const unsigned before = fake::gl.queries;
    const bool source = qualifies() && replay_gl::BeginCapture() && replay_gl::EndCapture();
    std::printf("layer_error layer=%d source=%d attempts=%u queries=%u\n", int(layer), int(source),
                evidence().reconstruct_attempts, fake::gl.queries - before);
}
// Structural sites never rebuild, and ask the driver nothing.
void structural(const char *name, void (*breaks)(), uint32_t site) {
    arm();
    breaks();
    const auto seen = cause();
    const unsigned before = fake::gl.queries;
    fake::advance(2000);
    const bool rebuilt = replay_gl::Reconstruct() || qualifies();
    fake::advance(2000);
    const bool again = replay_gl::Reconstruct();
    std::printf("structural %s site=%u expected=%u rebuilt=%d again=%d queries=%u\n", name, seen.site, site,
                int(rebuilt), int(again), fake::gl.queries - before);
}
void reinstalled() { replay_gl::InstallDispatch(); }
void other_thread() { std::thread([] { replay_gl::DrawableChanged(); }).join(); }
void bad_window() { replay_gl::DrawableCommitted(nullptr, true); }
void lost_layer() { CHECK(replay_gl::BeginLayer()); fake::raise(GL_OUT_OF_MEMORY); replay_gl::EndLayer(); }
void recoverable_then_other_thread() {
    fake::raise(GL_INVALID_OPERATION); replay_gl::BeginCapture(); other_thread();
}
// Arming refusals happen inside arm(): a context created without being current,
// and a driver with no texture units.
void not_current() {
    replay_gl::ContextLost(); fake::reset(); fake::load_table();
    fake::gl.current_context = reinterpret_cast<HGLRC>(0x9999);
    replay_gl::ContextCreated(fake::context, fake::dc, true);
    fake::gl.current_context = fake::context; replay_gl::InstallDispatch();
}
void no_units() {
    replay_gl::ContextLost(); fake::reset(); fake::load_table(); fake::gl.units = 0;
    replay_gl::ContextCreated(fake::context, fake::dc, true); replay_gl::InstallDispatch();
}
void install_elsewhere() {
    replay_gl::ContextLost(); fake::reset(); fake::load_table();
    replay_gl::ContextCreated(fake::context, fake::dc, true);
    fake::gl.current_context = reinterpret_cast<HGLRC>(0x9999); replay_gl::InstallDispatch();
    fake::gl.current_context = fake::context;
}
// A fatal driver error drained by the rebuild ends rebuilding for the context.
void fatal_drain(GLenum fatal) {
    arm();
    fake::raise(GL_INVALID_ENUM);
    CHECK(!replay_gl::BeginCapture());
    fake::raise(fatal);
    const bool first = replay_gl::Reconstruct();
    fake::advance(2000);
    const unsigned before = fake::gl.queries;
    const bool later = replay_gl::Reconstruct();
    std::printf("fatal_drain error=0x%x first=%d later=%d queries=%u renderer_error=0x%x structural=%d\n",
                fatal, int(first), int(later), fake::gl.queries - before, glGetError(),
                int((cause().flags & RS_BINDINGS_STRUCTURAL) != 0));
}
// The rebuild's own query error is never the renderer's, and leaves it invalid.
void query_error() {
    arm();
    fake::raise(GL_INVALID_OPERATION);
    CHECK(!replay_gl::BeginCapture());
    fake::gl.failing_query = GL_VIEWPORT; // one answer the driver will not give
    const bool first = replay_gl::Reconstruct();
    fake::gl.failing_query = 0;
    const bool leftover = fake::gl.flag_count != 0;
    const bool soon = replay_gl::Reconstruct(); // inside the second
    fake::advance(1000);
    const bool later = replay_gl::Reconstruct();
    const auto e = evidence();
    std::printf("query_error first=%d leftover=%d soon=%d later=%d attempts=%u successes=%u renderer_error=0x%x\n",
                int(first), int(leftover), int(soon), int(later), e.reconstruct_attempts, e.reconstruct_successes,
                glGetError());
}
// An error every frame at 60 Hz for two seconds: query batches stay bounded.
void every_frame() {
    arm();
    const unsigned before = fake::gl.queries;
    unsigned refused = 0;
    for (unsigned frame = 0; frame < 120; ++frame) {
        fake::raise(GL_INVALID_OPERATION); // the renderer's per-frame error
        if (replay_gl::BeginCapture()) replay_gl::EndCapture();
        refused += !surface_qualifies();
        glGetError();
        fake::advance(16);
    }
    const auto e = evidence();
    std::printf("every_frame frames=120 attempts=%u successes=%u refused=%u queries=%u\n",
                e.reconstruct_attempts, e.reconstruct_successes, refused, fake::gl.queries - before);
}
// The default framebuffer's selector is asked only while it is bound for reading.
void read_framebuffer_bound() {
    arm();
    fake::raise(GL_INVALID_OPERATION);
    CHECK(!replay_gl::BeginCapture());
    g_glBindFramebuffer(GL_READ_FRAMEBUFFER, 4);
    const bool rebuilt = replay_gl::Reconstruct();
    const bool qualified = qualifies();
    const auto flags = cause().flags;
    g_glBindFramebuffer(GL_READ_FRAMEBUFFER, 0); // GLideN64 binds the default before its swap
    const bool soon = surface_qualifies();
    fake::advance(1000);
    const bool later = surface_qualifies();
    std::printf("read_framebuffer_bound rebuilt=%d qualified=%d nominal=%d default_known=%d soon=%d later=%d\n",
                int(rebuilt), int(qualified), int((flags & RS_BINDINGS_NOMINAL) != 0),
                int((flags & RS_BINDINGS_DEFAULT_READ_KNOWN) != 0), int(soon), int(later));
}
// A resize while invalid still advances the drawable the rebuilt witness names.
void resize_while_invalid() {
    arm();
    renderer::Identity before{}; CHECK(qualifies(&before));
    fake::raise(GL_INVALID_OPERATION);
    CHECK(!replay_gl::BeginCapture());
    replay_gl::DrawableChanged();
    renderer::Identity after{};
    const bool healed = surface_qualifies() && qualifies(&after);
    std::printf("resize_while_invalid healed=%d drawable_generation=+%u context_generation=+%u\n",
                int(healed), after.drawable_generation - before.drawable_generation,
                after.context_generation - before.context_generation);
}
// A vanilla cartridge: nothing observed, nothing rebuilt, nothing asked.
void vanilla() {
    arm(false);
    const unsigned before = fake::gl.queries;
    const bool rebuilt = replay_gl::Reconstruct();
    std::printf("vanilla rebuilt=%d queries=%u\n", int(rebuilt), fake::gl.queries - before);
}
}

int main() {
    first_gateway_error();
    close_error();
    layer_error();
    structural("reinstalled", reinstalled, RS_BINDINGS_DISPATCH_REINSTALLED);
    structural("other_thread", other_thread, RS_BINDINGS_DRAWABLE_CHANGED);
    structural("bad_window", bad_window, RS_BINDINGS_DRAWABLE_COMMITTED);
    structural("lost_layer", lost_layer, RS_BINDINGS_LAYER_FATAL_ERROR);
    structural("recoverable_then_other_thread", recoverable_then_other_thread, RS_BINDINGS_GATEWAY_OPEN_ERROR);
    structural("not_current", not_current, RS_BINDINGS_CREATED_NOT_CURRENT);
    structural("no_units", no_units, RS_BINDINGS_CREATED_NO_UNITS);
    structural("install_elsewhere", install_elsewhere, RS_BINDINGS_DISPATCH_NOT_CURRENT);
    {
        // A driver without glActiveTexture: arm() itself installs an incomplete table.
        arm(true, false);
        const auto seen = cause();
        fake::advance(2000);
        const unsigned before = fake::gl.queries;
        const bool rebuilt = replay_gl::Reconstruct();
        std::printf("structural incomplete site=%u expected=%u rebuilt=%d again=0 queries=%u\n", seen.site,
                    unsigned(RS_BINDINGS_DISPATCH_INCOMPLETE), int(rebuilt), fake::gl.queries - before);
    }
    fatal_drain(GL_OUT_OF_MEMORY);
    fatal_drain(0x0507); // GL_CONTEXT_LOST
    query_error();
    every_frame();
    read_framebuffer_bound();
    resize_while_invalid();
    vanilla();
    return 0;
}
