/* Setup worker only: the US dialogue font, kept on the player's own machine.
 * Whenever the annotation atlas is built from a verified US cartridge, its
 * font is saved to a per-user file; on a JP cartridge, names are written with
 * that font when the file holds it, else in JP's own capitals. The trainer
 * never ships the font: it exists only where his own US cartridge lent it.
 *
 * The file (version 1, little endian): magic "SM64UFNT", u32 version, u32
 * payload bytes, the payload's SHA-256, then the payload (UsFont: present,
 * then tiles). It is read only when every field holds and the payload's
 * digest is the cache's expected font (the US font, us_font_sha256), and
 * written whole to a temporary file that then replaces it. */
#pragma once
#include "ghost_annotation_assets.h"
#include <string>

namespace ghost_font_cache {
struct Cache {
    std::wstring file;   // absolute path; empty: no cache
    const char *digest;  // SHA-256 (hex) of the only font it may hold
};
/* %LOCALAPPDATA%\SM64Trainer\plugin-cache\us-dialogue-font.bin, for the US
 * font: per user, writable wherever Project64 is installed, and kept across
 * Project64 restarts and plugin reinstalls. Empty without LOCALAPPDATA. */
Cache user_cache();
bool read(const Cache &, ghost_annotation::UsFont &out);
/* Refuses a font whose digest is not the cache's; leaves an identical file. */
bool write(const Cache &, const ghost_annotation::UsFont &);
/* The atlas for this cartridge: borrows the cached font on JP, and saves a
 * US cartridge's font. A missing or unusable cache leaves JP's capitals. */
bool load(const Cache &, ghost_annotation::Assets &, const uint8_t *rom, uint32_t bytes);
}
