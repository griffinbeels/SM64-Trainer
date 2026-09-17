/* Renderer-owned state witness. Feed actual successful mutations, never cached
 * intent or guessed postconditions. No GL calls, allocations or driver queries. */
#pragma once
#include "gl_snapshot.h"

namespace renderer {
struct Identity {
    uintptr_t context, read_drawable;
    uint32_t context_generation, drawable_generation, owner_thread;
};
class State {
public:
    static constexpr unsigned texture_units = 32, deletion_limit = 64;
    // Only a newly CREATED current context earns GL's initial-state defaults.
    void created(Identity identity, bool double_buffered) {
        identity_ = identity;
        const bool valid = identity.context && identity.read_drawable && identity.context_generation
            && identity.drawable_generation && identity.owner_thread;
        read_ = unit_ = 0; default_read_ = double_buffered ? GL_BACK : GL_FRONT;
        read_known_ = unit_known_ = default_known_ = valid;
        known_textures_ = valid ? ~0u : 0;
        for (auto &texture : textures_) texture = 0;
    }
    void lost() { identity_ = {}; read_known_ = unit_known_ = default_known_ = false;
                  known_textures_ = 0; }
    // Same context/drawable, storage resized: GL binding state survives. The
    // source generation changes without claiming a freshly initialized context.
    void drawable_changed(uint32_t generation) {
        if (!generation || generation <= identity_.drawable_generation) { lost(); return; }
        identity_.drawable_generation = generation;
    }
    void bind_framebuffer(GLenum target, GLuint name) {
        if (target == 0x8D40 || target == 0x8CA8) { read_ = name; read_known_ = true; }
    }
    void read_buffer(GLenum mode) {
        // Selectors belong to FBOs. Other FBO selectors never overwrite default.
        if (!read_known_) { default_known_ = false; return; }
        if (read_ == 0) { default_read_ = mode; default_known_ = true; }
    }
    void active_texture(GLenum unit) {
        unit_ = unit >= 0x84C0 ? unit - 0x84C0 : texture_units;
        unit_known_ = unit_ < texture_units;
    }
    void bind_texture(GLenum target, GLuint name) {
        if (target != GL_TEXTURE_2D) return;
        if (!unit_known_) { known_textures_ = 0; return; }
        textures_[unit_] = name; known_textures_ |= 1u << unit_;
    }
    void delete_textures(GLsizei count, const GLuint *names) {
        if (count < 0 || unsigned(count) > deletion_limit || (count && !names)) {
            known_textures_ = 0; return; // bound extra bookkeeping even on bulk deletion
        }
        for (unsigned u = 0; u < texture_units; ++u)
            for (GLsizei n = 0; n < count; ++n)
                if (names[n] && textures_[u] == names[n]) textures_[u] = 0;
    }
    void delete_framebuffers(GLsizei count, const GLuint *names) {
        if (count < 0 || unsigned(count) > deletion_limit || (count && !names)) {
            read_known_ = false; return;
        }
        for (GLsizei n = 0; n < count; ++n)
            if (names[n] && read_known_ && read_ == names[n]) read_ = 0;
    }
    void named_read_buffer(GLuint framebuffer, GLenum mode) {
        if (!framebuffer) { default_read_ = mode; default_known_ = true; }
    }
    bool default_bindings(Identity identity, snapshot::RestoreBindings *out) const {
        if (!out || !identity.context || !identity.read_drawable || !identity.context_generation
                || !identity.drawable_generation || !identity.owner_thread
                || identity.context != identity_.context
                || identity.read_drawable != identity_.read_drawable
                || identity.context_generation != identity_.context_generation
                || identity.drawable_generation != identity_.drawable_generation
                || identity.owner_thread != identity_.owner_thread
                || !read_known_ || !default_known_ || !unit_known_
                || !(known_textures_ & (1u << unit_))) return false;
        *out = {read_, textures_[unit_], default_read_};
        return true;
    }
    // Any unobserved mutation/error invalidates the witness until reconstructed.
    void unknown() { read_known_ = unit_known_ = default_known_ = false;
                     known_textures_ = 0; }
private:
    Identity identity_{};
    uint32_t known_textures_ = 0;
    GLuint read_ = 0, textures_[texture_units]{};
    GLenum default_read_ = GL_NONE;
    unsigned unit_ = 0;
    bool read_known_ = false, unit_known_ = false, default_known_ = false;
};
} // namespace renderer
