#include "ghost_clock.h"
#include <cstring>
#include <limits>

namespace {
bool contains(const uint32_t* values,uint32_t count,uint32_t value) {
    for(uint32_t i=0;i<count;++i)if(values[i]==value)return true;
    return false;
}
bool config_valid(const gcc_config& c) {
    if(c.version!=GCC_VERSION || !c.teleport_action || c.near_zero>65535
        || !c.wrap_ceiling || c.wrap_ceiling>65535 || c.wrap_ceiling<=c.near_zero
        || !c.castle_count || c.castle_count>GCC_LIST_MAX || !c.warp_count || c.warp_count>GCC_LIST_MAX)
        return false;
    const uint32_t windows[]={c.area_window,c.teleport_window,c.level_tail,c.warp_window};
    for(auto value:windows)if(!value || value>1000000)return false;
    for(uint32_t i=0;i<GCC_LIST_MAX;++i) {
        if(i<c.castle_count ? c.castle_levels[i]<4 || c.castle_levels[i]>36 : c.castle_levels[i]!=0)return false;
        if(i<c.warp_count ? !c.warp_ops[i] || c.warp_ops[i]>65535 : c.warp_ops[i]!=0)return false;
    }
    return true;
}
void clear_history(gcc_clock& s) {
    s.previous={};s.have_previous=0;s.trusted=0;s.banked=0;s.history_start=0;
    s.elapsed=0;s.have_elapsed=0;
    s.have_area=s.have_teleport=s.have_warp=s.have_level=s.awaiting_level_zero=0;
}
bool advance(gcc_clock& s,bool origin) {
    if(s.exhausted)return false;
    if(s.leg==UINT64_MAX || (origin && s.origin==UINT64_MAX)){s.exhausted=1;s.trusted=0;return false;}
    ++s.leg;if(origin)++s.origin;return true;
}
bool recent(uint32_t now,uint32_t then,uint32_t window){return now-then<=window;}
void elapsed_start(gcc_clock& s) {
    if(s.continuity==UINT64_MAX){s.exhausted=1;s.trusted=0;s.have_elapsed=0;return;}
    ++s.continuity;s.elapsed=0;s.have_elapsed=1;
}
void elapsed_add(gcc_clock& s,uint32_t frames) {
    if(s.elapsed>UINT64_MAX-frames){s.exhausted=1;s.trusted=0;s.have_elapsed=0;return;}
    s.elapsed+=frames;
}
uint32_t history_window(const gcc_config& c) {
    uint32_t value=c.area_window;
    if(value<c.teleport_window)value=c.teleport_window;
    if(value<c.level_tail)value=c.level_tail;
    if(value<c.warp_window)value=c.warp_window;
    return value;
}
void observe(gcc_clock& s,const gcc_input& in) {
    if(in.level!=s.previous.level) {
        s.have_area=s.have_teleport=s.have_warp=0;s.banked=0;
        s.have_level=1;s.level_frame=in.global_timer;return;
    }
    if(in.area!=s.previous.area){s.have_area=1;s.area_frame=in.global_timer;s.entered_area=uint32_t(in.area);}
    if(in.action==s.config.teleport_action){s.have_teleport=1;s.teleport_frame=in.global_timer;}
    if(contains(s.config.warp_ops,s.config.warp_count,in.warp_op)
        && s.previous.warp_op==in.warp_op && s.previous.warp_timer>0 && in.warp_timer==0) {
        s.have_warp=1;s.warp_frame=in.global_timer;
    }
}
bool area_load(const gcc_clock& s,const gcc_input& in) {
    if(contains(s.config.castle_levels,s.config.castle_count,uint32_t(in.level)))return false;
    if(s.have_warp && recent(in.global_timer,s.warp_frame,s.config.warp_window))return true;
    if(!s.have_area)return false;
    if(s.have_level && s.area_frame-s.level_frame<=s.config.level_tail)return false;
    return s.entered_area!=1 && recent(in.global_timer,s.area_frame,s.config.area_window);
}
uint32_t finish(gcc_clock& s,const gcc_input& in,gcc_output& out,uint32_t refusal) {
    s.previous=in;s.have_previous=1;
    out.valid=GCC_RAW_VALID;out.raw_igt=in.raw_igt;out.leg_id=s.leg;out.origin_id=s.origin;
    if(s.exhausted)refusal|=GCC_EXHAUSTED;
    if(!s.trusted)refusal|=GCC_UNKNOWN_ORIGIN;
    if(s.trusted && !refusal) {
        if(s.banked>UINT64_MAX-in.raw_igt){s.exhausted=1;s.trusted=0;refusal|=GCC_EXHAUSTED;}
        else {out.valid|=GCC_PROGRESS_VALID;out.banked_frames=s.banked;
            out.progress_frames=s.banked+in.raw_igt;}
    }
    if(s.have_elapsed && !s.exhausted) {
        out.valid|=GCC_ELAPSED_VALID;out.elapsed_frames=s.elapsed;out.continuity_id=s.continuity;
    }
    out.refusal=refusal;return refusal;
}
}
extern "C" int gcc_init(gcc_clock* state,const gcc_config* config) {
    if(!state)return 0;const gcc_config copy=config?*config:gcc_config{};
    *state={};if(!config_valid(copy))return 0;
    state->config=copy;state->initialized=GCC_VERSION;return 1;
}
extern "C" void gcc_reset(gcc_clock* state) {
    if(state && state->initialized==GCC_VERSION){clear_history(*state);state->last_sequence=0;}
}
extern "C" uint32_t gcc_step(gcc_clock* state,const gcc_input* input,gcc_output* out) {
    if(!out)return GCC_BAD_INPUT;*out={};
    if(!state || !input || state->initialized!=GCC_VERSION)return out->refusal=GCC_BAD_INPUT;
    auto& s=*state;const gcc_input in=*input;
    if(s.exhausted)return out->refusal=GCC_EXHAUSTED;
    if(!in.epoch || !in.sequence) {
        clear_history(s);return out->refusal=GCC_BAD_INPUT;
    }
    if(in.sequence<=s.last_sequence)
        return out->refusal=GCC_REPEATED;
    s.last_sequence=in.sequence;
    if(in.fields&~GCC_REQUIRED){clear_history(s);return out->refusal=GCC_BAD_INPUT;}
    if(in.fields!=GCC_REQUIRED){clear_history(s);return out->refusal=GCC_INCOMPLETE;}
    if(in.raw_igt>65535 || in.level<4 || in.level>36 || in.area<1 || in.area>8
        || in.warp_op>65535 || in.warp_timer<-32768 || in.warp_timer>32767) {
        clear_history(s);return out->refusal=GCC_BAD_INPUT;
    }
    const bool explicit_origin=in.origin_epoch || in.origin_sequence;
    if(explicit_origin && (in.origin_epoch!=in.epoch || in.origin_sequence!=in.sequence || in.raw_igt)) {
        clear_history(s);return out->refusal=GCC_BAD_INPUT;
    }
    uint32_t refusal=0;
    if(s.have_previous) {
        if(in.epoch!=s.previous.epoch || in.global_timer<s.previous.global_timer) {
            clear_history(s);refusal=GCC_REWIND;
        } else if(in.sequence-s.previous.sequence!=1) {clear_history(s);refusal=GCC_SEQUENCE_GAP;}
        else if(in.global_timer-s.previous.global_timer>1) {clear_history(s);refusal=GCC_FRAME_GAP;}
    }
    if(explicit_origin) {
        // A newly confirmed zero can restart star progress while an arbitrary
        // elapsed reference keeps spanning the same observed route.
        const auto elapsed=s.elapsed;const bool had_elapsed=s.have_elapsed!=0;
        clear_history(s);s.elapsed=elapsed;s.have_elapsed=had_elapsed;
        if(!s.have_elapsed)elapsed_start(s);
        s.history_start=in.global_timer;s.trusted=advance(s,true);
        out->transition=GCC_ORIGIN;return finish(s,in,*out,0);
    }
    if(!s.have_previous) {
        s.history_start=in.global_timer;advance(s,false);
        elapsed_start(s);
        out->transition=GCC_UNKNOWN;return finish(s,in,*out,refusal);
    }
    const bool level_changed=in.level!=s.previous.level;
    observe(s,in);out->transition=level_changed?GCC_LEVEL:GCC_STEADY;
    if(level_changed) {
        s.awaiting_level_zero=1;s.trusted=in.raw_igt==0;
        advance(s,true);
    }
    if(in.raw_igt<s.previous.raw_igt && in.raw_igt<=s.config.near_zero) {
        const bool area=area_load(s,in);
        const bool teleport=s.have_teleport && recent(in.global_timer,s.teleport_frame,s.config.teleport_window);
        if(area || teleport) {
            if(s.banked>UINT64_MAX-s.previous.raw_igt){s.exhausted=1;s.trusted=0;}
            else s.banked+=s.previous.raw_igt;
            advance(s,false);out->transition=area?GCC_AREA_WARP:GCC_TELEPORT;
        } else {
            const bool established=s.trusted || s.awaiting_level_zero
                || in.global_timer-s.history_start>=history_window(s.config);
            s.banked=0;s.trusted=established;advance(s,true);
            if(!level_changed)out->transition=GCC_RETRY;
        }
        s.have_warp=0;s.awaiting_level_zero=0;
        if(s.previous.raw_igt>=s.config.wrap_ceiling) {
            s.trusted=0;refusal|=GCC_COUNTER_WRAP;
        }
    } else if(in.raw_igt<s.previous.raw_igt && !level_changed) {
        s.trusted=0;refusal|=GCC_DISCONTINUITY;out->transition=GCC_UNKNOWN;
    }
    if(in.raw_igt>=s.previous.raw_igt)elapsed_add(s,in.raw_igt-s.previous.raw_igt);
    else if(!(refusal&(GCC_COUNTER_WRAP|GCC_DISCONTINUITY)) && in.raw_igt<=s.config.near_zero)
        elapsed_add(s,in.raw_igt);
    else elapsed_start(s);
    return finish(s,in,*out,refusal);
}
