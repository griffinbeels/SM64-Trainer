/* Setup-only desktop dispatch shared by native ghost renderer components. */
#pragma once
#include "ghost_compositor.h"
namespace ghost_dispatch {
// Requires the current renderer context AFTER replay_gl::InstallDispatch.
// Observed mutations retain the installed observation wrappers.
bool load(ghost_compositor::Dispatch&);
}
