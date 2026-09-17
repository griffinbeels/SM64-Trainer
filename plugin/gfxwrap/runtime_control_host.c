/* Actual control worker, x86 request reader, x64 Python client. No GPU/window. */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "control_worker.h"
int __cdecl gr_host_validate(unsigned,unsigned long long,unsigned,unsigned);
int main(int argc,char **argv){
    if(argc!=2)return 2;control_start(argv[1]);control_rom(TRUE, FALSE);puts("ready");fflush(stdout);
    char line[160];
    while(fgets(line,sizeof line,stdin)){
        if(!strncmp(line,"quit",4))break;
        unsigned owner,token,generation;unsigned long long birth;
        if(sscanf_s(line,"check %u %llu %u %u",&owner,&birth,&token,&generation)==4)
            printf("%d\n",gr_host_validate(owner,birth,token,generation));
        else if(!strncmp(line,"close",5)){control_stop();puts("ok");}
        else puts("bad");fflush(stdout);
    }
    control_stop();return 0;
}
