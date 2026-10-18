/* The US font a US cartridge lends to JP names, through the actual per-user
 * cache file: written whole on a US load, read back on a JP load, refused when
 * missing, short, altered or foreign. Synthetic cartridges always; with
 * <us rom> <jp rom> the real ones. The test sets LOCALAPPDATA to a temporary
 * folder, so no file outside it is touched. */
#include "ghost_font_cache.h"
#include "ghost_annotation_mesh.h"
#include "ghost_assets.h"
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

using ghost_annotation::Assets;using ghost_annotation::UsFont;using ghost_annotation::Layout;
using ghost_annotation::Payload;using ghost_annotation::atlas_width;using ghost_annotation::us_font_sha256;
namespace font=ghost_annotation::font;
static unsigned checks=0;
static void check(bool value,const char* why) {++checks;if(!value)throw std::runtime_error(why);}
static void put(std::vector<uint8_t>& data,unsigned at,uint32_t value) {
    for(unsigned i=0;i<4;++i)data[at+i]=uint8_t(value>>(24-8*i));
}
// A literal (all-raw) MIO0 segment 2 at a cartridge's place, with its font
// LUT, its HUD LUT and the given decoded bytes.
static std::vector<uint8_t> segment2(char country,unsigned base,unsigned size,unsigned hud,
                                     const std::vector<std::pair<unsigned,uint32_t>>& words,
                                     const std::vector<std::pair<unsigned,uint8_t>>& texels) {
    std::vector<uint8_t> decoded(size);
    for(unsigned i=0;i<48;++i)put(decoded,hud+4*i,0x02000000u+i*512);
    for(const auto& w:words)put(decoded,w.first,w.second);
    for(const auto& t:texels)decoded[t.first]=t.second;
    std::vector<uint8_t> rom(8*1024*1024);rom[0x3e]=uint8_t(country);std::memcpy(rom.data()+base,"MIO0",4);
    const unsigned flags_end=16+(size+7)/8;put(rom,base+4,size);put(rom,base+8,flags_end);put(rom,base+12,flags_end);
    std::memset(rom.data()+base+16,255,flags_end-16);std::memcpy(rom.data()+base+flags_end,decoded.data(),size);
    return rom;
}
static std::string hex(const UsFont& font) {
    std::vector<uint8_t> body(font.present.begin(),font.present.end());
    body.insert(body.end(),font.tiles.begin(),font.tiles.end());
    uint8_t digest[32]{};check(ghost_assets::sha256(body,digest),"digest");
    std::string out;char two[3];
    for(uint8_t b:digest) {std::snprintf(two,sizeof(two),"%02x",b);out+=two;}
    return out;
}
static std::vector<uint8_t> file_bytes(const std::wstring& path) {
    std::ifstream in(path,std::ios::binary);return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)),{});
}
static void set_file(const std::wstring& path,const std::vector<uint8_t>& data) {
    std::ofstream out(path,std::ios::binary|std::ios::trunc);out.write(reinterpret_cast<const char*>(data.data()),data.size());
}
// The glyph cells (rows 0-127): the font a name is written in.
static bool same_font_cells(const Assets& a,const Assets& b) {
    return !std::memcmp(a.rgba.data(),b.rgba.data(),size_t(atlas_width)*128*4);
}
static void capitals(const ghost_font_cache::Cache& cache,const std::vector<uint8_t>& jp,const char* why) {
    auto japanese_owned=std::make_unique<Assets>();Assets& japanese=*japanese_owned;check(ghost_font_cache::load(cache,japanese,jp.data(),unsigned(jp.size())),"JP atlas still loads");
    check(!japanese.borrowed && japanese.font==font::jp,why);
}

