import {requireThat,exactQuery} from './errors.js';
export function nativeAuthorize(value,cfg){
  requireThat(typeof value==='string'&&value.length<=4096,'invalid_authorization','Invalid desktop authorization.');
  let u;try{u=new URL(value);}catch{requireThat(false);}
  exactQuery(u,['client_id','redirect_uri','response_type','code_challenge_method','code_challenge','state','scope'],['client_id','redirect_uri','response_type','code_challenge_method','code_challenge','state']);
  requireThat(u.origin===cfg.supabaseURL&&u.pathname==='/auth/v1/oauth/authorize'&&!u.username&&!u.password&&!u.hash&&u.searchParams.get('client_id')===cfg.clientID&&u.searchParams.get('redirect_uri')===cfg.callback&&u.searchParams.get('response_type')==='code'&&u.searchParams.get('code_challenge_method')==='S256'&&/^[A-Za-z0-9_-]{43}$/.test(u.searchParams.get('code_challenge'))&&/^[A-Za-z0-9_-]{22,128}$/.test(u.searchParams.get('state'))&&(u.searchParams.get('scope')||'').split(' ').filter(Boolean).every(s=>['openid','email','profile'].includes(s)),'invalid_authorization','Desktop authorization is not registered.');return u.href;
}
