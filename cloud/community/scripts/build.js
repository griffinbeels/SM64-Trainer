import {build} from "esbuild";
import {mkdir} from "node:fs/promises";
await mkdir("public",{recursive:true});
await build({entryPoints:["portal/portal.js"],outfile:"public/portal.js",bundle:true,format:"esm",minify:true,target:"es2022",legalComments:"eof"});