int main(int argc,char** argv) {
    try {
        check(argc==2 || argc==4,"usage: ghost_font_cache_host <folder> [us rom] [jp rom]");
        const std::string folder_utf8(argv[1]);const std::wstring folder(folder_utf8.begin(),folder_utf8.end());
        // US: code 0 at 0x5900 (the LUT check), every other code its own tile;
        // 'a' (0x24) lights the texel the US rotation puts at screen (6, 13).
        std::vector<std::pair<unsigned,uint32_t>> lut{{0x77e8,0x02005900u}};
        for(unsigned c=1;c<256;++c)lut.push_back({0x77e8+4*c,0x0200a000u+c*64});
        auto us=segment2('E',0x108a40,100878,0x7700,lut,{{0xa000+0x24*64+(1*16+2)/2,0xf0}});
        // JP: its own one-bit font, every code's tile by its code; A lights (0, 0).
        std::vector<std::pair<unsigned,uint32_t>> jp_lut;
        for(unsigned c=0;c<256;++c)jp_lut.push_back({0x8338+4*c,0x02007100u+c*16});
        auto jp=segment2('J',0x1076d0,0x15bae,0x8250,jp_lut,{{0x7100+10*16,0x80},{0x7100+0xf4*16+3,0x10}});
        auto plain_owned=std::make_unique<Assets>();Assets& plain=*plain_owned;check(plain.load(us.data(),unsigned(us.size())) && plain.lends,"a US atlas lends its font");
        const std::string digest=hex(plain.lent);
        const ghost_font_cache::Cache cache{folder+L"\\synthetic\\nested\\us-font.bin",digest.c_str()};
        // No file yet: JP writes in its own capitals.
        capitals(cache,jp,"no cache: JP's own capitals");
        // A US load writes the cache, whole, through its own folders.
        auto us_atlas_owned=std::make_unique<Assets>();Assets& us_atlas=*us_atlas_owned;check(ghost_font_cache::load(cache,us_atlas,us.data(),unsigned(us.size())),"US atlas loads");
        const auto written=file_bytes(cache.file);
        check(written.size()==48+32+256*64 && !std::memcmp(written.data(),"SM64UFNT",8),"the US load wrote the cache");
        UsFont read{};check(ghost_font_cache::read(cache,read) && read.tiles==plain.lent.tiles
                            && read.present==plain.lent.present,"the cache reads back the lent font");
        // JP now writes names in the US font, with US widths.
        auto japanese_owned=std::make_unique<Assets>();Assets& japanese=*japanese_owned;check(ghost_font_cache::load(cache,japanese,jp.data(),unsigned(jp.size())),"JP atlas loads");
        check(japanese.borrowed && japanese.font==font::us,"JP borrows the cached US font and its widths");
        check(same_font_cells(japanese,us_atlas),"JP's glyph cells are the US font's, cell for cell");
        check(japanese.rgba[((16+13)*atlas_width+(0x24%32)*8+6)*4+3]==255,"lowercase is the US lowercase tile");
        Payload p;p.font=japanese.font;
        uint8_t chars[64]{};unsigned width=0,us_width=0;
        Layout::fitted("Mario",1,1000,chars,width,p.font);Layout::fitted("Mario",1,1000,chars,us_width);
        check(width==us_width && width<50,"a borrowed-font JP name keeps US widths");
        // Inputs and HUD art stay the cartridge's own: a JP load lends nothing.
        check(!japanese.lends,"a JP atlas never refreshes the US font");
        // Refusals: each leaves JP's capitals.
        set_file(cache.file,std::vector<uint8_t>(written.begin(),written.end()-1));
        capitals(cache,jp,"a truncated cache is refused");
        auto longer=written;longer.push_back(0);set_file(cache.file,longer);
        capitals(cache,jp,"a longer cache is refused");
        auto altered=written;altered[48+32+0x24*64]^=1;set_file(cache.file,altered);
        capitals(cache,jp,"a payload altered under its stored digest is refused");
        auto versioned=written;versioned[8]=2;set_file(cache.file,versioned);
        capitals(cache,jp,"another version is refused");
        auto magic=written;magic[0]='X';set_file(cache.file,magic);
        capitals(cache,jp,"another magic is refused");
        set_file(cache.file,written);
        const ghost_font_cache::Cache foreign{cache.file,ghost_annotation::us_font_sha256};
        capitals(foreign,jp,"a well-formed file holding another font is refused");
        check(!ghost_font_cache::write(foreign,plain.lent),"a font that is not the cache's own is never written");
        check(file_bytes(cache.file)==written,"a refused write leaves the file");
        _wremove(cache.file.c_str());capitals(cache,jp,"a missing cache leaves JP's capitals");
        // The user's cache: under LOCALAPPDATA, for the real US font only.
        const auto user=ghost_font_cache::user_cache();
        check(user.file==folder+L"\\SM64Trainer\\plugin-cache\\us-dialogue-font.bin"
              && !std::strcmp(user.digest,us_font_sha256),"the per-user cache under LOCALAPPDATA");
        bool real=false;
        if(argc==4) {
            const auto load_rom=[](const char* path) {
                std::ifstream in(path,std::ios::binary);return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)),{});
            };
            const auto us_rom=load_rom(argv[2]),jp_rom=load_rom(argv[3]);
            check(us_rom.size()==8*1024*1024 && jp_rom.size()==8*1024*1024 && us_rom[0x3e]=='E' && jp_rom[0x3e]=='J',
                  "a US then a JP practice ROM");
            capitals(user,jp_rom,"before US was ever loaded, JP writes in its capitals");
            auto us_real_owned=std::make_unique<Assets>();Assets& us_real=*us_real_owned;check(ghost_font_cache::load(user,us_real,us_rom.data(),unsigned(us_rom.size())),"US loads");
            check(hex(us_real.lent)==us_font_sha256,"his US cartridge's font is the known US font");
            UsFont kept{};check(ghost_font_cache::read(user,kept),"the US load kept the font in his cache");
            auto jp_real_owned=std::make_unique<Assets>();Assets& jp_real=*jp_real_owned;check(ghost_font_cache::load(user,jp_real,jp_rom.data(),unsigned(jp_rom.size())),"JP loads");
            check(jp_real.borrowed && jp_real.font==font::us && same_font_cells(jp_real,us_real),
                  "after US, JP names are written in the US font");
            check(jp_real.usamune && jp_real.chevron
                  && !std::memcmp(jp_real.rgba.data()+size_t(atlas_width)*128*4,us_real.rgba.data()+size_t(atlas_width)*128*4,
                                  size_t(atlas_width)*128*4),"inputs and HUD art stay the cartridge's own");
            real=true;
        }
        std::printf("{\"ok\":true,\"checks\":%u,\"rom\":%s}\n",checks,real?"true":"false");
        return 0;
    }catch(const std::exception& e) {std::fprintf(stderr,"font cache witness failed: %s\n",e.what());return 1;}
}
