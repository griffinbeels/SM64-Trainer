#include "renderer_pixel_format.h"
#include "practice_rom_fixture.h"
#include <cstdio>
#include <cstring>
static PIXELFORMATDESCRIPTOR formats[4]{};
static int selected=1,describes=0,existing=0;
static int WINAPI select_format(HDC,const PIXELFORMATDESCRIPTOR*){return selected;}
static int WINAPI assigned_format(HDC){return existing;}
static int WINAPI describe_format(HDC,int index,UINT,LPPIXELFORMATDESCRIPTOR out){
    ++describes;if(index<1 || index>3)return 0;*out=formats[index];return 3;
}
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"pixel format line %d: %s\n",__LINE__,#x);return 1;}}while(0)
int main(){
    unsigned char rom[0x40];PRACTICE_FIXTURE_USAMUNE(rom);
    PIXELFORMATDESCRIPTOR request{};request.nSize=sizeof(request);request.nVersion=1;
    request.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
    for(auto& p:formats){p=request;p.cDepthBits=24;p.cAlphaBits=8;}
    formats[1].cRedBits=formats[1].cGreenBits=formats[1].cBlueBits=10;
    formats[2].cRedBits=formats[2].cGreenBits=formats[2].cBlueBits=8;
    formats[3]=formats[2];formats[3].cDepthBits=32;
    const auto choose=[&](){return renderer_pixel_format::choose(rom,nullptr,&request,select_format,describe_format,assigned_format);};
    CHECK(choose()==2); // closest non-RGB8 driver answer replaced before SetPixelFormat
    selected=2;describes=0;CHECK(choose()==2 && describes==1); // valid original stays exact
    existing=1;describes=0;CHECK(choose()==1 && describes==0);existing=0; // window storage cannot change
    selected=1;formats[2].cDepthBits=16;CHECK(choose()==3); // preserve depth
    formats[3].dwFlags|=PFD_STEREO;CHECK(choose()==1); // no compatible alternative
    formats[2].cDepthBits=24;formats[2].dwFlags|=PFD_GENERIC_FORMAT;CHECK(choose()==1);
    formats[2].dwFlags=request.dwFlags;formats[2].cAlphaBits=0;request.cAlphaBits=8;CHECK(choose()==1);
    PRACTICE_FIXTURE_VANILLA(rom);describes=0;CHECK(choose()==1 && describes==0);
    selected=0;CHECK(choose()==0);
    std::puts("pixel format passed: supported RGB8, original, depth, flags, alpha, baseline, failure");
}
