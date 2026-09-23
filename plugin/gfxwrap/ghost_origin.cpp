#include "ghost_origin.h"
#include "ghost_capture.h"
#include <cmath>
#include <cstring>

namespace ghost_origin {
namespace {
// A loaded state's recorded root is not bit-identical to the frame it was
// saved on: the root is read back through the camera's fixed-point matrices,
// and the camera after a load is not the one that drew the save. His WF 100
// Coins loads (2026-09-23, 7 loads of one IGT-296 state) drew Mario up to 7.4
// units from the witnessed save frame, which an exact compare never matched,
// so every load hid the ghost. The action, animation and angles still match
// exactly; the root must agree within GO_RESTORE_PLACE units per axis.
bool same_place(const float (&a)[3],const float (&b)[3]) {
    for(unsigned axis=0;axis<3;++axis)
        if(!(std::fabs(a[axis]-b[axis])<=GO_RESTORE_PLACE))return false;
    return true;
}
}
void Ledger::clear() {
    configuration_ = epoch_ = count_ = next_ = 0;
    sequence_ = 0;
}
void Ledger::add(uint32_t configuration, const gc_sample &sample) {
    if (!configuration || !sample.epoch || !sample.sequence
            || sample.bytes != sizeof(sample) || sample.version != GC_VERSION) return;
    if (sample.sequence <= sequence_) return;
    if (configuration != configuration_ || sample.epoch != epoch_) {
        clear(); configuration_ = configuration; epoch_ = sample.epoch;
    }
    sequence_ = sample.sequence;
    entries_[next_] = {sample.global_timer, sample.igt, sample.valid_fields,
        sample.clock.valid, sample.qpc, sample.sequence, sample.clock.continuity_id,
        sample.clock.elapsed_frames, sample.clock.origin_id};
    next_ = (next_ + 1) % GO_HISTORY;
    if (count_ < GO_HISTORY) ++count_;
}
go_result Ledger::resolve(const go_marker &marker, uint64_t frequency,
                         int64_t anchor, double utc, uint32_t near_zero,
                         go_origin &out) const {
    out = {};
    if ((marker.kind != GO_STAR && marker.kind != GO_SEGMENT)
            || !marker.configuration || !marker.epoch || !marker.session_id || !marker.arm_id
            || !std::isfinite(marker.utc) || marker.utc <= 0
            || !frequency || anchor < 0 || !std::isfinite(utc) || utc <= 0) return GO_INVALID;
    if (marker.configuration != configuration_ || marker.epoch != epoch_) return GO_MISSING;
    const Entry *first = nullptr;
    for (uint32_t i = 0; i < count_; ++i) {
        const Entry &entry = entries_[(next_ + GO_HISTORY - count_ + i) % GO_HISTORY];
        if (entry.timer != marker.global_timer) continue;
        const double captured = utc + (static_cast<double>(entry.qpc) - static_cast<double>(anchor))
            / static_cast<double>(frequency);
        if (std::abs(captured - marker.utc) > GO_UTC_WINDOW) continue;
        if (!first) first = &entry;
        else if (entry.continuity != first->continuity || entry.elapsed != first->elapsed
                || entry.raw != first->raw || entry.star_generation != first->star_generation
                || entry.clock_valid != first->clock_valid
                || (entry.valid & GC_VALID_CLOCK) != (first->valid & GC_VALID_CLOCK)) return GO_AMBIGUOUS;
    }
    if (!first) return GO_MISSING;
    // The first occurrence owns this frame even when it was refused. A later
    // valid pose or clock must never make that original refusal disappear.
    const uint32_t required = GCC_RAW_VALID | GCC_ELAPSED_VALID;
    if (!(first->valid & GC_VALID_CLOCK) || (first->clock_valid & required) != required || !first->continuity
            || (marker.kind == GO_STAR && first->raw > near_zero)) return GO_INVALID;
    const uint32_t zero = marker.kind == GO_SEGMENT && first->raw <= near_zero ? GO_ZERO_CLOCK : 0;
    out = {marker.kind, marker.epoch, first->sequence, first->continuity,
        first->elapsed, first->star_generation, marker.kind == GO_STAR ? first->raw : 0, zero};
    return GO_READY;
}

void Recovery::clear() {
    history_.clear();next_=0;origin_={};latest_={};restoration_={};
    pending_=ended_=absolute_timer_=direct_=direct_known_=false;direct_offset_=0;
    first_level_=first_area_=0;
}
void Recovery::suspend(bool absolute_timer) {if(bound()){pending_=true;absolute_timer_=absolute_timer;}}
bool Recovery::coordinate(const Checkpoint &s,uint32_t &out) const {
    if(!bound() || s.epoch!=origin_.epoch || s.continuity!=origin_.continuity
            || s.sequence<origin_.sequence || s.elapsed<origin_.elapsed)return false;
    const auto delta=s.elapsed-origin_.elapsed;
    if(delta>UINT32_MAX-origin_.offset)return false;
    out=origin_.offset+uint32_t(delta);return true;
}
void Recovery::remember(const Checkpoint &value) {
    auto copy=value;if(!coordinate(copy,copy.coordinate))return;
    const int64_t relation=int64_t(copy.coordinate)-int64_t(copy.raw);
    if(!direct_known_) {
        direct_known_=true;direct_offset_=relation;first_level_=copy.level;first_area_=copy.area;
        direct_=origin_.kind==GO_STAR ? relation==0 : (origin_.flags&GO_ZERO_CLOCK)!=0;
    } else if(relation!=direct_offset_ || copy.level!=first_level_ || copy.area!=first_area_)
        direct_=false;
    // Repeated display lists/pause observations must not evict a long run.
    if(!history_.empty()) {
        const auto &last=history_[(next_+uint32_t(history_.size())-1)%history_.size()];
        if(last.timer==copy.timer && last.raw==copy.raw && last.coordinate==copy.coordinate
                && last.level==copy.level && last.area==copy.area)return;
    }
    if(history_.size()<limit) {
        if(history_.empty())history_.reserve(limit);
        history_.push_back(copy);next_=uint32_t(history_.size())%limit;
    } else {history_[next_]=copy;next_=(next_+1)%limit;}
}
void Recovery::bind(const go_origin &origin,bool same_run) {
    if(!(same_run && bound())) {history_.clear();next_=0;}
    origin_=origin;restoration_={};
    pending_=ended_=absolute_timer_=false;
    first_level_=latest_.level;first_area_=latest_.area;
    direct_=direct_known_=false;direct_offset_=0;
    remember(latest_);
}
bool Recovery::current(go_origin &out) const {
    if(!bound() || pending_)return false;
    out=origin_;return true;
}
bool Recovery::restored_near(const go_marker &marker,uint64_t frequency,int64_t anchor,double utc) const {
    if(!restoration_.sequence || !frequency || !bound() || pending_
            || marker.kind!=origin_.kind || marker.epoch!=restoration_.epoch)return false;
    const double captured=utc+(double(restoration_.qpc)-double(anchor))/double(frequency);
    // This only hands a confirmed restoration to its new tracker arm. It never
    // chooses a neighboring coordinate: the checkpoint already chose that.
    return std::abs(captured-marker.utc)<=GO_UTC_WINDOW
        && std::abs(int64_t(marker.global_timer)-int64_t(restoration_.timer))<=2;
}
go_result Recovery::observe(const gc_sample &sample,const uint32_t *actions,uint32_t count) {
    if(sample.bytes!=sizeof(sample) || sample.version!=GC_VERSION || !sample.sequence
            || sample.sequence<=latest_.sequence)return GO_INVALID;
    const auto previous=latest_;
    const uint32_t fields=GC_VALID_CLOCK|GC_VALID_ACTION|GC_VALID_WARP;
    const bool valid=(sample.valid_fields&fields)==fields
        && (sample.clock.valid&(GCC_RAW_VALID|GCC_ELAPSED_VALID))==(GCC_RAW_VALID|GCC_ELAPSED_VALID)
        && sample.clock.continuity_id && sample.clock.raw_igt==sample.igt;
    Checkpoint now{};now.epoch=sample.epoch;now.timer=sample.global_timer;now.raw=sample.igt;
    now.fields=sample.valid_fields;now.level=sample.level;now.area=sample.area;
    now.sequence=sample.sequence;now.continuity=sample.clock.continuity_id;
    now.elapsed=sample.clock.elapsed_frames;now.star=sample.clock.origin_id;now.qpc=sample.qpc;
    now.action=sample.pose.action;now.animation=sample.pose.animation_id;
    now.animation_frame=sample.pose.animation_frame;
    std::memcpy(now.position,sample.pose.position,sizeof(now.position));
    std::memcpy(now.angles,sample.pose.angles,sizeof(now.angles));
    latest_=now;
    if(!bound())return GO_MISSING;
    if(!valid) {pending_=true;return GO_MISSING;}
    if(previous.sequence && (now.epoch!=previous.epoch || now.timer<previous.timer))absolute_timer_=true;
    const bool forward=previous.sequence && now.epoch==previous.epoch && now.timer>=previous.timer
        && now.raw>previous.raw && now.raw-previous.raw>now.timer-previous.timer;
    const bool retry=origin_.kind==GO_STAR && now.star!=origin_.star_generation
        && now.raw<previous.raw && now.level==previous.level && now.area==previous.area;
    // The occurrence an origin was resolved from already is that origin. A
    // second observer (the renderer) adopting the worker's origin sees it
    // after its own previous sample and must not treat it as a new jump.
    const bool origin_frame=now.sequence==origin_.sequence && now.epoch==origin_.epoch
        && now.continuity==origin_.continuity && now.elapsed==origin_.elapsed;
    if(!origin_frame && (pending_ || now.epoch!=origin_.epoch || now.continuity!=origin_.continuity
            || forward || retry)) {
        pending_=true;const Checkpoint *found=nullptr;
        for(const auto &entry:history_) {
            if(entry.raw!=now.raw || entry.level!=now.level || entry.area!=now.area)continue;
            const bool pose=(entry.fields&now.fields&GC_VALID_POSE)!=0;
            if(pose) {
                if(entry.action!=now.action || entry.animation!=now.animation
                        || entry.animation_frame!=now.animation_frame || !same_place(entry.position,now.position)
                        || std::memcmp(entry.angles,now.angles,sizeof(now.angles)))continue;
            } else if(!absolute_timer_ || entry.timer!=now.timer)continue;
            if(absolute_timer_ && entry.timer!=now.timer)continue;
            if(found && found->coordinate!=entry.coordinate)return GO_AMBIGUOUS;
            found=&entry;
        }
        // Before any banked area/level transition, a star's coordinate is raw
        // Usamune IGT, and so is a segment's that began at Usamune's zero (less
        // its origin's raw IGT). That also names states saved in an earlier
        // attempt or before the ghost was enabled: his practice loads a state
        // saved earlier, also after a reset (round 10). Any other clock needs
        // a unique witnessed LIVE checkpoint, which is always tried first.
        const int64_t named=int64_t(now.raw)+direct_offset_;
        const bool direct=direct_ && now.level==first_level_ && now.area==first_area_
            && named>=0 && named<=int64_t(UINT32_MAX);
        if(!found && !direct)return GO_MISSING;
        origin_={origin_.kind,now.epoch,now.sequence,now.continuity,now.elapsed,now.star,
            found?found->coordinate:uint32_t(named),GO_RECOVERED|(origin_.flags&GO_ZERO_CLOCK)};
        restoration_=now;pending_=ended_=absolute_timer_=false;
        remember(now);return GO_READY;
    }
    if(origin_.kind==GO_STAR && now.star!=origin_.star_generation)ended_=true;
    for(uint32_t i=0;i<count;++i)if(sample.live_action==actions[i])ended_=true;
    if(!ended_)remember(now);
    return GO_READY;
}
}
