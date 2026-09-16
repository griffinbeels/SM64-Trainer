/* Drives actual wrapper exports and control watchdog; CPU-only child fixture. */
#include "zilmar.h"
#include "renderer_boundary.h"
#include <stdio.h>
#include <string.h>
int main(int argc,char**argv){if(argc!=2)return 2;
 HMODULE module=LoadLibraryA(argv[1]);if(!module)return 3;gfx_api_t api{};RESOLVE_GFX_API(api,module);
 if(!api.InitiateGFX||!api.RomOpen||!api.RomClosed||!api.CloseDLL||!api.ProcessDList||!api.UpdateScreen)return 4;
 auto take=reinterpret_cast<int(__cdecl*)(rb_record*)>(GetProcAddress(module,"TestTake"));
 auto requests=reinterpret_cast<unsigned(__cdecl*)()>(GetProcAddress(module,"TestRequests"));
 auto gate=reinterpret_cast<unsigned(__cdecl*)()>(GetProcAddress(module,"TestGate"));
 unsigned vi=0;GFX_INFO info{};info.MemoryBswaped=TRUE;info.VI_ORIGIN_REG=&vi;
 info.RDRAM=static_cast<unsigned char*>(VirtualAlloc(nullptr,4096,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE));
 if(!info.RDRAM)return 5;
 const BOOL ok=api.InitiateGFX(info);if(ok)api.RomOpen();printf("ready %d\n",ok);fflush(stdout);
 char line[128];bool opened=ok!=FALSE;
 while(fgets(line,sizeof line,stdin)){
  unsigned n=0,d=0;
  if(!strncmp(line,"quit",4))break;
  if(sscanf_s(line,"frame %u %u",&n,&d)==2){
   HMODULE original=GetModuleHandleW(L"original.dll");if(!original)original=GetModuleHandleW(L"unsupported.dll");
   auto delta=reinterpret_cast<void(__cdecl*)(unsigned)>(GetProcAddress(original,"TestDelta"));delta(d);
   for(unsigned i=0;i<n;i++)api.ProcessDList();api.UpdateScreen();printf("%u %u\n",unsigned(info.RDRAM[0]),vi);
  }else if(!strncmp(line,"record",6)){rb_record r{};int result=take(&r);
   printf("%d %llu %u %u %u %u %u %u\n",result,r.occurrence,r.stamp.lists_since,
     unsigned(r.stamp.bytes[0]),r.stamp.lengths[0],r.stamp.lengths[1],r.image.ownership,r.image.status);
  }else if(!strncmp(line,"stats",5))printf("%u %u\n",requests(),gate());
  else if(!strncmp(line,"diagnostic",10)){
   auto diagnostic=reinterpret_cast<int(__cdecl*)()>(GetProcAddress(module,"TestDiagnostic"));
   printf("%d\n",diagnostic?diagnostic():0);
  }
  else if(!strncmp(line,"romclose",8)){api.RomClosed();opened=false;puts("ok");}
  else if(!strncmp(line,"romopen",7)){api.RomOpen();opened=true;puts("ok");}
  else if(!strncmp(line,"close",5)){api.CloseDLL();opened=false;puts("ok");}
  else if(!strncmp(line,"reinit",6)){if(opened)api.RomClosed();api.CloseDLL();const BOOL again=api.InitiateGFX(info);
    opened=again!=FALSE;if(opened)api.RomOpen();printf("%d\n",again);}
  else puts("bad");fflush(stdout);
 }
 if(opened)api.RomClosed();api.CloseDLL();VirtualFree(info.RDRAM,0,MEM_RELEASE);FreeLibrary(module);return 0;
}
