/* Byte fixture follows geo_process_object -> GEO_SCALE(.25) -> generated
 * alpha -> first animated-part body. throwMatrix is already NULL here. */
#define main capture_fixture_main
#include "ghost_capture_host.cpp"
#undef main
#include <cmath>
#include <functional>
#if GC_VERSION >= 4
#include "ghost_root.h"
#endif

#include "ghost_root_fixture.h"
static void scene(Fixture &f) {
    configure_root_scene(f,floor_matrix);
    f.put32(f.config.mario_state+0x0c,0x04008448);f.put32(Fixture::body,0x04008448);
}
#if GC_VERSION >= 4
static void begin(Fixture &f,gc_root_probe &probe,uint32_t expected=GC_UNSUPPORTED_ROOT) {
    for(size_t i=0;i<f.n64.size();++i) f.ram[i]=f.n64[i^3];
    ghost_capture_begin(&f.reader,&f.config,&f.frame,&f.sample,&probe);
    check(f.sample.status==expected && !(f.sample.valid_fields&GC_VALID_POSE),"root published before execution");
    check(zero(f.sample.pose) || cleared(f.sample.pose,f.sample.pose.refusal),"unqualified root retained pose bytes");
    ++f.frame.sequence;++f.frame.dlist_serial;
}
static void pair(gc_root_probe &p) {
    // Match RSP_LoadMatrix's fixed point conversion, including truncation.
    float m[16];for(unsigned i=0;i<16;++i)m[i]=float(int32_t(floor_matrix[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,m);
    ghost_root_display_list(p,root_dl+24,body_dl,0x40080,1,m);
}
// Mario's executed shadow: captured beside its root, or refused outright.
static bool shadowed(Fixture &f,gc_root_probe &p,const std::function<void()> &execute,bool completed=true) {
    begin(f,p);pair(p);execute();ghost_root_finish(p,completed,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_SHADOW)==zero(f.sample.shadow),"shadow bytes follow validity");
    return (f.sample.valid_fields&GC_VALID_SHADOW)!=0;
}
static void shadows(Fixture &f) {
    gc_root_probe p;
    const auto fresh=[&]{
        scene(f);configure_shadow_scene(f);f.put16(f.config.object_pool+0x42,0x8123); // fresh animation
    };
    const auto normal=[&]{execute_shadow(p,shadow_matrix);};
    fresh();
    check(shadowed(f,p,normal) && (f.sample.valid_fields&GC_VALID_POSE),"Mario's executed shadow captured");
    const auto &s=f.sample.shadow;
    check(s.matrix[12]==130 && s.matrix[14]==-445 && s.matrix[0]==1 && s.matrix[15]==1,"executed shadow matrix kept");
    check(s.position[0][0]==-40 && s.position[8][0]==40 && s.position[3][1]==-5 && s.position[8][2]==-10,"Vtx positions");
    check(s.uv[2][0]==960 && s.uv[6][1]==960 && s.uv[4][0]==480,"Vtx S10.5 texture coordinates");
    check(s.alpha[4]==180 && s.alpha[8]==160 && !s.reserved,"per-vertex solidity");
    check(s.texture[0]==0x07 && s.texture[1]==(uint8_t((14<<4)|5)) && s.texture[127]==uint8_t((((254*7)%16)<<4)|((255*7)%16)),
          "IA8 alpha nibbles packed in texel order");
    // Refusals: each leaves the pose, never a guessed shadow.
    fresh();check(!shadowed(f,p,[]{}) && (f.sample.valid_fields&GC_VALID_POSE),"no executed shadow, no shadow");
    fresh();check(!shadowed(f,p,normal,false),"interrupted list published a shadow");
    fresh();check(!shadowed(f,p,[&]{normal();normal();}),"repeated shadow list accepted");
    fresh();check(!shadowed(f,p,[&]{
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(shadow_matrix[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,shadow_list,shadow_list,1,exact);
        ghost_root_display_list(p,shadow_list,seg_circle,physical2(seg_circle),2,exact);
        ghost_root_display_list(p,shadow_list+16,seg_tris,physical2(seg_tris),2,exact);}),
        "shadow without its closing call accepted");
    fresh();check(!shadowed(f,p,[&]{execute_shadow(p,shadow_matrix,2);}) ,"shadow at another depth than its nested calls accepted"
        );
    // Another object's shadow: same list shape, not at Mario's allocation.
    fresh();words(f,root_dl+44,{shadow_list+48});words(f,shadow_list+48,{0x06000000,seg_circle,0x04800090,
        Fixture::base|shadow_vtx,0x06000000,seg_tris,0x06000000,seg_end,0xb8000000,0});
    check(!shadowed(f,p,[&]{
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(shadow_matrix[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,shadow_list+48,shadow_list+48,1,exact);
        ghost_root_display_list(p,shadow_list+48,seg_circle,physical2(seg_circle),2,exact);
        ghost_root_display_list(p,shadow_list+64,seg_tris,physical2(seg_tris),2,exact);
        ghost_root_display_list(p,shadow_list+72,seg_end,physical2(seg_end),2,exact);}),"another object's shadow accepted");
    fresh();words(f,root_dl+36,{0x22200});fixed(f,0x22200,shadow_matrix);
    check(!shadowed(f,p,normal),"shadow matrix outside Mario's allocation accepted");
    fresh();words(f,shadow_list+12,{Fixture::base|(shadow_vtx+16)});
    check(!shadowed(f,p,normal),"vertices outside Mario's allocation accepted");
    fresh();f.put8(Fixture::base+shadow_vtx+16*3+13,0);check(!shadowed(f,p,normal),"coloured shadow vertex accepted");
    fresh();f.put8(Fixture::base+physical2(seg_texture)+9,0x1f);check(!shadowed(f,p,normal),"non-black shadow texel accepted");
    fresh();words(f,physical2(seg_tris)+4,{0x001e32});check(!shadowed(f,p,normal),"different triangle order accepted");
    fresh();words(f,physical2(seg_circle)+52,{0x00050141});check(!shadowed(f,p,normal),"different texture tile accepted");
    fresh();words(f,physical2(seg_begin)+20,{0});check(!shadowed(f,p,normal),"different shadow combiner accepted");
    fresh();words(f,physical2(seg_end)+12,{0});check(!shadowed(f,p,normal),"different shadow restore accepted");
    // A correct triangle list resolved through a different segment base.
    fresh();words(f,physical2(seg_tris)+0x1000,{0xbf000000,0x001e28,0xbf000000,0x00280a,0xbf000000,0x0a2814,
        0xbf000000,0x142832,0xbf000000,0x1e3c28,0xbf000000,0x283c46,0xbf000000,0x284650,
        0xbf000000,0x285032,0xb8000000,0});
    check(!shadowed(f,p,[&]{
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(shadow_matrix[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,shadow_list,shadow_list,1,exact);
        ghost_root_display_list(p,shadow_list,seg_circle,physical2(seg_circle),2,exact);
        ghost_root_display_list(p,shadow_list+16,seg_tris,physical2(seg_tris)+0x1000,2,exact);
        ghost_root_display_list(p,shadow_list+24,seg_end,physical2(seg_end),2,exact);}),"inconsistent segment base accepted");
    fresh();
    float far[16];std::memcpy(far,shadow_matrix,sizeof(far));far[12]=120+2000;
    fixed(f,shadow_mtx,far);check(!shadowed(f,p,[&]{execute_shadow(p,far);}),"shadow far from Mario accepted");
    fresh();
    float skew[16];std::memcpy(skew,shadow_matrix,sizeof(skew));skew[1]=0.5f;
    fixed(f,shadow_mtx,skew);check(!shadowed(f,p,[&]{execute_shadow(p,skew);}),"non-rigid shadow matrix accepted");
    fresh();begin(f,p);pair(p);pair(p);normal();ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&(GC_VALID_POSE|GC_VALID_SHADOW)) && zero(f.sample.shadow),"shadow without a proven root");
    fresh();check(shadowed(f,p,normal),"shadow proof recovers on the next occurrence");
}
// A held object is drawn from Mario's hand node (geo_process_held_object),
// so its lists follow Mario's alpha/body pair in the master list. Holding,
// hands-free holding (heldObj set outside every hold action), Bowser's tail
// and a ridden shell keep Mario's executed root; the ghost draws Mario alone.
static void held(Fixture &f) {
    gc_root_probe p;
    constexpr uint32_t held_dl=0x23000, held_mtx=0x22200;
    const float hand[16]={.25f,0,0,0, 0,.25f,0,0, 0,0,.25f,0, 150,80,-430,1};
    fixed(f,held_mtx,hand);
    // The held object's own opaque list is alpha-shaped, at the hand.
    words(f,held_dl,{0xfb000000,0xffffffff,0xb8000000,0});
    words(f,root_dl+32,{0x01020040,held_mtx,0x06000000,held_dl});
    const auto state=f.config.mario_state;
    const struct { uint32_t action, held, ridden; uint8_t grab; const char *name; } cases[]={
        {0x08000207,0x80100000,0,1,"hold idle (bob-omb)"},
        {0x00840452,0x80100000,0,1,"hands-free holding while butt sliding"},
        {0x00000391,0x80100000,0,3,"holding Bowser's tail"},
        {0x00000447,0x80100000,0,2,"heavy hold walking"},
        {0x20810446,0,0x80100000,0,"riding a shell"}};
    for(const auto &c:cases) {
        scene(f);f.put32(state+0x0c,c.action);f.put32(Fixture::body,c.action);
        f.put32(state+0x7c,c.held);f.put32(state+0x84,c.ridden);f.put8(Fixture::body+0x0a,c.grab);
        begin(f,p);pair(p);
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(hand[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,held_dl,held_dl,1,exact);
        ghost_root_finish(p,true,f.sample);
        check((f.sample.valid_fields&GC_VALID_POSE) && f.sample.status==GC_OK && !f.sample.pose.refusal
              && f.sample.pose.root_kind==GC_ROOT_SCALED_MODELVIEW && f.sample.pose.grab_position==c.grab
              && f.sample.pose.action==c.action
              && f.sample.pose.root_matrix[9]==float(int32_t(floor_matrix[9]*65536))/65536,c.name);
    }
    // Holding never relaxes ownership: the pair must still be Mario's.
    scene(f);f.put32(state+0x7c,0x80100000);begin(f,p);
    float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(hand[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+40,held_dl,held_dl,1,exact);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE) && cleared(f.sample.pose,GC_REFUSED_NOT_DRAWN_FLOOR),
          "held object's list stood in for Mario");
    f.put32(state+0x7c,0);f.put8(Fixture::body+0x0a,2);words(f,root_dl+32,{0,0,0,0});
}
// A star spawn or dialog cutscene points the camera away: the game still
// updates Mario's graph node (animation, cameraToObject) but culls every
// list. Frozen, his root inputs are bit-identical to the last proven frame,
// so the record carries that root under the camera it executed with.
static void culled(Fixture &f) {
    gc_root_probe p;gc_root_memory memory;
    const auto obj=f.config.object_pool, state=f.config.mario_state;
    const auto proven=[&]{
        scene(f);realistic_shadow(f,shadow_matrix);f.put16(obj+0x42,0x8123);
        begin(f,p);pair(p);execute_shadow(p,shadow_matrix);ghost_root_finish(p,true,f.sample,&memory);
        check((f.sample.valid_fields&GC_VALID_SHADOW) && !f.sample.pose.refusal && memory.valid,
              "proving frame not remembered");
    };
    const auto culled_frame=[&](uint8_t reason) {
        begin(f,p);ghost_root_finish(p,true,f.sample,&memory);
        return f.sample.pose.refusal==reason;
    };
    proven();
    const gc_sample first=f.sample;
    // The cutscene camera moves every frame; Mario does not.
    f.putfloat(Fixture::camera+0x1c,-900);f.putfloat(obj+0x54,5000);
    check(culled_frame(GC_REFUSED_NOT_DRAWN) && (f.sample.valid_fields&GC_VALID_POSE)
          && f.sample.status==GC_OK && f.sample.pose.root_kind==GC_ROOT_SCALED_MODELVIEW,"frozen culled Mario refused");
    check(!std::memcmp(f.sample.pose.root_matrix,first.pose.root_matrix,sizeof(first.pose.root_matrix))
          && !std::memcmp(&f.sample.camera,&first.camera,sizeof(first.camera)),
          "carried root lost the camera it executed under");
    check((f.sample.valid_fields&GC_VALID_SHADOW) && !std::memcmp(&f.sample.shadow,&first.shadow,sizeof(first.shadow)),
          "identical frozen Mario lost his shadow");
    check(f.sample.pose.position[0]==first.pose.position[0] && f.sample.pose.animation_frame==first.pose.animation_frame,
          "carried pose lost its own fresh fields");
    check(culled_frame(GC_REFUSED_NOT_DRAWN) && (f.sample.valid_fields&GC_VALID_POSE),"long freeze stopped carrying");
    // Head turns and animation do not move the root; the shadow can.
    f.put16(Fixture::body+0x12,999);f.put16(obj+0x40,8);
    check(culled_frame(GC_REFUSED_NOT_DRAWN) && (f.sample.valid_fields&GC_VALID_POSE)
          && !(f.sample.valid_fields&GC_VALID_SHADOW) && zero(f.sample.shadow)
          && f.sample.pose.head_angles[0]==999,"animation change carried a stale shadow");
    f.put16(obj+0x40,7);
    // A floor-aligned (sliding) Mario that moved while culled has an
    // unknown tilt: refused, and the reason says so.
    const auto moved=[&](uint32_t at,uint32_t value,const char *name) {
        proven();f.put32(at,value);
        check(culled_frame(GC_REFUSED_NOT_DRAWN_FLOOR) && !(f.sample.valid_fields&(GC_VALID_POSE|GC_VALID_SHADOW))
              && cleared(f.sample.pose,GC_REFUSED_NOT_DRAWN_FLOOR) && zero(f.sample.shadow),name);
    };
    moved(obj+0x20,0x41a80001,"moved Mario carried a root");
    moved(obj+0x1c,0x30390001,"turned Mario carried a root");
    moved(obj+0x30,0x3fa00001,"rescaled Mario carried a root");
    moved(state+0x0c,0x00840452,"new action carried a root");
    moved(Fixture::body,0x00840452,"new body action carried a root");
    // The frame the star spawn freezes Mario he has already moved once, off
    // camera. Airborne (no align_with_floor), geo_process_object composes
    // TRS(gfx pos, angle, scale): the record says so and keeps this frame's
    // camera. Later frozen frames match it, not the remembered matrix.
    const auto airborne=[&](uint32_t action) {
        f.put32(state+0x0c,action);f.put32(Fixture::body,action);
    };
    proven();airborne(0x01000887);f.putfloat(obj+0x24,4046);f.putfloat(Fixture::camera+0x1c,-700);
    for(unsigned frozen=0;frozen<3;++frozen) {
        check(culled_frame(GC_REFUSED_NOT_DRAWN) && (f.sample.valid_fields&GC_VALID_POSE) && f.sample.status==GC_OK
              && f.sample.pose.root_kind==GC_ROOT_TRS && !(f.sample.valid_fields&GC_VALID_SHADOW)
              && f.sample.pose.position[1]==4046 && f.sample.camera.position[0]==-700,
              "culled airborne Mario was not his own TRS root");
        const float none[16]{};
        check(!std::memcmp(f.sample.pose.root_matrix,none,sizeof(none)),"TRS root kept a matrix");
    }
    proven();airborne(0x01000887);f.putfloat(obj+0x24,4046);f.put32(obj+0x8c,0x800);
    check(culled_frame(GC_REFUSED_NOT_DRAWN_FLOOR) && !(f.sample.valid_fields&GC_VALID_POSE),
          "object throw-matrix flag composed a TRS root");
    f.put32(obj+0x8c,0);
    // A drawn-but-unproven Mario, a translucent one, an interrupted list, a
    // shared model, another scene or epoch never borrow the memory.
    proven();begin(f,p);
    float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(floor_matrix[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,exact);ghost_root_finish(p,true,f.sample,&memory);
    check(cleared(f.sample.pose,GC_REFUSED_BODY_MISMATCH),"drawn Mario without his body carried a root");
    begin(f,p);pair(p);pair(p);ghost_root_finish(p,true,f.sample,&memory);
    check(cleared(f.sample.pose,GC_REFUSED_DUPLICATE),"duplicate proof carried a root");
    begin(f,p);ghost_root_finish(p,false,f.sample,&memory);
    check(cleared(f.sample.pose,GC_REFUSED_INCOMPLETE),"interrupted list carried a root");
    const auto clone=obj+0x260;f.put16(clone+2,1);f.put32(clone+0x14,Fixture::base+0x18000);
    check(culled_frame(GC_REFUSED_SHARED_MODEL) && !(f.sample.valid_fields&GC_VALID_POSE),"shared model carried a root");
    f.put16(clone+2,0);
    memory.area=2;check(culled_frame(GC_REFUSED_NOT_DRAWN_FLOOR) && !(f.sample.valid_fields&GC_VALID_POSE),
                        "another area's root carried");
    proven();++f.frame.epoch;f.frame.sequence=f.frame.dlist_serial=1;
    check(culled_frame(GC_REFUSED_NOT_DRAWN_FLOOR) && !(f.sample.valid_fields&GC_VALID_POSE),"another epoch's root carried");
    begin(f,p);ghost_root_finish(p,true,f.sample);
    check(cleared(f.sample.pose,GC_REFUSED_NOT_DRAWN_FLOOR),"no memory, no carry");
    // An invisible Mario (act_disappeared) is a valid hidden pose even though
    // the renderer never advanced his animation from startFrame-1.
    scene(f);f.put16(obj+2,0x20);f.put16(obj+0x40,0xffff);f.put16(obj+0x42,1);
    for(size_t i=0;i<f.n64.size();++i) f.ram[i]=f.n64[i^3];
    ghost_capture_begin(&f.reader,&f.config,&f.frame,&f.sample,&p);++f.frame.sequence;++f.frame.dlist_serial;
    ghost_root_finish(p,true,f.sample,&memory);
    check((f.sample.valid_fields&GC_VALID_POSE) && f.sample.pose.hidden && f.sample.pose.root_kind==GC_ROOT_TRS
          && !f.sample.pose.refusal,"invisible Mario refused");
    f.put16(obj+2,0x21);f.put16(obj+0x40,7);f.put16(obj+0x42,0x8123);
}
// Vanish cap: make_gfx_mario_alpha's translucent list (dithered alpha
// compare, then envcolor alpha 0x80 for MODEL_STATE_NOISE_ALPHA) is 24 bytes,
// so it starts 8 bytes lower and Mario's shadow keeps its place past the
// list's end. Metal keeps the opaque list; the cap-effect switch picked the
// metal body the walk follows. The executed list must match the recorded
// modelState, alpha included.
static void materials(Fixture &f) {
    gc_root_probe p;
    float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(floor_matrix[i]*65536))/65536;
    const auto drawn=[&](uint16_t state,uint32_t alpha_at,uint32_t alpha_word) {
        scene(f);realistic_shadow(f,shadow_matrix);f.put16(f.config.object_pool+0x42,0x8123);
        f.put16(Fixture::body+8,state);
        words(f,alpha_dl,{0xfb000000,0xffffffff,0xb8000000,0});
        if(alpha_at!=alpha_dl)words(f,alpha_dl-8,{0xb9000002,3,0xfb000000,alpha_word,0xb8000000,0});
        words(f,root_dl+12,{alpha_at});
        begin(f,p);
        ghost_root_display_list(p,root_dl+8,alpha_at,alpha_at,1,exact);
        ghost_root_display_list(p,root_dl+24,body_dl,0x40080,1,exact);
        execute_shadow(p,shadow_matrix);
        ghost_root_finish(p,true,f.sample);
        const bool posed=(f.sample.valid_fields&GC_VALID_POSE)!=0;
        check(!posed || (f.sample.valid_fields&GC_VALID_SHADOW),"proven material Mario lost his shadow");
        check(!posed || f.sample.pose.model_state==state,"material state not recorded");
        f.put16(Fixture::body+8,0);words(f,root_dl+12,{alpha_dl});
        return posed;
    };
    check(drawn(0x180,alpha_dl-8,0xffffff80),"vanish-cap Mario refused");
    check(drawn(0x200,alpha_dl,0xffffff80),"metal Mario refused");
    check(drawn(0x340,alpha_dl-8,0xffffff40),"metal vanish-cap Mario refused");
    check(drawn(0x1ff,alpha_dl,0xffffff80),"fully faded-in warp Mario (alpha 255) refused");
    check(!drawn(0x180,alpha_dl-8,0xffffff7f),"translucent list with another alpha accepted");
    check(!drawn(0x180,alpha_dl,0xffffff80),"opaque list accepted for a vanish-cap Mario");
    check(!drawn(0,alpha_dl-8,0xffffffff),"translucent list accepted for an opaque Mario");
}
// The object Mario holds rides his proven pose: its own graph node off or
// invisible, its animation advanced this frame, the HOLP its world root.
// Every failed proof, or a refused root, leaves the block cleared.
static void held_block(Fixture &f) {
    gc_root_probe p;gc_root_memory memory;
    const uint32_t bomb=f.config.object_pool+0x260, anim=Fixture::base+0x14100;
    const auto holding=[&]{
        scene(f);f.put16(f.config.object_pool+0x42,0x8123);
        f.put32(f.config.mario_state+0x7c,bomb);
        // Its own model (sharedChild), not Mario's: the hand draws it.
        f.put16(bomb+0x74,1);f.put16(bomb+2,0x20);f.put32(bomb+0x14,Fixture::base+0x14200);
        f.put32(bomb+0x20c,f.config.behaviour_base+0x3174);
        f.put16(bomb+0x38,1);f.put16(bomb+0x3a,7);f.put16(bomb+0x40,12);f.put32(bomb+0xf0,1);
        f.put32(bomb+0x3c,anim);f.put16(anim+8,24);f.put16(bomb+0x42,0x8123);
        for(unsigned i=0;i<3;++i) {
            f.putfloat(bomb+0x2c+4*i,1.0f);f.putfloat(Fixture::body+0x18+4*i,100.0f+float(i));
        }
    };
    const auto frame=[&](bool proven) {
        begin(f,p);if(proven)pair(p);ghost_root_finish(p,true,f.sample,proven?&memory:nullptr);
        const bool held=(f.sample.valid_fields&GC_VALID_HELD)!=0;
        check(held || zero(f.sample.held),"held block bytes follow validity");
        return held;
    };
    holding();
    check(frame(true),"held Bob-omb not captured");
    const auto &h=f.sample.held;
    check(h.behavior==0x13003174 && h.animation_id==1 && h.animation_frame==12 && h.anim_y_translation==7
          && h.holp[0]==100 && h.holp[2]==102 && h.scale[1]==1 && h.anim_state==1,"held Bob-omb fields");
    holding();check(!frame(false),"held object kept without a proven pose");
    const auto none=[&](uint32_t at,uint32_t word,const char *why){holding();f.put32(at,word);check(!frame(true),why);};
    none(bomb+0,0x00180021,"held object drawn by its own node accepted");
    none(bomb+0x40,0x000c8122,"stale held animation accepted"); // frame 12, animTimer one behind
    none(bomb+0x74,0,"inactive held object accepted");
    none(bomb+0x20c,0x80001000,"behaviour outside segment 0x13 accepted");
    none(bomb+0x40,0x00188123,"frame past the held animation accepted");
    none(Fixture::body+0x18,0x7fc00000,"nonfinite HOLP accepted");
    none(f.config.mario_state+0x7c,bomb+4,"unaligned held pointer accepted");
    none(bomb+0x14,0,"held object without a model accepted"); // Bowser's tail anchor
    // Each kind with the node flags its held behaviour really leaves:
    // bobomb_held_loop and bhv_mips_held set only GRAPH_RENDER_INVISIBLE
    // (0x10), ACTIVE (0x01) staying set; the others clear ACTIVE; the cork box
    // through cur_obj_disable_rendering. HAS_ANIMATION is 0x20.
    const struct { uint32_t behaviour; uint16_t flags; bool animated; const char *name; } kinds[]={
        {0x3174,0x31,true,"held Bob-omb (active, invisible) not captured"},
        {0x44FC,0x31,true,"held Mips (active, invisible) not captured"},
        {0x01F4,0x20,true,"held King Bob-omb not captured"},
        {0x0528,0x20,true,"held Chuckya not captured"},
        {0x20E8,0x20,true,"held penguin not captured"},
        {0x1650,0x00,false,"held crazy box not captured"},
        {0x4218,0x00,false,"held cork box not captured"}};
    for(const auto &k:kinds) {
        holding();f.put32(bomb+0x20c,f.config.behaviour_base+k.behaviour);f.put16(bomb+2,k.flags);
        if(!k.animated)f.put32(bomb+0x3c,0);
        check(frame(true) && f.sample.held.behavior==0x13000000u+k.behaviour,k.name);
    }
    // A box has no animation (id -1, frame 0): the hand drew it only if
    // Mario's body was drawn, so it rides a root proven by this frame's lists
    // and never a carried one. An animated object's own timer proves itself.
    const auto box=[&]{holding();f.put32(bomb+0x20c,f.config.behaviour_base+0x4218);f.put32(bomb+0x3c,0);};
    box();check(frame(true),"held box not captured");
    check(h.behavior==0x13004218 && h.animation_id==-1 && h.animation_frame==0 && h.anim_y_translation==0
          && h.anim_state==1,"held box fields");
    box();begin(f,p);ghost_root_finish(p,true,f.sample,&memory);
    check((f.sample.valid_fields&GC_VALID_POSE) && !(f.sample.valid_fields&GC_VALID_HELD) && zero(f.sample.held),
          "held box kept beside a carried root");
    holding();begin(f,p);ghost_root_finish(p,true,f.sample,&memory);
    check((f.sample.valid_fields&GC_VALID_POSE) && (f.sample.valid_fields&GC_VALID_HELD),
          "animated held object beside a carried root");
    holding();f.config.behaviour_base=0;check(!frame(true),"capture without a behaviour base");
    f.config.behaviour_base=0x800EB180;
    f.put32(f.config.mario_state+0x7c,0);f.put16(bomb+0x74,0);
}
#endif
int main(int argc,char **argv) {
    check(argc==2,"usage: ghost_root_host config"); Fixture f(argv[1]);scene(f);
#if GC_VERSION < 4
    check(f.run()==GC_OK,"RED: completed floor-aligned root is refused after throwMatrix was cleared");
#else
    gc_root_probe p;begin(f,p);check(p.prepared,"Mario persistent graph was not qualified");pair(p);
    ghost_root_finish(p,true,f.sample);
    check(f.sample.status==GC_OK && (f.sample.valid_fields&GC_VALID_POSE),"executed floor root not restored");
    check(f.sample.pose.root_kind==GC_ROOT_SCALED_MODELVIEW,"floor root guessed TRS");
    check(f.sample.pose.root_matrix[9]==float(int32_t(floor_matrix[9]*65536))/65536,"fixed matrix was not preserved");
    check(f.sample.pose.scale[1]==1.25f,"graphics scale lost");
    begin(f,p);pair(p);gc_sample preview=f.sample;
    check(ghost_root_candidate(p,preview) && (preview.valid_fields&GC_VALID_POSE),"drawing preview omitted proven root");
    check(!(f.sample.valid_fields&GC_VALID_POSE),"drawing preview changed recording evidence");
    ghost_root_finish(p,false,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"preview bypassed completion guard");
    begin(f,p);pair(p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE) && cleared(f.sample.pose,GC_REFUSED_DUPLICATE),"duplicate owner accepted");
    begin(f,p);pair(p);ghost_root_finish(p,false,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"interrupted list published a root");
    begin(f,p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"old matrix survived new occurrence");
    begin(f,p);pair(p);++f.sample.epoch;ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"wrong epoch reused root");
    f.putfloat(f.config.object_pool+0x54,121);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"shared Mario model at another position matched owner");
    scene(f);const auto clone=f.config.object_pool+0x260;
    f.put16(clone+2,1);f.put32(clone+0x14,Fixture::base+0x18000);
    begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"second active owner of shared model accepted");
    f.put16(clone+2,0);
    scene(f);f.put32(Fixture::base+root_dl+16,0x01000040);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"matrix multiply accepted as fresh root load");
    scene(f);begin(f,p);
    float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(floor_matrix[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,exact);
    check(p.armed,"depth witness did not first admit alpha");
    ghost_root_display_list(p,root_dl+24,body_dl,0x40080,2,exact);
    ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"body at another traversal depth accepted");
    scene(f);f.put32(Fixture::base+root_dl+24,0);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"callback without an executed body command accepted");
    scene(f);
    const auto branch=Fixture::base+0x18100, animation=branch+0x40;
    const auto start=branch+0xc0, near_lod=start+0x40, far_lod=near_lod+0x40, far_part=far_lod+0x40;
    f.put16(branch+0x1e,1);f.put32(animation+8,start);
    f.node(start,0x0a,branch);f.put32(start+8,animation);f.put32(start+16,near_lod);
    f.node(near_lod,0x0b,start);f.put16(near_lod+0x14,uint16_t(-2048));f.put16(near_lod+0x16,400);
    f.put32(near_lod+8,far_lod);
    f.node(far_lod,0x0b,start);f.put16(far_lod+0x14,400);f.put16(far_lod+0x16,1600);
    f.put32(far_lod+8,near_lod);f.put32(far_lod+16,far_part);
    f.node(far_part,0x19,far_lod);f.put32(far_part+0x14,body_dl);
    begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(f.sample.valid_fields&GC_VALID_POSE,"selected moving/LOD branch did not own its executed body");
    f.put16(branch+0x1e,7);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"unavailable selected model branch accepted");
    scene(f);begin(f,p);
    float wrong[16];std::memcpy(wrong,floor_matrix,sizeof(wrong));wrong[0]=.5f;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,wrong);pair(p);
    // A bad preceding candidate cannot poison a subsequent complete proof.
    ghost_root_finish(p,true,f.sample);check(f.sample.valid_fields&GC_VALID_POSE,"valid proof after refused candidate lost");
    scene(f);f.put16(f.config.object_pool+0x42,0);begin(f,p,GC_BAD_VALUE); // source freshness still enforced
    check(!p.prepared,"stale animation admitted root candidate");
    shadows(f);
    held(f);
    culled(f);
    materials(f);
    held_block(f);
    std::puts("executed root qualification passed");
#endif
}
