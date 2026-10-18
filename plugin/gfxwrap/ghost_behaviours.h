/* The behaviours ghost capture and playback recognise, by decomp symbol, at
 * their segmented offset (segment 0x13) in each ROM layout. The symbol is the
 * identity, never the offset: JP moves four of these (memory/behaviours.py).
 * tests/test_ghost_behaviours.py checks every offset below against
 * src/sm64_events/data/behaviours_{us,jp}.tsv by symbol; keep one row per line.
 *
 * A clip records the layout it was captured on (gclip_header.rom_layout), so
 * its held behaviour is named through that layout whichever cartridge runs.
 *
 * animations: the non-NULL entry count of the object's animation table in the
 * pinned n64decomp/sm64 actor tables at 06ec56df7f951f88da05f468cdcacecba496145a,
 * matching our model inputs. Tables are not uniformly terminated (Bob-omb:
 * then NULL; Mips: then two NULLs; Hoot, penguin, King Bob-omb, Chuckya: no
 * terminator; penguin aliases entries 0 and 4). 0: no animation (both boxes,
 * the shell), or an unknown behaviour, which has no proven bound and cannot
 * supply an animation ID. */
#pragma once
#include "ghost_capture.h"
#include <stdint.h>

namespace ghost_behaviours {
enum class Symbol : uint32_t {
    none, bobomb, small_penguin, king_bobomb, chuckya, breakable_box_small,
    jumping_box, mips, hoot, koopa_shell, ukiki, macro_ukiki
};
struct Row { Symbol symbol; const char *name; uint32_t offset[2]; uint32_t animations; };
constexpr uint32_t segment = 0x13000000u;
constexpr Row rows[] = {
    {Symbol::bobomb, "bhvBobomb", {0x13003174u, 0x13003154u}, 2},
    {Symbol::small_penguin, "bhvSmallPenguin", {0x130020e8u, 0x130020e8u}, 5},
    {Symbol::king_bobomb, "bhvKingBobomb", {0x130001f4u, 0x130001f4u}, 12},
    {Symbol::chuckya, "bhvChuckya", {0x13000528u, 0x13000528u}, 6},
    {Symbol::breakable_box_small, "bhvBreakableBoxSmall", {0x13004218u, 0x130041f8u}, 0},
    {Symbol::jumping_box, "bhvJumpingBox", {0x13001650u, 0x13001650u}, 0},
    {Symbol::mips, "bhvMips", {0x130044fcu, 0x130044dcu}, 5},
    {Symbol::hoot, "bhvHoot", {0x130033ecu, 0x130033ccu}, 2},
    {Symbol::koopa_shell, "bhvKoopaShell", {0x13001f3cu, 0x13001f3cu}, 0},
    {Symbol::ukiki, "bhvUkiki", {0x13000f08u, 0x13000f08u}, 13},
    {Symbol::macro_ukiki, "bhvMacroUkiki", {0x13001cb0u, 0x13001cb0u}, 13},
};
constexpr bool supported_layout(uint32_t rom_layout) {
    return rom_layout == GC_ROM_US || rom_layout == GC_ROM_JP;
}
// Which supported behaviour a segmented script names on that layout.
constexpr Symbol symbol(uint32_t rom_layout, uint32_t segmented) {
    if (!supported_layout(rom_layout) || !segmented) return Symbol::none;
    for (const Row &row : rows)
        if (row.offset[rom_layout - GC_ROM_US] == segmented) return row.symbol;
    return Symbol::none;
}
// The symbol's segmented script on that layout; 0 when either is unknown.
constexpr uint32_t offset(uint32_t rom_layout, Symbol name) {
    if (!supported_layout(rom_layout)) return 0;
    for (const Row &row : rows) if (row.symbol == name) return row.offset[rom_layout - GC_ROM_US];
    return 0;
}
constexpr uint32_t animations(Symbol name) {
    for (const Row &row : rows) if (row.symbol == name) return row.animations;
    return 0;
}
constexpr bool rows_in_order() {
    for (uint32_t i = 0; i < sizeof(rows) / sizeof(rows[0]); ++i)
        if (static_cast<uint32_t>(rows[i].symbol) != i + 1) return false;
    return sizeof(rows) / sizeof(rows[0]) == static_cast<uint32_t>(Symbol::macro_ukiki);
}
static_assert(rows_in_order(), "one row per symbol, in enum order");
static_assert(GC_ROM_JP == GC_ROM_US + 1, "rows index offsets by layout");
}
