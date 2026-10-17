"""A JP Layout for tests of the native ghost path, built from candidates.

memory/layout.py's JP row stays empty until each address passes its live gate
(.claude/rules/sync.md), so these are NOT verified addresses. The first group
was derived statically from SM64_USAMUNE_v1.93j.z64 and agrees with STROOP's
JP map (data/symbols_jp.tsv); the warp pair is that map's. behaviour_base and
usamune_overall have no JP candidate yet: FIXTURE values, only somewhere a
byte fixture can plant objects and a clock.
"""
from sm64_events.memory.layout import Layout

JP_CANDIDATE = Layout(
    version="jp",
    global_timer=0x8032C694,
    mario_struct=0x80339E00,
    curr_level=0x8032CE98,
    curr_area=0x8033A75A,
    object_pool=0x8033C118,
    mario_object=0x8035FDE8,
    current_area_pointer=0x8032CE6C,
    area_update_counter=0x8032CFA8,
    sound_code=0x8031DC78,
    sound_producer=0x80331E34,
    sound_consumer=0x80331E30,
    sound_ring=0x80360128,
    player1_controller=0x80339C20,
    pending_warp_op=0x80339EE2,
    delayed_warp_timer=0x80339EE4,
    usamune_overall=0x80417C72,  # FIXTURE: the US address; JP's is unknown
    behaviour_base=0x800EA000,   # FIXTURE: JP's segment 0x13 base is unknown
)
