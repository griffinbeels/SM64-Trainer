/* Actual pinned gSPDisplayList, staged hook and current ghost_link callback. */
#define main capture_fixture_program
#include "ghost_capture_host.cpp"
#undef main
#include "ghost_root_fixture.h"
#include "ghost_root.h"
#include <cassert>
using u32=uint32_t;
using f32=float;
#define WIN32_ASM 1
constexpr float FIXED2FLOATRECIP16=1.0f/65536.0f;
#define DebugMsg(...) ((void)0)
#define _SHIFTR(value,shift,width) ((value)>>(shift))
#define OS_WINDOWS 1
#define SM64_GHOST_RENDERER 1
constexpr unsigned F3D=1;
struct {unsigned PC[10]{},PCi=0,nextCmd=0;bool LLE=false;} RSP;
struct {unsigned PCStackSize=10;unsigned getMicrocodeType(){return F3D;}bool isNoN(){return false;}} GBI;
struct {struct {float modelView[1][4][4]{};unsigned modelViewi=0;} matrix;} gSP;
uint8_t *RDRAM=nullptr;
unsigned RDRAMSize=0;
bool observing=true;
gc_root_probe probe;
namespace ghost_stream {
void display_list(uint32_t pc,uint32_t dl,uint32_t physical,uint32_t depth,const float *matrix) {
    ghost_root_display_list(probe,pc,dl,physical,depth,matrix);
}
}
namespace ghost_link {
// ghost_link.cpp's file-scope hook timer, like observing above: the root
// witness runs the extracted hook, not its cost accounting.
struct HookTime { HookTime() {} }; // user-provided: a used scope object, not an unused local
#include "actual_root_link.inc"
}
unsigned RSP_SegmentToPhysical(unsigned value){return value==body_dl ? 0x40080 : value;}
#include "actual_root_matrix.inc"
#include "actual_root_dispatch.inc"
int main(int argc,char **argv) {
    check(argc==2,"usage: ghost_root_dispatch layout");Fixture f(argv[1]);configure_root_scene(f,floor_matrix);
    for(size_t i=0;i<f.n64.size();++i)f.ram[i]=f.n64[i^3];
    RDRAM=f.ram.data();RDRAMSize=uint32_t(f.ram.size()-1);
    ghost_capture_begin(&f.reader,&f.config,&f.frame,&f.sample,&probe);
    RSP_LoadMatrix(gSP.matrix.modelView[0],root_mtx);
    // _ProcessDList increments the parent's PC before dispatching G_DL.
    RSP.PCi=1;RSP.PC[1]=root_dl+16;gSPDisplayList(alpha_dl);
    check(RSP.PCi==2 && RSP.PC[2]==alpha_dl,"upstream push did not execute");
    RSP_LoadMatrix(gSP.matrix.modelView[0],body_mtx);
    RSP.PCi=1;RSP.PC[1]=root_dl+32;gSPDisplayList(body_dl);
    ghost_root_finish(probe,true,f.sample);
    check(f.sample.pose.root_kind==GC_ROOT_SCALED_MODELVIEW,"actual staged dispatch failed root proof");
    std::puts("actual staged root dispatch passed");
}
