import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPair,SignJWT,exportJWK,createLocalJWKSet,createRemoteJWKSet,customFetch} from 'jose';
import {verifyUserToken,authenticateAdmin} from '../src/auth.js';
const cfg={issuer:'https://fixture.supabase.co/auth/v1', jwks:'https://fixture.supabase.co/auth/v1/.well-known/jwks.json', clientID:'fixture-public-client'};
const subject='10000000-0000-4000-8000-000000000001';
async function signedAccess(pair,{alg='ES256',issuer=cfg.issuer,aud='authenticated',kid='fixture',exp='5m',claims={}}={}){
  return new SignJWT({role:'authenticated',client_id:cfg.clientID,...claims}).setProtectedHeader({alg,kid}).setIssuer(issuer).setSubject(subject).setAudience(aud).setIssuedAt().setExpirationTime(exp).sign(pair.privateKey);
}
test('asymmetric API credentials reject ID audience, issuer, client or claimed role',async()=>{
  for(const alg of ['ES256','RS256']){const pair=await generateKeyPair(alg),publicKey=await exportJWK(pair.publicKey),keys=createLocalJWKSet({keys:[{...publicKey,kid:'fixture',alg}]});
    assert.equal((await verifyUserToken(await signedAccess(pair,{alg}),cfg,keys)).sub,subject);
    for(const options of [{alg,issuer:'https://evil.invalid'},{alg,aud:cfg.clientID},{alg,claims:{client_id:'wrong'}},{alg,claims:{role:'claimed-admin'}},{alg,exp:1}])await assert.rejects(verifyUserToken(await signedAccess(pair,options),cfg,keys),e=>e.status===401);
  }
});
test('JWKS rotation remains on configured URL and rejects unknown keys',async()=>{
  const p1=await generateKeyPair('ES256'),p2=await generateKeyPair('ES256'),k1={...await exportJWK(p1.publicKey),kid:'one'},k2={...await exportJWK(p2.publicKey),kid:'two'};let current=[k1],urls=[];
  const keys=createRemoteJWKSet(new URL(cfg.jwks),{cooldownDuration:0,[customFetch]:async url=>{urls.push(String(url));return Response.json({keys:current});}});
  await verifyUserToken(await signedAccess(p1,{kid:'one'}),cfg,keys);current=[k2];await keys.reload();await verifyUserToken(await signedAccess(p2,{kid:'two'}),cfg,keys);await assert.rejects(verifyUserToken(await signedAccess(p1,{kid:'unknown'}),cfg,keys));assert(urls.every(u=>u===cfg.jwks));
});
test('administrator requires Access issuer/audience and explicit identity allowlist',async()=>{
  const pair=await generateKeyPair('RS256'),keys=createLocalJWKSet({keys:[{...await exportJWK(pair.publicKey),kid:'fixture'}]});
  const env={ACCESS_ISSUER:'https://fixture.cloudflareaccess.com', ACCESS_AUD:'fixture-access-app', ADMIN_EMAILS:'owner@example.invalid'};
  const assertion=await signedAccess(pair,{alg:'RS256',issuer:env.ACCESS_ISSUER,aud:env.ACCESS_AUD,claims:{email:'owner@example.invalid'}}),request=new Request('https://example.invalid',{headers:{'cf-access-jwt-assertion':assertion}});
  assert.equal(await authenticateAdmin(request,env,keys),'owner@example.invalid');await assert.rejects(authenticateAdmin(request,{...env,ADMIN_EMAILS:'other@example.invalid'},keys));await assert.rejects(authenticateAdmin(new Request('https://example.invalid'),env,keys));
});
