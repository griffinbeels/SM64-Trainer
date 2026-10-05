#include <cassert>
#include <cstdio>
struct { bool infloop=false, halt=true; } RSP;
bool ConfigOpen=false;
int triangles=0, rectangles=0, observed=0;
bool requested=true, draw_requested=true, completed=false;
struct Drawer {
    void drawTriangles() { ++triangles; }
    void flush() { ++rectangles; }
};
struct Window {
    bool resized=false;
    Drawer drawer;
    bool isResizeWindow() { return resized; }
    Drawer &getDrawer() { return drawer; }
} window;
Window &dwnd() { return window; }
namespace ghost_link {
bool begin_list() { return requested; }
bool drawing() { return draw_requested; }
void end_list(bool valid) { ++observed; completed=valid; }
}
void execute(bool resume_clears_flag=false) {
#include "actual_ghost_list.inc"
    if (resume_clears_flag) RSP.infloop=false;
}
void reset() {
    triangles=rectangles=observed=0; completed=false;
    ConfigOpen=false; window.resized=false; RSP.infloop=false; RSP.halt=true; requested=draw_requested=true;
}
int main() {
    reset(); execute(); assert(observed==1 && completed && triangles==1 && rectangles==1);
    // Capture still completes with playback disabled, without forcing draws.
    reset(); draw_requested=false; execute(); assert(observed==1 && completed && !triangles && !rectangles);
    reset(); requested=false; execute(); assert(!observed && !triangles && !rectangles);
    reset(); ConfigOpen=true; execute(); assert(observed==1 && !completed && !triangles && !rectangles);
    reset(); window.resized=true; execute(); assert(observed==1 && !completed && !triangles);
    reset(); RSP.halt=false; execute(); assert(observed==1 && !completed && !triangles);
    reset(); RSP.infloop=true; execute(true);
    assert(observed==1 && !completed && !triangles && !rectangles);
    std::puts("actual staged DList lifetime passed");
}
