/* ghost_link's real begin_list (copied verbatim from plugin/gfxwrap/ghost_link.cpp
 * by tests/test_link_dispatch_cpu.py into actual_begin_list.inc) over the REAL
 * link_dispatch.cpp and the fake driver: with no recording request at all, a
 * ghost list rebuilds a recoverably broken witness and pose capture begins.
 * The pose stream, draw glue and IPC worker are shells that record what the
 * list start asked of them. No context, window or GPU. */
#include "fake_gl.h"
#include "link_dispatch.h"
#include "N64.h"
#include "practice_rom_fixture.h"
#include "ghost_draw_facts.h"
#include <cstdio>
#include <cstdlib>

unsigned char cartridge[0x40];
u8 *HEADER = cartridge;
unsigned char rdram_bytes[16];
unsigned char *RDRAM = rdram_bytes;
uint32_t RDRAMSize = 0x7fffff;
struct { uint32_t segment[16]; } gSP;
extern "C" int __cdecl SM64ReplayContextEvidenceV1(rs_context_evidence *, uint32_t);

namespace ghost_stream {
struct Stats { uint32_t configuration; };
struct Api { Stats stats() const { return {77}; } };
const Api *api() { static Api shell; return &shell; }
unsigned begins = 0, cancels = 0;
uint32_t begun_generation = 0;
bool requested() { return true; } // the trainer wants poses; no recording is requested
void cancel() { ++cancels; }
bool begin(const unsigned char *, uint32_t bytes, uint32_t generation, int64_t, const uint32_t *) {
    ++begins; begun_generation = generation; return bytes == 0x800000;
}
const void *sample() { static int shell; return &shell; }
}
namespace ghost_draw {
unsigned begins = 0;
uint32_t configuration = 0;
void begin(uint32_t, uint32_t config, const void *) { ++begins; configuration = config; }
void cancel() {}
}
namespace ghost_ipc { uint32_t rom_generation() { return 1; } }
namespace {
uint32_t context_generation = 0;
bool context_current = true, observing = false;
uint32_t hook_queries = 0;
int64_t hook_ticks = 0, flush_ticks = 0, list_start = 0;
struct HookTime { explicit HookTime(int64_t & = hook_ticks) {} };
void renderer_settings() {} // ghost_link's settings read: the renderer's Config is not shelled here
}
namespace ghost_link {
#include "actual_begin_list.inc"
}

namespace {
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "ghost list cpu line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
void arm() {
    replay_gl::ContextLost();
    fake::reset(); fake::advance(5000); fake::load_table();
    replay_gl::ContextCreated(fake::context, fake::dc, true);
    replay_gl::InstallDispatch();
}
bool qualifies(renderer::Identity *identity) {
    snapshot::RestoreBindings unused{};
    return replay_gl::CandidateBindings(identity, &unused);
}
rs_context_evidence evidence() {
    rs_context_evidence out{}; CHECK(SM64ReplayContextEvidenceV1(&out, sizeof out)); return out;
}
// One recoverable error at a capture gateway (the presentation's setup at the
// context's start, before any recording request).
void break_witness() {
    fake::raise(GL_INVALID_OPERATION);
    CHECK(!replay_gl::BeginCapture());
}
}

int main() {
    practice_rom_fixture(cartridge, "SM64 USAMUNE v1.93u", 'E');
    arm();
    unsigned clean_gate = 0, structural_gate = 0, rdram_gate = 0;
    {
        const unsigned begins = ghost_stream::begins;
        ghost_facts::list_pending = 0;
        const bool observing = ghost_link::begin_list();
        clean_gate = ghost_facts::list_pending;
        std::printf("clean observing=%d begins=+%u queries=%u attempts=%u\n", int(observing),
                    ghost_stream::begins - begins, hook_queries, evidence().reconstruct_attempts);
    }
    {
        // Candidate F (2026-10-30): RDRAM sized other than 4 or 8 MiB reads
        // nothing, and the list's first refusal says so.
        RDRAMSize = 0x7fffef;
        ghost_facts::list_pending = 0;
        const bool observing = ghost_link::begin_list();
        rdram_gate = ghost_facts::list_pending;
        RDRAMSize = 0x7fffff;
        CHECK(!observing && rdram_gate == GDF_LIST_RDRAM_SIZE);
    }
    arm();
    break_witness();
    {
        renderer::Identity identity{};
        const bool broken = !qualifies(&identity);
        const unsigned begins = ghost_stream::begins;
        const bool observing = ghost_link::begin_list();
        const bool healed = qualifies(&identity);
        const auto e = evidence();
        std::printf("healed broken=%d observing=%d healed=%d begins=+%u generation_match=%d configuration=%u "
                    "queries=%u attempts=%u successes=%u renderer_error=0x%x\n", int(broken), int(observing),
                    int(healed), ghost_stream::begins - begins, int(ghost_stream::begun_generation == identity.context_generation),
                    ghost_draw::configuration, hook_queries, e.reconstruct_attempts, e.reconstruct_successes, glGetError());
    }
    arm();
    replay_gl::InstallDispatch(); // structural: the table installed twice
    {
        const unsigned begins = ghost_stream::begins, cancels = ghost_stream::cancels;
        ghost_facts::list_pending = 0;
        const bool observing = ghost_link::begin_list();
        structural_gate = ghost_facts::list_pending;
        fake::advance(2000);
        const bool later = ghost_link::begin_list();
        std::printf("ghost_structural observing=%d later=%d begins=+%u cancels=+%u queries=%u attempts=%u\n",
                    int(observing), int(later), ghost_stream::begins - begins, ghost_stream::cancels - cancels,
                    hook_queries, evidence().reconstruct_attempts);
    }
    arm();
    break_witness();
    {
        // One limit for both askers: the recording's rebuild a moment ago
        // leaves the ghost's next try for the next second.
        CHECK(replay_gl::Reconstruct());
        break_witness();
        const bool soon = ghost_link::begin_list();
        fake::advance(1000);
        const bool later = ghost_link::begin_list();
        const auto e = evidence();
        std::printf("shared_limit soon=%d later=%d attempts=%u successes=%u\n", int(soon), int(later),
                    e.reconstruct_attempts, e.reconstruct_successes);
    }
    std::printf("facts clean=%u structural=%u rdram=%u\n", clean_gate, structural_gate, rdram_gate);
    return 0;
}
