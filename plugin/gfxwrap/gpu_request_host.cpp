#include "gpu_request.h"
extern "C" int __cdecl gr_host_validate(unsigned owner,unsigned long long birth,unsigned token,unsigned generation){
    FILETIME created{},exited{},kernel{},user{};
    if(!GetProcessTimes(GetCurrentProcess(),&created,&exited,&kernel,&user))return 0;
    const gr_identity id{GetCurrentProcessId(),owner,generation,token,
        (uint64_t(created.dwHighDateTime)<<32)|created.dwLowDateTime,birth};
    gr_page page{};gc_header partial{};
    if(!gr_read(id,8,512ull<<20,&page)||!gr_channel(page,&partial))return 0;
    return !partial.width&&!partial.height&&!partial.epoch&&!partial.producer_pid
        && partial.owner_pid==owner && partial.table_count==page.table_count
        && partial.table[0].offset==page.table[0].offset && partial.offer_count==page.slots;
}
