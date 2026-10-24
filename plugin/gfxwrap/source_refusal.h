/* Diagnostic-only failed-surface witness. No SourceV2 layout or admission rule
 * changes. Successful pictures retain their existing witness semantics.
 * A code identifies the observed gate, not the underlying driver cause. */
#pragma once
#include <stdint.h>
enum rs_surface_refusal : uint32_t {
    RS_SURFACE_BINDINGS=0x53520001u,
    RS_SURFACE_FORMAT=0x53520002u,
    RS_SURFACE_EXTENT=0x53520003u,
    RS_SURFACE_GEOMETRY=0x53520004u,
    RS_SURFACE_VI_REGISTER=0x53520005u,
    RS_SURFACE_CROP=0x53520006u,
    RS_SURFACE_NOT_OBSERVED=0x53520007u
};
inline bool rs_surface_refusal_valid(uint32_t value) {
    return value>=RS_SURFACE_BINDINGS && value<=RS_SURFACE_NOT_OBSERVED;
}

/* THE BINDINGS CAUSE (docs/replay-link-state.md): why CandidateBindings was
 * false when a BINDINGS refusal happened. The renderer writes already-known
 * values; it makes no query for them. A BINDINGS refusal carries it in the
 * refused surface's unused bytes (link_source_api.h). Zero means an older
 * renderer: unknown, never "nothing went wrong". Python names the values
 * (replay/gpudiagnostics.py). Append values; never renumber them. */
enum rs_bindings_site : uint32_t {
    RS_BINDINGS_SITE_NONE=0,              // no invalidation since the context was created
    RS_BINDINGS_FRAMEBUFFER_TARGET=1,     // BindFramebuffer: a target other than FRAMEBUFFER/READ/DRAW
    RS_BINDINGS_FRAMEBUFFER_DELETE=2,     // DeleteFramebuffers: count/names beyond the bounded bookkeeping
    RS_BINDINGS_TEXTURE_UNIT=3,           // ActiveTexture: a unit outside this context's units
    RS_BINDINGS_VERTEX_ARRAY_DELETE=4,    // DeleteVertexArrays: negative count or null names
    RS_BINDINGS_DRAWABLE_CHANGED=5,       // a resize from another thread, or drawable generations exhausted
    RS_BINDINGS_DRAWABLE_COMMITTED=6,     // the client rectangle was unavailable or empty after a resize
    RS_BINDINGS_DISPATCH_REINSTALLED=7,   // InstallDispatch found its own hooks already installed
    RS_BINDINGS_DISPATCH_INCOMPLETE=8,    // a framebuffer or active-texture entry point was missing
    RS_BINDINGS_DISPATCH_UNARMED=9,       // InstallDispatch ran while the observation was not armed
    RS_BINDINGS_DISPATCH_NOT_CURRENT=10,  // InstallDispatch ran with another context or drawable current
    RS_BINDINGS_READ_BUFFER=11,           // ReadBuffer: a selector outside the legal subset
    RS_BINDINGS_TEXTURE_DELETE=12,        // DeleteTextures: count/names beyond the bounded bookkeeping
    RS_BINDINGS_RENDERER_ERROR=13,        // the renderer's own glGetError read returned an error
    RS_BINDINGS_GATEWAY_OPEN_ERROR=14,    // BeginCapture found a GL error already pending
    RS_BINDINGS_GATEWAY_CLOSE_ERROR=15,   // EndCapture found a GL error after the capture work
    RS_BINDINGS_LAYER_FATAL_ERROR=16,     // EndLayer: out of memory or a lost context
    RS_BINDINGS_LAYER_UNDRAINED=17,       // EndLayer could not drain the GL error flag
    RS_BINDINGS_CREATED_NOT_CURRENT=18,   // ContextCreated: null handles, or not the current ones
    RS_BINDINGS_CREATED_EXHAUSTED=19,     // ContextCreated: context generations exhausted
    RS_BINDINGS_CREATED_NO_UNITS=20       // ContextCreated: the driver reported no texture units
};
enum rs_bindings_flag : uint32_t {
    RS_BINDINGS_REPORTED=1u,              // this renderer filled the record
    RS_BINDINGS_CONTEXT_CREATED=2u,       // ContextCreated ran since the last context loss
    RS_BINDINGS_PRACTICE_ROM=4u,          // ...for a practice ROM (practice_rom.h)
    RS_BINDINGS_ARMED=8u,                 // ...and armed the observation
    RS_BINDINGS_INSTALL_CALLED=16u,       // InstallDispatch ran for this context
    RS_BINDINGS_DISPATCH_INSTALLED=32u,   // the observed dispatch belongs to this context
    RS_BINDINGS_NOMINAL=64u,              // the observation was still nominal at the refusal
    RS_BINDINGS_OWNER_THREAD=128u,        // the refusal ran on the context's owner thread
    RS_BINDINGS_CONTEXT_LOST=256u,        // a context was lost and none created since
    RS_BINDINGS_STATE_IDENTITY=512u,      // the state witness holds this context's identity
    RS_BINDINGS_READ_KNOWN=1024u,         // read framebuffer binding known
    RS_BINDINGS_DEFAULT_READ_KNOWN=2048u, // default framebuffer read selector known
    RS_BINDINGS_UNIT_KNOWN=4096u,         // active texture unit known
    RS_BINDINGS_TEXTURE_KNOWN=8192u       // active unit's 2D texture known
};
typedef struct {
    uint32_t site;          // rs_bindings_site: the FIRST invalidation since ContextCreated
    uint32_t gl_error;      // the GL error read at that site; 0 when the site read none
    uint32_t flags;         // rs_bindings_flag at the refusal
    uint32_t invalidations; // invalidations since ContextCreated, saturating
    uint32_t after_ms;      // ms from ContextCreated to the first invalidation, saturating
    uint32_t contexts;      // ContextCreated calls in this process, saturating
} rs_bindings_cause;
static_assert(sizeof(rs_bindings_cause)==24, "bindings cause layout");

/* The GL identity of the renderer's practice-ROM context, read by the renderer
 * on its own thread: at ContextCreated, or once at the first bindings refusal
 * of a context ContextCreated never saw. SM64ReplayContextEvidenceV1 copies it
 * for the wrapper without GL. Bounded printable ASCII, NUL-padded. */
#define RS_CONTEXT_EVIDENCE_V1 1u
enum rs_context_identity_source : uint32_t {
    RS_IDENTITY_NONE=0, RS_IDENTITY_CONTEXT_CREATED=1, RS_IDENTITY_FIRST_REFUSAL=2
};
typedef struct rs_context_evidence {
    uint32_t bytes, version, source, reserved;
    char vendor[80], renderer[128], gl_version[96];
} rs_context_evidence;
static_assert(sizeof(rs_context_evidence)==320, "context evidence layout");
typedef int (__cdecl *rs_read_context_evidence)(rs_context_evidence *, uint32_t bytes);
/* Printable ASCII only (anything else becomes '?'), at most capacity-1 bytes,
 * the rest zero. A null source leaves an empty string. */
inline void rs_bounded_text(char *out, unsigned capacity, const unsigned char *in) {
    unsigned n = 0;
    if (in) for (; n + 1 < capacity && in[n]; ++n) out[n] = in[n] >= 0x20 && in[n] < 0x7F ? char(in[n]) : '?';
    for (; n < capacity; ++n) out[n] = 0;
}
