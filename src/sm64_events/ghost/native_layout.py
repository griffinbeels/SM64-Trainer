"""Byte layout and limits of the native ghost formats, in one place.

The C headers own these values (plugin/gfxwrap/ghost_capture.h, ghost_clip.h,
ghost_ipc.h, ghost_tuning.h). This module restates each ONCE for Python;
clip.py, native_protocol.py and the byte fixtures import it instead of writing
numbers of their own. tests/test_ghost_layout.py compiles
tests/native/ghost_layout_host.cpp, which prints every name below from
sizeof/offsetof and the C macros, and fails on any difference or on a name
here the host does not print.

Offsets ending in _AT are from the start of a portable record (gclip_frame),
whose sample begins after RECORD_PREFIX_BYTES; offsets inside an optional
block are named <BLOCK>_<FIELD>_AT and are relative to that block.
"""

# Versions and names
CLIP_MAGIC, CLIP_VERSION = b"SM64GHO5", 5
SAMPLE_VERSION = 6  # GC_VERSION: gc_sample, and the gc_layout it is read with
IPC_MAGIC, IPC_VERSION = b"SM64GIP7", 7
IPC_PREFIX = "Local\\sm64_trainer_ghost_v7_"

# Sizes
HEADER_BYTES, RECORD_BYTES, SAMPLE_BYTES = 160, 1268, 1252
RECORD_PREFIX_BYTES = 16  # bytes, version, configuration, world_status
LAYOUT_BYTES = 188
CLOCK_BYTES = 64
SHADOW_BYTES, SOUND_BYTES, PARTICLES_BYTES, PARTICLE_BYTES, HELD_BYTES = 292, 36, 580, 36, 36

# Record offsets
POSE_AT, ROOT_KIND_AT, REFUSAL_AT, ROOT_MATRIX_AT = 76, 128, 153, 156
CAMERA_AT, CAMERA_FOV_AT = 220, 248
WARP_AT, CLOCK_AT = 256, 260
SHADOW_AT, SOUND_AT, PARTICLES_AT, HELD_AT = 324, 616, 652, 1232

# Offsets inside the optional blocks
SHADOW_POSITION_AT, SHADOW_ALPHA_AT, SHADOW_RESERVED_AT, SHADOW_TEXTURE_AT = 64, 154, 163, 164
SOUND_BITS_AT = 4
PARTICLES_ENTRIES_AT, PARTICLE_OPACITY_AT, PARTICLE_TRANSLATION_AT = 4, 22, 24
HELD_HOLP_AT = 12

# Counts and flags
SHADOW_VERTICES, SHADOW_TEXTURE_BYTES = 9, 128
SOUND_EVENTS, SOUND_OVERFLOW = 8, 1
PARTICLE_EVENTS, PARTICLE_OVERFLOW, PARTICLE_OPACITY = 16, 1, 0x100
VALID_CLOCK, VALID_POSE, VALID_CAMERA, VALID_ACTION, VALID_WARP = 1, 2, 4, 8, 16
VALID_SHADOW, VALID_SOUND, VALID_PARTICLES, VALID_HELD = 32, 64, 128, 256
VALID_REQUIRED, VALID_ALL = 31, 511
MAX_RECORDS, WORLD_READY, DANCE_TAIL = 100000, 3, 1
STAR_CLOCK, SEGMENT_CLOCK = 1, 2
ROOT_TRS, ROOT_SCALED_MODELVIEW = 1, 2  # gc_root_kind

# GCLIP4 (GC5, before particles): still read, upgraded to the current record
V4_MAGIC, V4_VERSION, V4_SAMPLE_VERSION, V4_RECORD_BYTES, V4_SAMPLE_BYTES = b"SM64GHO4", 4, 5, 652, 636

# The IPC page and ghost limits
IPC_SLOTS, IPC_RING_AT, IPC_PATH_CHARS = 512, 20480, 520
GHOSTS = 16  # displayed ghosts at once: playback, compositor batch, sound sources, request slots
PROXIMITY_MAX = 400.0  # the largest overlap-fade distance a request may carry
BRIDGE_FRAMES = 2  # the longest refused run playback holds the previous pose over
