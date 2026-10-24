/* Choose actual RGB8 window storage when the practice overlay needs it.
 * ChoosePixelFormat returns a closest match, not an RGB-channel guarantee.
 * This only selects storage before context creation; SourceFormat's later GL
 * component-type/encoding checks remain the authority for capture admission. */
#pragma once
#include <windows.h>
#include "practice_rom.h"

namespace renderer_pixel_format {
inline bool rgb8(const PIXELFORMATDESCRIPTOR& p) {
    return p.iPixelType==PFD_TYPE_RGBA && p.cRedBits==8 && p.cGreenBits==8 && p.cBlueBits==8;
}
inline int choose(const unsigned char* rom,HDC dc,const PIXELFORMATDESCRIPTOR* request,
        decltype(&ChoosePixelFormat) select=ChoosePixelFormat,
        decltype(&DescribePixelFormat) describe=DescribePixelFormat,
        decltype(&GetPixelFormat) assigned=GetPixelFormat) {
    const int original=select(dc,request);
    if(!original || !practice_rom(rom))return original;
    // Windows permits one format per window. A baseline-ROM window can survive
    // a ROM switch; never break that switch by trying to replace its storage.
    const int existing=assigned(dc);
    if(existing)return existing;
    PIXELFORMATDESCRIPTOR actual{};
    const int count=describe(dc,original,sizeof(actual),&actual);
    if(!count || rgb8(actual))return original;
    const DWORD flags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER|PFD_STEREO;
    for(int index=1;index<=count && index<=4096;++index){
        PIXELFORMATDESCRIPTOR p{};
        if(!describe(dc,index,sizeof(p),&p) || !rgb8(p) || (p.dwFlags&PFD_GENERIC_FORMAT)
                || (p.dwFlags&flags)!=(actual.dwFlags&flags) || p.iLayerType!=actual.iLayerType
                || p.cDepthBits<actual.cDepthBits || p.cStencilBits<actual.cStencilBits
                || p.cAlphaBits<request->cAlphaBits)continue;
        return index;
    }
    return original; // Preserve rendering when no compatible RGB8 format exists.
}
}
