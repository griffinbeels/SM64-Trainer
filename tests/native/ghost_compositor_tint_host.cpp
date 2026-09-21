/* Reuse the proven hidden-WGL fixture; this executable runs only tint cases. */
#define main compositor_fixture_main
#include "ghost_compositor_host.cpp"
#undef main
int main() {
    const auto foreground=GetForegroundWindow();
    Window window;auto g=dispatch();Scene scene(g,32,32);auto vertices=mesh();
    ordinary_state(g,scene);const auto clean=pixels(g,scene.fbo,32,32);
    const auto z=depth(g,scene),n64z=image_depth(g,scene);
    auto* compositor=gc::create(g,1);require(compositor!=nullptr,"tint create");
    const uint8_t atlas[4]={0,0,0,0};
    require(gc::prepare(compositor,32,32,60,atlas,1,1)==gc::Result::ok,"tint prepare");
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image})for(float hue:{0.f,120.f}) {
        auto mario=pose(vertices,1);mario.hue_degrees=hue;
        require(gc::begin(compositor,target(scene,mode),mario)==gc::Result::ok,"tint begin");
        require(gc::finalize(compositor,77)==gc::Result::ok,"tint finalize");
        const auto rendered=pixels(g,gc::display(compositor).framebuffer,32,32);
        if(hue==0)check_image(rendered,32,32,1);
        for(unsigned y=0;y<32;++y)for(unsigned x=0;x<32;++x) {
            const size_t i=(y*32+x)*4;
            const bool visible=x>=16 && x<24 && y>=8 && y<24;
            if(!visible)for(int channel=0;channel<4;++channel)
                require(rendered[i+channel]==clean[i+channel],"outside silhouette and behind wall stay exact at 100%");
            else if(hue==120) {
                // CSS hue-rotate preserves its luminance matrix, not HSV value.
                // Pure red at 120 degrees is clamped to RGB(0,113,0).
                require(rendered[i]==0 && std::abs(int(rendered[i+1])-113)<=1 && rendered[i+2]==0 && rendered[i+3]==255,
                    "120-degree tint applies only to visible Mario");
            }
        }
        require(pixels(g,scene.fbo,32,32)==clean,"tint leaves clean game RGB exact");
        require(depth(g,scene)==z && image_depth(g,scene)==n64z,"tint leaves both game depth representations exact");
    }
    gc::destroy(compositor);no_error("tint shutdown");require(GetForegroundWindow()==foreground,"tint focus unchanged");
    std::puts("tint passed: hidden=yes hue0=unchanged hue120=mario_only opacity100=outside_exact depth=ordinary,n64_image clean=exact");return 0;
}
