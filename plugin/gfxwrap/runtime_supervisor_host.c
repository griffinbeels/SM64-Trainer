#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#define GFXWRAP_GPU_RUNTIME
#include "control_worker.h"
int __cdecl fake_configure(void);
void __cdecl fake_command(const char*);
int main(int argc,char **argv){
 if(argc!=2||!fake_configure())return 2;
 control_start(argv[1]);control_rom(TRUE);puts("ready");fflush(stdout);
 char line[160];
 while(fgets(line,sizeof line,stdin)){
  if(!strncmp(line,"quit",4))break;
  if(!strncmp(line,"romclose",8)){control_rom(FALSE);puts("ok");}
  else if(!strncmp(line,"romopen",7)){control_rom(TRUE);puts("ok");}
  else if(!strncmp(line,"close",5)){control_stop();puts("ok");}
  else fake_command(line);
  fflush(stdout);
 }
 control_stop();return 0;
}

