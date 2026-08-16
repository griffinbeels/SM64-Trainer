"""The behaviour SYMBOL is the identity; the pointer is per version."""
from sm64_events.memory import behaviours as B


def test_both_catalogues_load_and_have_the_measured_sizes():
    # STROOP MappingUS.map / MappingJP.map @ dev, fetched 2026-08-15: every
    # symbol the linker placed in segment 0x13, section markers excluded.
    assert len(B.offsets("us")) == 536
    assert len(B.offsets("jp")) == 532
    assert "bhvPlaysMusicTrackWhenTouched" in B.symbols("us")
    assert "bhvPlaysMusicTrackWhenTouched" not in B.symbols("jp")


def test_offsets_differ_between_versions_so_the_symbol_is_the_identity():
    assert B.offsets("us")["bhvBobomb"] == 0x13003174
    assert B.offsets("jp")["bhvBobomb"] == 0x13003154
    assert B.offsets("us")["bhvBlueCoinSwitch"] == B.offsets("jp")["bhvBlueCoinSwitch"]


def test_us_pointer_roundtrip_through_the_shipped_base():
    assert B.pointer_of("us", "bhvBobomb") == 0x800EE2F4
    assert B.symbol_of("us", 0x800EE2F4) == "bhvBobomb"
    assert B.symbol_of("us", 0x800EBC8C) == "bhvDoor"
    assert B.symbol_of("us", 0x800EBC7C) == "bhvDoorWarp"


def test_unknown_pointer_keys_as_ptr_and_jp_without_a_base_too():
    assert B.symbol_of("us", 0x12345678) == "ptr_12345678"
    assert B.symbol_of("jp", 0x80000000) == "ptr_80000000"
    assert B.pointer_of("jp", "bhvBobomb") is None
    assert B.pointer_of("jp", "bhvBobomb", base=0x800EB000) == 0x800EB000 + 0x3154
    assert B.pointer_of("us", "bhvNotAThing") is None


def test_base_from_marios_own_behaviour():
    assert B.base_from_mario("us", 0x800EB180 + 0x2EC0) == 0x800EB180
    assert B.base_from_mario("jp", 0x800EA000 + 0x2EA0) == 0x800EA000


def test_globals_map_names_every_layout_symbol():
    from sm64_events.memory.layout import LAYOUT_ROWS
    for version in ("us", "jp"):
        have = B.globals_map(version)
        for row in LAYOUT_ROWS:
            if row.symbol:
                assert row.symbol in have, (version, row.symbol)
    assert B.globals_map("us")["gGlobalTimer"] == 0x8032D5D4
    assert B.globals_map("jp")["gGlobalTimer"] == 0x8032C694


def test_the_us_globals_map_agrees_with_the_us_layout():
    """The map is the derivation the layout claims for every US symbol row;
    the layout's US values are what was live-verified. They must agree, or one
    of them is wrong."""
    from sm64_events.memory.layout import LAYOUT_ROWS, US
    have = B.globals_map("us")
    for row in LAYOUT_ROWS:
        if row.symbol:
            assert US.value(row.field) == have[row.symbol], row.field


def test_kind_names_ship_the_same_shape_the_catalogue_did():
    names = dict(B.kind_names("us"))
    assert names["bhvBobomb"] == "bob-omb"
    assert names["bhvWhompKingBoss"] == "Whomp King"
    assert names["bhvChainChomp"] == "chain chomp"
    assert not any(s.startswith("bhvUnused") for s in names)
    assert not any(not s.startswith("bhv") for s in names)
