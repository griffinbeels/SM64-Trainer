#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_font_cache.h"
#include "ghost_assets.h"
#include <cstring>
#include <vector>

namespace ghost_font_cache {
namespace {
using ghost_annotation::UsFont;
constexpr char magic[8]={'S','M','6','4','U','F','N','T'};
constexpr uint32_t version=1,header_bytes=48;
constexpr uint32_t payload_bytes=sizeof(UsFont::present)+sizeof(UsFont::tiles);
constexpr uint32_t file_bytes=header_bytes+payload_bytes;
void put32(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;++i)p[i]=uint8_t(v>>(8*i));}
uint32_t get32(const uint8_t *p) {return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
bool hex_equals(const uint8_t digest[32],const char *hex) {
    if(!hex || std::strlen(hex)!=64)return false;
    for(unsigned i=0;i<32;++i) {
        unsigned value=0;
        for(unsigned j=0;j<2;++j) {
            const char c=hex[2*i+j];
            const int nibble=c>='0' && c<='9'?c-'0':c>='a' && c<='f'?c-'a'+10:-1;
            if(nibble<0)return false;
            value=value*16+unsigned(nibble);
        }
        if(value!=digest[i])return false;
    }
    return true;
}
std::vector<uint8_t> payload(const UsFont &font) {
    std::vector<uint8_t> out(payload_bytes);
    std::memcpy(out.data(),font.present.data(),font.present.size());
    std::memcpy(out.data()+font.present.size(),font.tiles.data(),font.tiles.size());
    return out;
}
// Every header field, the stored digest and the cache's expected digest.
bool decode(const std::vector<uint8_t> &file,const char *expected,UsFont &out) {
    if(file.size()!=file_bytes || std::memcmp(file.data(),magic,8) || get32(file.data()+8)!=version
            || get32(file.data()+12)!=payload_bytes)return false;
    const std::vector<uint8_t> body(file.begin()+header_bytes,file.end());
    uint8_t digest[32]{};
    if(!ghost_assets::sha256(body,digest) || std::memcmp(digest,file.data()+16,32) || !hex_equals(digest,expected))
        return false;
    std::memcpy(out.present.data(),body.data(),out.present.size());
    std::memcpy(out.tiles.data(),body.data()+out.present.size(),out.tiles.size());
    return true;
}
bool make_parents(const std::wstring &file) {
    for(size_t at=file.find(L'\\',3);at!=std::wstring::npos;at=file.find(L'\\',at+1)) {
        const std::wstring folder=file.substr(0,at);
        if(!CreateDirectoryW(folder.c_str(),nullptr) && GetLastError()!=ERROR_ALREADY_EXISTS)return false;
    }
    return true;
}
}
Cache user_cache() {
    wchar_t base[MAX_PATH]{};
    const DWORD length=GetEnvironmentVariableW(L"LOCALAPPDATA",base,MAX_PATH);
    // Only an absolute drive path: never a relative or empty folder.
    if(!length || length>=MAX_PATH || length<3 || base[1]!=L':' || base[2]!=L'\\')return {std::wstring(),nullptr};
    std::wstring file(base,length);
    if(file.back()!=L'\\')file+=L'\\';
    return {file+L"SM64Trainer\\plugin-cache\\us-dialogue-font.bin",ghost_annotation::us_font_sha256};
}
bool read(const Cache &cache,UsFont &out) {
    out={};
    if(cache.file.empty() || !cache.digest)return false;
    const HANDLE file=CreateFileW(cache.file.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return false;
    std::vector<uint8_t> data(file_bytes+1); // one more: a longer file is refused
    DWORD got=0;
    const BOOL ok=ReadFile(file,data.data(),DWORD(data.size()),&got,nullptr);
    CloseHandle(file);
    if(!ok || got!=file_bytes)return false;
    data.resize(file_bytes);
    return decode(data,cache.digest,out);
}
bool write(const Cache &cache,const UsFont &font) {
    if(cache.file.empty() || !cache.digest)return false;
    const std::vector<uint8_t> body=payload(font);
    uint8_t digest[32]{};
    if(!ghost_assets::sha256(body,digest) || !hex_equals(digest,cache.digest))return false;
    UsFont existing;
    if(read(cache,existing) && existing.present==font.present && existing.tiles==font.tiles)return true;
    std::vector<uint8_t> data(header_bytes);
    std::memcpy(data.data(),magic,8);put32(data.data()+8,version);put32(data.data()+12,payload_bytes);
    std::memcpy(data.data()+16,digest,32);
    data.insert(data.end(),body.begin(),body.end());
    if(!make_parents(cache.file))return false;
    // Whole or not at all: a temporary file beside it, then one replace.
    const std::wstring temporary=cache.file+L"."+std::to_wstring(GetCurrentProcessId())+L"."
        +std::to_wstring(GetTickCount64())+L".tmp";
    const HANDLE file=CreateFileW(temporary.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return false;
    DWORD put=0;
    const bool written=WriteFile(file,data.data(),DWORD(data.size()),&put,nullptr) && put==data.size()
        && FlushFileBuffers(file);
    CloseHandle(file);
    if(!written || !MoveFileExW(temporary.c_str(),cache.file.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(temporary.c_str());return false;
    }
    return true;
}
bool load(const Cache &cache,ghost_annotation::Assets &assets,const uint8_t *rom,uint32_t bytes) {
    UsFont cached;
    const bool have=rom && bytes>0x3e && rom[0x3e]!='E' && read(cache,cached);
    const bool loaded=assets.load(rom,bytes,have?&cached:nullptr);
    if(loaded && assets.lends)write(cache,assets.lent);
    return loaded;
}
}
