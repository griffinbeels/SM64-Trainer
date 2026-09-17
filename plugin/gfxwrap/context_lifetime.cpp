#include "context_lifetime.h"
#include <gl/GL.h>
namespace source_context {
bool Registry::publish(HGLRC context, uint32_t generation, int pixel_format,
                       Create factory,HDC dc,const int *attributes) {
    if (!context || !generation || pixel_format<=0 || health()) return false;
    for (auto &s:slots_) {
        if ((s.word.load()&state_mask)!=free && s.context==reinterpret_cast<uintptr_t>(context)) return false;
    }
    if (next_lifetime_==UINT32_MAX) { health_.fetch_or(RCL_SERIAL_EXHAUSTED); return false; }
    for (auto &s:slots_) {
        auto word=s.word.load();
        if ((word&state_mask)!=free) continue;
        if (!s.word.compare_exchange_strong(word,(word&~state_mask)|writing)) continue;
        s.context=reinterpret_cast<uintptr_t>(context); s.generation=generation;
        s.pixel_format=uint32_t(pixel_format);
        const HGLRC owned=factory?factory(dc,context,attributes):context;
        if (!owned || (factory && owned==context)) {
            const DWORD error=owned?ERROR_INVALID_HANDLE:GetLastError();
            error_.store(error?error:ERROR_GEN_FAILURE);
            s.word.store(word); // no owned handle, therefore no quarantine
            return false;
        }
        s.owned_context=reinterpret_cast<uintptr_t>(owned);
        error_.store(0);
        s.revoked.store(false); s.retired.store(false); s.failed.store(false);
        s.word.store((uint64_t(++next_lifetime_)<<32)|live);
        return true;
    }
    return false; // fixed capacity; the source retains normal context ownership
}
rcl_token Registry::revoke(HGLRC context) {
    if (!context) return {};
    for (uint32_t n=0;n<RCL_SLOTS;++n) {
        auto &s=slots_[n]; const auto word=s.word.load();
        if (((word&state_mask)!=live && (word&state_mask)!=held)
                || s.context!=reinterpret_cast<uintptr_t>(context)) continue;
        s.revoked.store(true);
        return {word&lifetime_mask,n,0};
    }
    return {};
}
int Registry::delete_idle(Slot &s,uint64_t expected) {
    if ((expected&state_mask)!=live) return RCL_DEFERRED;
    if (s.failed.load()) {
        if (s.word.compare_exchange_strong(expected,(expected&~state_mask)|quarantine))
            return RCL_QUARANTINED;
        return RCL_RELEASED;
    }
    if (!s.word.compare_exchange_strong(expected,(expected&~state_mask)|deleting))
        return RCL_RELEASED; // other owner completed or an acquisition must release
    if (!delete_(reinterpret_cast<HGLRC>(s.owned_context))) {
        const DWORD error=GetLastError();
        error_.store(error?error:ERROR_GEN_FAILURE); health_.fetch_or(RCL_DELETE_FAILED);
        s.word.store((expected&~state_mask)|quarantine);
        return RCL_QUARANTINED;
    }
    s.word.store(expected&~state_mask);
    return RCL_DELETED;
}
int Registry::unbound(rcl_token token,BOOL succeeded) {
    if (!token.value || token.reserved || token.slot>=RCL_SLOTS || (token.value&~lifetime_mask)) return RCL_STALE;
    auto &s=slots_[token.slot]; const auto word=s.word.load();
    if ((word&lifetime_mask)!=token.value || (word&state_mask)==free || !s.revoked.load()) return RCL_STALE;
    if (!succeeded) {
        s.failed.store(true); error_.store(ERROR_BUSY); health_.fetch_or(RCL_UNBIND_FAILED);
    }
    // SC publication and the worker's SC ownership CAS/load form the handoff:
    // at least one sees both unbound and idle, even on weakly ordered machines.
    s.retired.store(true);
    return delete_idle(s,s.word.load());
}
int Registry::acquire(uintptr_t context,uint32_t generation,rcl_lease *out) {
    if (!out) return RCL_UNAVAILABLE;
    *out={};
    if (!context || !generation || health()) return RCL_UNAVAILABLE;
    for (uint32_t n=0;n<RCL_SLOTS;++n) {
        auto &s=slots_[n]; auto word=s.word.load();
        if ((word&state_mask)!=live || s.revoked.load()) continue;
        if ((word&0xfffffff8ull)==0xfffffff8ull) {
            health_.fetch_or(RCL_SERIAL_EXHAUSTED); return RCL_UNAVAILABLE;
        }
        const auto owned=(word&~state_mask)+8+held;
#ifdef RCL_TEST_HOST
        if(probe_)probe_(1);
#endif
        if (!s.word.compare_exchange_strong(word,owned)) continue;
#ifdef RCL_TEST_HOST
        if(probe_)probe_(2);
#endif
        const rcl_token token{owned,n,0};
        if (s.revoked.load() || s.context!=context || s.generation!=generation || health()) {
            release(token); continue;
        }
        *out={token,s.owned_context,s.generation,s.pixel_format,0};
        return RCL_ACQUIRED;
    }
    return RCL_UNAVAILABLE;
}
int Registry::release(rcl_token token) {
    if (token.slot>=RCL_SLOTS || token.reserved || (token.value&state_mask)!=held
            || !(token.value&lifetime_mask)) return RCL_STALE;
    auto &s=slots_[token.slot]; auto expected=token.value;
    const auto idle=(expected&~state_mask)|live;
    if (!s.word.compare_exchange_strong(expected,idle)) return RCL_STALE;
    return s.retired.load()?delete_idle(s,idle):RCL_RELEASED;
}
namespace {
Registry registry;
int __cdecl acquire(uintptr_t c,uint32_t g,rcl_lease *out){return registry.acquire(c,g,out);}
int __cdecl release(rcl_token t){return registry.release(t);}
uint32_t __cdecl health(){return registry.health();}
uint32_t __cdecl error(){return registry.error();}
const rcl_api api{sizeof(rcl_api),RCL_ABI_V1,RCL_CAP_NEVER_CURRENT_ANCHOR,0,acquire,release,health,error};
}
bool Publish(HGLRC c,HDC dc,uint32_t g,int p){
    if(c!=wglGetCurrentContext() || dc!=wglGetCurrentDC())return false;
    auto create=reinterpret_cast<Registry::Create>(wglGetProcAddress("wglCreateContextAttribsARB"));
    const auto address=reinterpret_cast<uintptr_t>(create);
    if(address<=3 || address==UINTPTR_MAX)return false;
    GLint major=0,minor=0,profile=0;
    glGetIntegerv(0x821B,&major);glGetIntegerv(0x821C,&minor); // source startup only
    if(major<3 || (major==3 && minor<2))return false;
    glGetIntegerv(0x9126,&profile);
    if(profile!=1 && profile!=2)return false;
    const int attributes[]={0x2091,major,0x2092,minor,0x9126,profile,0};
    return registry.publish(c,g,p,create,dc,attributes);
}
rcl_token Revoke(HGLRC c){return registry.revoke(c);}
int Retire(rcl_token t){return registry.unbound(t,TRUE);}
}
extern "C" const rcl_api *__cdecl SM64ReplayContextV1(uint32_t version,uint32_t bytes) {
    if (version!=RCL_ABI_V1 || bytes!=sizeof(rcl_api)) return nullptr;
    HMODULE module=nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCWSTR>(&SM64ReplayContextV1),&module)) return nullptr;
    return &source_context::api;
}
#pragma comment(linker,"/EXPORT:SM64ReplayContextV1=_SM64ReplayContextV1")
