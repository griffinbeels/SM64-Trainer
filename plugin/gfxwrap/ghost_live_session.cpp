#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_live_session.h"
#include "ghost_annotation_assets.h"
#include "ghost_proximity.h"
#include "ghost_scene.h"
#include <atomic>
#include <cmath>
#include <cstring>
#include <new>

namespace ghost_live_session {
static_assert(ghost_compositor::max_poses==GP_GHOSTS+GLIVE_MAX_PEERS,"one batch preserves saved and live capacities");
namespace {
template<class T> struct Mailbox {
    static_assert(sizeof(T)%4==0);
    std::atomic<uint32_t> sequence{0},words[sizeof(T)/4]{};
    bool put(const T &value) noexcept {
        const auto before=sequence.load();if(before>=UINT32_MAX-2)return false;
        uint32_t copy[sizeof(T)/4];std::memcpy(copy,&value,sizeof(value));
        sequence.store(before+1);
        for(unsigned i=0;i<sizeof(T)/4;++i)words[i].store(copy[i]);
        sequence.store(before+2);return true;
    }
    bool get(T &value) const noexcept {
        for(unsigned attempt=0;attempt<2;++attempt) {
            const auto before=sequence.load();if(before&1)continue;
            uint32_t copy[sizeof(T)/4];
            for(unsigned i=0;i<sizeof(T)/4;++i)copy[i]=words[i].load();
            if(sequence.load()==before) {std::memcpy(&value,copy,sizeof(value));return true;}
        }
        return false;
    }
};
struct Member {uint8_t incarnation[16]{};uint32_t member=0,hue=0;char label[GP_LABEL_BYTES]{};};
struct Command {
    LocalOwner owner{};uint8_t room[16]{};uint64_t revision=0;
    uint32_t enabled=0,hidden=0;Controls controls{};Member peers[GLIVE_MAX_PEERS]{};
};
Mailbox<Command> commands;
ghost_live::Input input(stale_after_ns);
ghost_live::Renderer renderer(input); // fixed large storage; never the emulation stack
alignas(8) std::atomic<uint64_t> enabled_revision{0};
std::atomic<uint32_t> worker_thread{0};
Command previous{},active{}; // worker / render respectively
uint64_t last_revision=0,receipt_floor=0;bool have_time=false,worker_exhausted=false;
bool had_present[GLIVE_MAX_PEERS]{}; // worker only; absence withdraws, never retires membership
uint8_t receiver_history[receiver_history_capacity][16]{},active_room[16]{};
unsigned receiver_history_count=0;bool room_active=false; // worker only; no namespace reuse after permanent leave
DWORD render_thread=0;uint64_t render_generation=1;bool render_exhausted=false,chosen=false;
ghost_live::ModelBinding binding{};

bool worker_owner() noexcept {
    auto expected=uint32_t(0);const auto thread=GetCurrentThreadId();
    if(worker_thread.compare_exchange_strong(expected,thread))return true;
    return expected==thread;
}
bool nonzero(const uint8_t *value,unsigned bytes) noexcept {
    uint8_t any=0;for(unsigned i=0;i<bytes;++i)any|=value[i];return any!=0;
}
bool controls_valid(const Controls &c) noexcept {
    return std::isfinite(c.opacity) && c.opacity>=0 && c.opacity<=1
        && std::isfinite(c.proximity_distance) && c.proximity_distance>=0 && c.proximity_distance<=GP_PROXIMITY_MAX
        && std::isfinite(c.name_background_opacity) && c.name_background_opacity>=0 && c.name_background_opacity<=1
        && c.tint_enabled<=1 && c.proximity_enabled<=1 && c.occlusion_fade<=1 && c.names_enabled<=1
        && c.name_background<=GP_NAME_WHITE && c.record_ghosts<=1 && c.inputs_enabled<=1
        && c.input_mode<=GP_INPUT_VALUES && c.hud_inputs_enabled<=1;
}
Command command(const Update &value) noexcept {
    Command out{};out.owner=value.owner;std::memcpy(out.room,value.room,16);
    out.revision=value.revision;out.enabled=value.enabled;out.hidden=value.hidden;out.controls=value.controls;
    for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {
        auto &peer=out.peers[i];peer.member=value.peers[i].member;peer.hue=value.peers[i].hue_degrees;
        std::memcpy(peer.incarnation,value.peers[i].incarnation,16);
        std::memcpy(peer.label,value.peers[i].label,sizeof(peer.label));
    }
    return out;
}
void revoke_impl() noexcept {
    enabled_revision.store(0,std::memory_order_release);
    for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {input.revoke(i);had_present[i]=false;}
    room_active=false;std::memset(active_room,0,sizeof(active_room));
    previous={};
    ghost_playback::set_live_model_demand(0,0,0);
}
int fail(int result) noexcept {revoke_impl();return result;}
int activate_room(const Command &next) noexcept {
    if(room_active && !std::memcmp(active_room,next.room,16))
        return previous.owner.pid==next.owner.pid && previous.owner.created_lo==next.owner.created_lo
            && previous.owner.created_hi==next.owner.created_hi?GP_OK:GP_INVALID;
    for(unsigned i=0;i<receiver_history_count;++i)
        if(!std::memcmp(receiver_history[i],next.room,16))return GP_INVALID;
    if(receiver_history_count==receiver_history_capacity) {worker_exhausted=true;return GP_EXHAUSTED;}
    std::memcpy(receiver_history[receiver_history_count++],next.room,16);
    std::memcpy(active_room,next.room,16);room_active=true;return GP_OK;
}
bool render_owner() noexcept {return render_thread && render_thread==GetCurrentThreadId();}
bool control_current() noexcept {
    return render_owner() && !render_exhausted && active.enabled && !active.hidden
        && active.revision && enabled_revision.load(std::memory_order_acquire)==active.revision;
}
bool same_model(const ghost_live::ModelBinding &a,const ghost_live::ModelBinding &b) noexcept {
    return a.model==b.model && a.model_id==b.model_id && a.rom_generation==b.rom_generation
        && a.configuration==b.configuration && a.context_serial==b.context_serial;
}
void annotate(unsigned seat,const gc_sample &remote,ghost_compositor::Pose &pose) noexcept {
    auto &out=pose.annotation;const auto &peer=active.peers[seat];
    std::memcpy(out.label,peer.label,sizeof(out.label));
    std::memcpy(out.position,remote.pose.position,sizeof(out.position));
    out.scale=remote.pose.scale[1];out.names=active.controls.names_enabled!=0;
    out.background=uint8_t(active.controls.name_background);out.background_opacity=active.controls.name_background_opacity;
    out.slot=uint8_t(GP_GHOSTS+seat);out.inputs=active.controls.inputs_enabled!=0;
    out.hud=active.controls.hud_inputs_enabled!=0;out.values=active.controls.input_mode==GP_INPUT_VALUES;
    if(remote.valid_fields&GC_VALID_PAD) {out.known=true;out.pad=remote.pad;}
    ghost_annotation::View view{};uint64_t id=0;
    out.artwork=ghost_playback::annotations(&view,&id) && id==binding.model_id && view.usamune;
    // Model atlas supplies the font; its exact source is owned with that model.
    out.font=ghost_playback::annotation_font();
    out.top=NAN;
    for(unsigned i=0;i<pose.body_count && i<pose.count;++i)
        if(std::isfinite(pose.vertices[i].position[1]) && !(pose.vertices[i].position[1]<=out.top))
            out.top=pose.vertices[i].position[1];
}
}

int worker_update(const Update &value,uint64_t now_ns) noexcept {
    if(!worker_owner())return GP_WRONG_THREAD;
    if(worker_exhausted)return GP_EXHAUSTED;
    if(have_time && now_ns<receipt_floor)return fail(GP_INVALID);
    receipt_floor=now_ns;have_time=true;
    const auto &owner=value.owner;
    if(!value.revision || value.revision==UINT64_MAX) {worker_exhausted=true;return fail(GP_EXHAUSTED);}
    if(value.revision<last_revision)return GP_INVALID;
    if(value.enabled>1 || value.hidden>1 || !controls_valid(value.controls)
            || !owner.pid || !(owner.created_lo|owner.created_hi) || !owner.generation
            || !owner.rom_generation || !owner.configuration || !owner.token || !owner.model_id
            || (value.enabled && !nonzero(value.room,16)))return fail(GP_INVALID);
    const auto next=command(value);
    if(value.revision==last_revision && std::memcmp(&next,&previous,sizeof(next)))return fail(GP_INVALID);
    for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {
        const auto &peer=value.peers[i];
        if(peer.member>1 || peer.present>1 || peer.hue_degrees>359 || !ghost_annotation::valid_label(peer.label)
                || (peer.member && !nonzero(peer.incarnation,16)) || (peer.present && !peer.member)
                || (peer.present && peer.received_ns>now_ns))return fail(GP_INVALID);
        for(unsigned j=0;j<i;++j)if(peer.member && value.peers[j].member
                && !std::memcmp(peer.incarnation,value.peers[j].incarnation,16))return fail(GP_INVALID);
    }
    if(!value.enabled) {last_revision=value.revision;revoke_impl();previous=next;return GP_OK;}
    const int admitted=activate_room(next);
    if(admitted!=GP_OK)return fail(admitted);
    const int demand=ghost_playback::set_live_model_demand(owner.model_id,owner.rom_generation,owner.configuration);
    if(demand!=GP_OK)return fail(demand);
    const bool changed=value.revision!=last_revision;
    const bool new_room=std::memcmp(previous.room,next.room,16)
        || previous.owner.pid!=owner.pid || previous.owner.created_lo!=owner.created_lo
        || previous.owner.created_hi!=owner.created_hi;
    if(changed)enabled_revision.store(0,std::memory_order_release);
    if(new_room)for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {input.revoke(i);had_present[i]=false;}
    if(!new_room && std::memcmp(&previous.owner,&next.owner,sizeof(owner)))
        for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {input.clear(i);had_present[i]=false;}
    for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {
        const auto &old=previous.peers[i];const auto &peer=value.peers[i];
        const bool same=!new_room && old.member && peer.member && !std::memcmp(old.incarnation,peer.incarnation,16);
        if(old.member && !same) {input.revoke(i);had_present[i]=false;}
        if(peer.member && !same) {
            const auto result=input.authorize(i,peer.incarnation,next.room);
            if(result!=ghost_live::InputResult::ok) {
                worker_exhausted=result==ghost_live::InputResult::capacity || result==ghost_live::InputResult::exhausted;
                return fail(worker_exhausted?GP_EXHAUSTED:GP_INVALID);
            }
        }
        if(peer.present) {
            const auto result=input.ingest(i,&peer.packet,sizeof(peer.packet),peer.received_ns);
            if(result==ghost_live::InputResult::ok)had_present[i]=true;
            if(result!=ghost_live::InputResult::ok && result!=ghost_live::InputResult::not_newer
                    && result!=ghost_live::InputResult::old_source && result!=ghost_live::InputResult::bad_time)
                return fail(result==ghost_live::InputResult::exhausted?GP_EXHAUSTED:GP_INVALID);
        } else if(had_present[i]) {input.clear(i);had_present[i]=false;}
    }
    if(!input.publish(now_ns))return fail(input.exhausted()?GP_EXHAUSTED:GP_BUSY);
    if(changed && !commands.put(next)) {worker_exhausted=true;return fail(GP_EXHAUSTED);}
    previous=next;last_revision=value.revision;enabled_revision.store(value.revision,std::memory_order_release);
    return GP_OK;
}
void worker_revoke() noexcept {if(worker_owner())revoke_impl();}
void worker_suspend() noexcept {
    if(!worker_owner())return;
    enabled_revision.store(0,std::memory_order_release);
    for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {input.clear(i);had_present[i]=false;}
    ghost_playback::set_live_model_demand(0,0,0);
}
int worker_collect() noexcept {
    if(!worker_owner())return GP_WRONG_THREAD;
    return ghost_playback::api()->collect();
}
int worker_release() noexcept {
    if(!worker_owner())return GP_WRONG_THREAD;
    if(enabled_revision.load())return GP_DEFERRED;
    const auto result=ghost_playback::api()->release_worker();
    if(result==GP_OK)worker_thread.store(0,std::memory_order_release);
    return result;
}
bool begin(uint32_t rom,uint32_t configuration,uint32_t context,const gc_sample *local,uint64_t now_ns) noexcept {
    const auto thread=GetCurrentThreadId();if(render_thread && render_thread!=thread)return false;
    render_thread=thread;chosen=false;
    retire_list(); // invalidate every previous list even when controls refuse
    if(render_exhausted || !commands.get(active) || !control_current() || !local
            || active.owner.rom_generation!=rom || active.owner.configuration!=configuration
            || !ghost_playback::shared_model(context,binding) || binding.model_id!=active.owner.model_id)return false;
    chosen=renderer.begin(binding,*local,now_ns);return chosen;
}
bool plan() noexcept {return control_current() && chosen && renderer.plan();}
bool evaluate(const gc_sample &local,const ghost::WorldPass &world,uint64_t now_ns,
              ghost_compositor::Pose *out,uint32_t &count,Token &token) noexcept {
    count=0;token={};if(out)for(unsigned i=0;i<GLIVE_MAX_PEERS;++i)out[i]={};
    if(!control_current() || !out || !ghost_playback::model_current(binding))return false;
    ghost_live::DrawToken draw{};
    if(!renderer.evaluate(local,world,now_ns,active.controls.opacity,out,count,draw))return false;
    unsigned at=0;
    for(unsigned seat=0;seat<GLIVE_MAX_PEERS;++seat)if(draw.included_seats&(1u<<seat)) {
        gc_sample remote{};
        if(!renderer.source(draw,seat,remote) || !ghost_scene::rebase(remote,local)) {count=0;return false;}
        auto &pose=out[at++];pose.hue_degrees=active.controls.tint_enabled?float(active.peers[seat].hue):0;
        ghost_occlusion::region(pose.view_projection,active.controls.occlusion_fade!=0,
            (local.valid_fields&GC_VALID_POSE)!=0,local.pose.position,local.pose.scale,pose.occlusion);
        pose.occlusion.overlap=active.controls.proximity_enabled && (local.valid_fields&GC_VALID_POSE)
            ?ghost_proximity::factor(local.pose.position,local.pose.scale,remote.pose.position,remote.pose.scale,
                active.controls.proximity_distance):1;
        annotate(seat,remote,pose);
    }
    token.draw=draw;token.model=binding;token.revision=active.revision;token.render_generation=render_generation;
    if(!current(token,now_ns)) {count=0;token={};return false;}
    return count!=0;
}
bool current(const Token &token,uint64_t now_ns) noexcept {
    return control_current() && token.revision==active.revision && token.render_generation==render_generation
        && same_model(token.model,binding) && ghost_playback::model_current(binding) && renderer.current(token.draw,now_ns);
}
bool records_display() noexcept {return control_current() && active.controls.record_ghosts!=0;}
void retire_list() noexcept {
    if(!render_owner())return;
    renderer.retire();chosen=false;binding={};
}
void retire_on_context_loss() noexcept {
    if(!render_owner())return;
    renderer.retire();chosen=false;binding={};active={};
    if(render_generation==UINT64_MAX)render_exhausted=true;else ++render_generation;
    // Old render owner has stopped using Scratch before this explicit boundary.
    // Reconstructing releases only its thread binding; Session generation keeps
    // old tokens invalid even if the local Renderer address/counters recur.
    renderer.~Renderer();new (&renderer) ghost_live::Renderer(input);render_thread=0;
}
}
