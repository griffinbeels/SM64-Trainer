import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPair,SignJWT,exportJWK,createLocalJWKSet} from 'jose';
import {config,ready} from '../src/config.js';
import {nativeAuthorize,registeredNativeClient} from '../src/portal-contract.js';
import {verifyUserToken} from '../src/auth.js';
import {environment,catalog} from './fixtures.js';
import {createService} from '../src/service.js';
const legacy={client_id:'10000000-0000-4000-8000-000000000001',callback:'http://127.0.0.1:8072/api/community/auth/callback'};
const normal={client_id:'10000000-0000-4000-8000-000000000002',callback:'http://127.0.0.1:8064/api/community/auth/callback'};
async function fixture(){const {env}=await environment();return {...env,OAUTH_CLIENT_ID:legacy.client_id,NATIVE_CALLBACK:legacy.callback,OAUTH_CLIENTS:JSON.stringify([legacy,normal])};}
function authorize(pair,cfg){const u=new URL(cfg.supabaseURL+'/auth/v1/oauth/authorize');u.search=new URLSearchParams({client_id:pair.client_id,redirect_uri:pair.callback,response_type:'code',code_challenge_method:'S256',code_challenge:'a'.repeat(43),state:'b'.repeat(32),scope:'openid email profile'});return u.href;}
test('normal and legacy authorizations bind to registered client/callback pairs',async()=>{
  const env=await fixture();try{const cfg=config(env);ready(cfg);
    for(const pair of [legacy,normal]){assert.equal(nativeAuthorize(authorize(pair,cfg),cfg),authorize(pair,cfg));assert.deepEqual(registeredNativeClient(cfg,pair.client_id,pair.callback),pair);}
    for(const pair of [{...normal,callback:legacy.callback},{...legacy,callback:normal.callback},
      {...normal,callback:normal.callback+'?redirect=evil'}, {...normal,callback:normal.callback.replace('127.0.0.1','localhost')},
      {...normal,client_id:'30000000-0000-4000-8000-000000000003'}])assert.throws(()=>nativeAuthorize(authorize(pair,cfg),cfg));
  }finally{env.DB.close();}
});
test('unbounded, duplicate, unregistered or malformed client configuration fails closed',async()=>{
  const env=await fixture();try{for(const clients of [null,[],[normal], [legacy,legacy], [legacy,normal,normal],
    [legacy,{...normal,callback:'http://127.0.0.1:8066/api/community/auth/callback'}],
    [legacy,{...normal,unexpected:true}], [legacy,{...normal,client_id:'wrong'}]]){
      assert.throws(()=>config({...env,OAUTH_CLIENTS:JSON.stringify(clients)}));
    }
    assert.throws(()=>config({...env,OAUTH_CLIENTS:'{broken'}));
    const old={...env};delete old.OAUTH_CLIENTS;assert.deepEqual(config(old).nativeClients,[legacy]);ready(config(old));
  }finally{env.DB.close();}
});
test('API accepts signed native tokens only from either deliberately registered client',async()=>{
  const env=await fixture();try{const cfg=config(env),pair=await generateKeyPair('ES256'),keys=createLocalJWKSet({keys:[{...await exportJWK(pair.publicKey),kid:'fixture'}]});
    async function token(id){return new SignJWT({role:'authenticated',client_id:id}).setProtectedHeader({alg:'ES256',kid:'fixture'}).setIssuer(cfg.issuer).setSubject('30000000-0000-4000-8000-000000000003').setAudience('authenticated').setIssuedAt().setExpirationTime('5m').sign(pair.privateKey);}
    for(const client of [legacy,normal])assert.equal((await verifyUserToken(await token(client.client_id),cfg,keys)).sub,'30000000-0000-4000-8000-000000000003');
    await assert.rejects(verifyUserToken(await token('40000000-0000-4000-8000-000000000004'),cfg,keys),e=>e.status===401);
  }finally{env.DB.close();}
});
test('public configuration and hosted start expose and enforce the same exact pairs',async()=>{
  const env=await fixture();try{
    env.ASSETS={fetch:async()=>new Response('<main>Portal fixture</main>')};
    const service=createService({catalog:async()=>catalog}),cfg=config(env);
    const response=await service.fetch(new Request('https://community.example.invalid/v1/config'),env,{});
    assert.equal(response.status,200);assert.deepEqual((await response.json()).auth.native_clients,[legacy,normal]);
    for(const pair of [legacy,normal]){
      const u=new URL('https://community.example.invalid/auth/start');u.search=new URLSearchParams({provider:'discord',native_authorize:authorize(pair,cfg)});
      assert.equal((await service.fetch(new Request(u),env,{})).status,200);
    }
    const u=new URL('https://community.example.invalid/auth/start');u.search=new URLSearchParams({provider:'discord',native_authorize:authorize({...normal,callback:legacy.callback},cfg)});
    assert.equal((await service.fetch(new Request(u),env,{})).status,400);
  }finally{env.DB.close();}
});
