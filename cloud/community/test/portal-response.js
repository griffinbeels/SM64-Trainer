import {readFileSync} from 'node:fs';
import {createService} from '../src/service.js';
import {environment} from './fixtures.js';
const {env}=await environment();
try{
  env.ASSETS={fetch:async()=>new Response(readFileSync(new URL('../public/index.html',import.meta.url)),{headers:{'Content-Type':'text/html'}})};
  const response=await createService().fetch(new Request('https://community.example.invalid/auth/authorize?authorization_id=fixture-authorization'),env,{});
  if(response.status!==200)throw Error('Portal response fixture failed');
  const policy=await createService().fetch(new Request('https://community.example.invalid/policies'),env,{});
  if(policy.status!==200)throw Error('Policy response fixture failed');
  console.log(JSON.stringify({html:await response.text(),headers:Object.fromEntries(response.headers),policy:{html:await policy.text(),headers:Object.fromEntries(policy.headers)}}));
}finally{env.DB.close();}
