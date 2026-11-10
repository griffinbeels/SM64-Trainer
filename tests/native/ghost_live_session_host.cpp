/* Actual shared playback/session/Input/Renderer, established transparent model
 * shell. No ROM assets, SDK, graphics context, provider or live user resource. */
#define main saved_playback_witness_main
#include "ghost_playback_host.cpp"
#undef main
#include "ghost_live_session.h"
#include <fstream>

int main(int argc,char **argv) {
    try {
        check(argc==2,"session host synthetic packet argument");
        std::ifstream file(argv[1],std::ios::binary|std::ios::ate);
        check(bool(file) && file.tellg()==std::streamoff(GLIVE_PACKET_BYTES),"exact synthetic packet");
        glive_packet seed{};file.seekg(0);file.read(reinterpret_cast<char*>(&seed),sizeof(seed));
        check(bool(file),"read synthetic packet");
        const auto *api=ghost_playback::api();Renderer render;
        std::vector<uint8_t> rom(8u*1024u*1024u);
        ghost_live_session::Update update{};
        update.owner={GetCurrentProcessId(),1,2,3,7,4,123,1};update.room[0]=41;
        update.enabled=1;update.revision=1;update.controls.opacity=.7f;
        update.controls.inputs_enabled=update.controls.hud_inputs_enabled=1;
        gc_sample local{};std::memcpy(&local,seed.core,sizeof(seed.core));local.pad=seed.pad;
        local.epoch=5;local.sequence=900;local.dlist_serial=900;local.level=9;local.area=1;
        ghost::WorldPass world{};world.epoch=local.epoch;world.list_serial=local.dlist_serial;world.context_generation=7;
        ghost_compositor::Pose live_poses[GLIVE_MAX_PEERS]{},saved_poses[GP_GHOSTS]{};
        ghost_live_session::Token live_token{};gp_draw_token saved_token{};
        uint32_t live_count=0,saved_visible=0;uint64_t now=1000000000,sequence=1;
        auto offer=[&](bool fresh) {
            if(fresh)++sequence;
            for(unsigned i=0;i<GLIVE_MAX_PEERS;++i) {
                auto &peer=update.peers[i];peer.member=peer.present=1;peer.hue_degrees=i*100;
                if(!peer.incarnation[0])peer.incarnation[0]=uint8_t(i+1);
                std::strcpy(peer.label,i==0?"First":i==1?"Second":"Third");
                peer.packet=seed;std::memcpy(peer.packet.header.participant_incarnation,peer.incarnation,16);
                peer.packet.header.elapsed_ns=sequence;
                gc_sample source{};std::memcpy(&source,seed.core,sizeof(seed.core));
                source.sequence=sequence;source.dlist_serial=sequence+100;source.qpc=int64_t(sequence+1000);
                source.level=9;source.area=1;source.global_timer=uint32_t(sequence+100);
                source.area_update_counter=source.igt=uint16_t(sequence);source.pose.anim_timer=uint16_t(sequence);
                source.clock.raw_igt=source.igt;source.clock.elapsed_frames=sequence;
                source.pose.position[2]=float(10+i*10);
                std::memcpy(peer.packet.core,&source,sizeof(peer.packet.core));
                if(fresh || !peer.received_ns)peer.received_ns=now;
            }
            check(ghost_live_session::worker_update(update,now)==GP_OK,"trusted worker publishes current membership and latest poses");
        };
        auto boundary=[&](bool expect_live,uint32_t expected_count=3) {
            now+=10; // distinct render occurrences cannot precede prior evaluation
            ++local.sequence;++local.dlist_serial;world.list_serial=local.dlist_serial;
            render.run([&] {
                ghost_live_session::retire_list();ghost_playback::safe_boundary(7,4);
                check(ghost_live_session::begin(7,4,7,&local,now+1)==expect_live,"live plan uses common model without saved origin");
                check(ghost_live_session::evaluate(local,world,now+2,live_poses,live_count,live_token)==expect_live,
                      "live evaluation agrees with current visible plan");
                if(expect_live)check(live_count==expected_count && ghost_live_session::current(live_token,now+2),"retained live authorities match surviving seats");
                else check(!live_count && !live_token.revision,"refused evaluation clears live output identity");
            });
        };
        offer(true);
        check(api->prepare_model(1,rom.data(),uint32_t(rom.size()))==GP_OK,"live-only worker prepares one detached local model");
        boundary(true);
        check(opens==1 && closes==0,"live-only has one model and zero saved clips");
        check(live_poses[0].opacity==.7f && live_poses[0].annotation.names && live_poses[0].annotation.inputs
                && live_poses[0].annotation.hud && live_poses[0].annotation.known
                && !std::strcmp(live_poses[0].annotation.label,"First"),"exact posed source carries prefs, name and captured PAD");
        check(live_poses[1].hue_degrees==100 && live_poses[0].occlusion.enabled,"existing live appearance controls applied");
        check(ghost_live_session::presentation_status(now+2).presentations==0,"evaluation alone never claims a window presentation");
        render.run([&]{ghost_live_session::presentation(ghost_live_session::PresentationReason::ready);});
        auto graphics=ghost_live_session::presentation_status(now+2);
        check(graphics.selected_mask==7 && !graphics.displayed_mask && !graphics.presentations,"ready retains selected poses without claiming pixels");
        render.run([&]{ghost_live_session::presentation(ghost_live_session::PresentationReason::displayed,true);});
        graphics=ghost_live_session::presentation_status(now+2);
        check(graphics.displayed_mask==7 && graphics.presentations==1,"qualified display notice advances the fixed counter");
        graphics=ghost_live_session::presentation_status(now+ghost_live_session::stale_after_ns+1);
        check(!graphics.selected_mask && !graphics.displayed_mask && graphics.reason==ghost_live_session::PresentationReason::waiting,
            "status expires before another render boundary even without a worker publication");

        // Healthy / refused / healthy passes through the actual session, Input,
        // Renderer and shared-model facade. No permanently retired room reset.
        update.peers[0].packet.core[0]=0;
        check(ghost_live_session::worker_update(update,now)==GP_OK,"malformed admitted pose clears only its seat");
        graphics=ghost_live_session::presentation_status(now+2);
        check(!graphics.selected_mask && !graphics.displayed_mask,"same-revision seat withdrawal immediately retires prior batch status");
        boundary(true,2);
        check(live_token.draw.included_seats==6,"malformed first peer preserves the two healthy peers");
        now+=1000000;offer(false);boundary(true,2);
        check(live_token.draw.included_seats==6,"old same-source occurrence cannot refill the cleared bad seat");
        now+=1000000;offer(true);boundary(true);
        ++update.peers[0].packet.header.configuration;
        check(ghost_live_session::worker_update(update,now)==GP_OK,"source identity mutation clears presentation without erasing replay fences");
        boundary(true,2);
        now+=1000000;offer(true);boundary(true);
        update.peers[0].received_ns=now+1;
        check(ghost_live_session::worker_update(update,now)==GP_OK,"receipt newer than pre-copy now is skipped without room retirement");
        boundary(true,2);
        now+=1000000;offer(true);boundary(true);
        ++update.revision;ghost_live_session::test_drop_next_publication();
        check(ghost_live_session::worker_update(update,now)==GP_BUSY,"forced bounded publication miss reports retry without retiring room");
        now+=1000000;offer(true);boundary(true);

        std::vector<Clip> clips;
        for(unsigned i=0;i<GP_GHOSTS;++i) {
            clips.push_back(Clip::linear(uint8_t(i+1),3,UINT32_MAX,float(i)));
            check(api->prepare_clip(&clips.back().header,clips.back().frames.data())==GP_OK,"prepare one saved clip beside live demand");
        }
        auto saved=request(clips[0],1);saved.mode=GP_FIXED;saved.origin={};saved.seek_igt=0;saved.clip_count=GP_GHOSTS;
        for(unsigned i=0;i<GP_GHOSTS;++i) {
            saved.clips[i]={};
            std::memcpy(saved.clips[i].clip_id,clips[i].header.clip_id,16);std::strcpy(saved.clips[i].label,"Saved");
        }
        check(api->configure(&saved)==GP_OK,"configure sixteen saved ghosts beside live peers");
        now+=1000000;offer(true);boundary(true);
        render.run([&] {
            check(ghost_playback::plan(local) && ghost_playback::evaluate(local,world,saved_poses,saved_visible,saved_token)
                    && saved_visible==GP_GHOSTS,"sixteen saved and three live use same borrowed model");
            for(unsigned field=0;field<5;++field) {
                auto forged=live_token;
                if(field==0)forged.model.model=nullptr;
                else if(field==1)++forged.model.model_id;
                else if(field==2)++forged.model.rom_generation;
                else if(field==3)++forged.model.configuration;
                else ++forged.model.context_serial;
                check(!ghost_live_session::current(forged,now+2),"every model/context binding component participates in final validity");
            }
            check(ghost_live_session::current(live_token,now+2),"binding mutation probes leave original candidate current");
        });
        check(opens==1,"mixed mode did not prepare or construct a second model");
        ghost_playback::test_miss_next_request();boundary(true);
        check(api->collect()==GP_OK && opens==1 && closes==0,"bounded saved mailbox miss retains mixed model and clip custody");
        render.run([&] {
            check(ghost_playback::plan(local) && ghost_playback::evaluate(local,world,saved_poses,saved_visible,saved_token)
                && saved_visible==GP_GHOSTS,"sixteen saved clips remain usable after bounded mailbox miss beside live demand");
        });
        api->withdraw();ghost_live_session::worker_revoke();
        render.run([&] {
            ghost_live_session::retire_list();ghost_playback::safe_boundary(7,4);
            gp_status status{};
            check(api->status(&status,sizeof(status))==GP_OK && status.model_state==GP_MODEL_RENDER_OWNED,
                  "room leave preserves a withdrawn saved request's reusable model");
            check(!ghost_playback::current(saved_token),"withdrawn saved request remains hidden");
        });
        check(opens==1 && closes==0,"temporary saved withdrawal and room leave do not reload assets");
        ++saved.revision;check(api->configure(&saved)==GP_OK,"resume retained saved assets without reprepare");
        ++update.revision;update.room[0]=42;
        for(auto &peer:update.peers)peer.incarnation[0]+=10;
        now+=1000000;offer(true);boundary(true);
        render.run([&] {
            check(ghost_playback::plan(local) && ghost_playback::evaluate(local,world,saved_poses,saved_visible,saved_token)
                    && saved_visible==GP_GHOSTS,"withdrawn saved assets resume beside new room without reprepare");
        });
        ghost_live_session::worker_revoke();
        render.run([&] {
            check(!ghost_live_session::current(live_token,now+3),"room withdrawal immediately invalidates live candidate");
            check(ghost_playback::current(saved_token),"room withdrawal preserves saved command, clips and timeline");
        });
        ++update.revision;update.room[0]=43;
        for(auto &peer:update.peers)peer.incarnation[0]+=10;
        now+=1000000;offer(true);boundary(true);
        saved.enabled=0;++saved.revision;
        check(api->configure(&saved)==GP_OK,"saved disable is independent of active live demand");
        render.run([&] {
            check(!ghost_playback::current(saved_token) && ghost_live_session::current(live_token,now+3),
                  "saved cancellation cannot cancel the current live model or Input token");
        });
        now+=1000000;offer(true);boundary(true);
        check(opens==1 && closes==0,"saved stop retains only common live model");

        ++update.revision;update.hidden=1;now+=1000000;offer(true);
        render.run([&] {check(!ghost_live_session::current(live_token,now+2),"overlay hide invalidates display only");});
        boundary(false);
        graphics=ghost_live_session::presentation_status(now+2);
        check(graphics.reason==ghost_live_session::PresentationReason::hidden && !graphics.selected_mask && !graphics.displayed_mask,
            "hidden command retires both presentation masks and reports fixed hidden cause");
        ++update.revision;update.hidden=0;now+=1000000;offer(true);boundary(true);
        check(opens==1,"overlay resume does not reload model or replace membership");
        ghost_live_session::worker_suspend();api->revoke(GP_WRONG_IDENTITY);
        render.run([&] {
            check(!ghost_live_session::current(live_token,now+3),"local lease suspension hides before model retirement");
            ghost_live_session::retire_list();ghost_playback::safe_boundary(7,4);
        });
        check(ghost_live_session::worker_collect()==GP_OK && closes==1,"worker retires the former local capture model");
        ++update.owner.token;++update.owner.generation;++update.revision;
        now+=1000000;offer(false);
        check(api->prepare_model(1,rom.data(),uint32_t(rom.size()))==GP_OK,"local lease replacement prepares a fresh model");
        boundary(false); // same old source occurrences cannot refill cleared presentation
        now+=1000000;offer(true);boundary(true);
        check(opens==2,"same source membership resumes on replacement local lease without nonce reuse");
        update.peers[0].present=0;
        check(ghost_live_session::worker_update(update,now)==GP_OK,"same membership may withdraw its latest value without a control change");
        render.run([&] {
            check(!ghost_live_session::current(live_token,now+3),"present-to-absent withdraws included native contribution immediately");
            ghost_live_session::retire_list();ghost_playback::safe_boundary(7,4);
            check(ghost_live_session::begin(7,4,7,&local,now+4)
                && ghost_live_session::evaluate(local,world,now+5,live_poses,live_count,live_token)
                && live_count==2 && live_token.draw.included_seats==6,"absence preserves the other two admitted native peers");
        });
        now+=1000000;offer(true);boundary(true);
        now+=ghost_live_session::stale_after_ns+1;offer(false);boundary(false);
        now+=1000000;offer(true);boundary(true);

        uint8_t retired_room[16]{};std::memcpy(retired_room,update.room,16);
        const auto previous_room_token=live_token;
        ghost_live_session::worker_revoke();
        ++update.revision;++update.room[0];
        now+=1000000;offer(true);boundary(true);
        render.run([&] {check(!ghost_live_session::current(previous_room_token,now+3),
            "new receiver admission with unchanged friend source cannot revive old room draw tokens");});
        auto stale_room=update;++stale_room.revision;std::memcpy(stale_room.room,retired_room,16);
        check(ghost_live_session::worker_update(stale_room,now)==GP_INVALID,
              "retired receiver room cannot reactivate the unchanged friend source");
        ++update.revision;++update.revision;++update.room[0];
        now+=1000000;offer(true);boundary(true);
        stale_room=update;++stale_room.revision;std::memcpy(stale_room.room,retired_room,16);
        for(auto &peer:stale_room.peers) {
            peer.incarnation[0]+=70;
            std::memcpy(peer.packet.header.participant_incarnation,peer.incarnation,16);
        }
        check(ghost_live_session::worker_update(stale_room,now)==GP_INVALID,
              "retired receiver room cannot reactivate even previously unseen friend source identities");
        ++update.revision;++update.revision;++update.room[0];
        now+=1000000;offer(true);boundary(true);
        update.peers[0].member=update.peers[0].present=0;++update.revision;
        check(ghost_live_session::worker_update(update,now)==GP_OK,"actual membership removal retires one scoped source");
        update.peers[0].member=update.peers[0].present=1;++update.revision;
        check(ghost_live_session::worker_update(update,now)==GP_INVALID,
              "removed source cannot be reauthorized within the same receiver room");
        ++update.revision;++update.room[0];
        now+=1000000;offer(true);boundary(true);

        render.run([&] {
            const auto old=live_token;
            ghost_live_session::retire_on_context_loss();ghost_playback::retire_on_context_loss();
            check(!ghost_live_session::current(old,now+3),"context retirement invalidates before shared model detaches");
        });
        graphics=ghost_live_session::presentation_status(now+3);
        check(!graphics.selected_mask && !graphics.displayed_mask && graphics.reason==ghost_live_session::PresentationReason::waiting,
            "context retirement clears presentation status before replacement begins");
        check(ghost_live_session::worker_collect()==GP_OK && closes==2,"worker reclaims detached context model");
        check(api->prepare_model(1,rom.data(),uint32_t(rom.size()))==GP_OK,"same local model rebuilt on setup worker");
        now+=1000000;offer(true);boundary(true);
        check(opens==3,"context recovery creates exactly one replacement model");
        ghost_live_session::worker_revoke();api->revoke(GP_DISABLED);
        render.run([&] {ghost_live_session::retire_on_context_loss();ghost_playback::retire_on_context_loss();});
        check(ghost_live_session::worker_collect()==GP_OK && ghost_live_session::worker_release()==GP_OK,"all worker custody retires after both demands end");
        for(auto &peer:update.peers)peer={};
        bool bounded=false;
        for(unsigned i=0;i<=ghost_live_session::receiver_history_capacity;++i) {
            std::memset(update.room,0,16);const uint32_t room_id=1000+i;
            std::memcpy(update.room,&room_id,sizeof(room_id));update.room[15]=1;++update.revision;
            const int result=ghost_live_session::worker_update(update,now);
            if(result==GP_EXHAUSTED) {bounded=true;break;}
            check(result==GP_OK,"fresh receiver room admission within bounded lifetime history");
            ghost_live_session::worker_revoke();
        }
        check(bounded && ghost_live_session::worker_update(update,now)==GP_EXHAUSTED,
              "receiver room history never evicts or resets after leave to permit unbounded admission");
        check(ghost_live_session::worker_collect()==GP_OK && ghost_live_session::worker_release()==GP_OK,
              "exhausted receiver room history retains no model or active worker demand");
        check(opens==closes && allocations.load()==0 && destructions.load()==0,"no render heap/model destruction or model leak");
        std::printf("{\"ok\":true,\"transparent_model_shell\":true,\"checks\":%u,\"models\":%u,"
            "\"saved\":%u,\"live\":%u,\"render_allocations\":%u,\"render_frees\":%u}\n",
            checks,opens,GP_GHOSTS,GLIVE_MAX_PEERS,allocations.load(),destructions.load());
        return 0;
    } catch(const std::exception &error) {
        std::fprintf(stderr,"live session witness: %s (check %u)\n",error.what(),checks);return 1;
    }
}
