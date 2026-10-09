import {createClient} from '@supabase/supabase-js';
import {nativeAuthorize} from '../src/portal-contract.js';
const $=id=>document.getElementById(id),message=t=>{$('status').textContent=t;},attemptKey='sm64-beta-oauth-attempt';
let client,cfg,attempt,authorizationID;
function save(v){sessionStorage.setItem(attemptKey,JSON.stringify(v));}
function validAttempt(){let a;try{a=JSON.parse(sessionStorage.getItem(attemptKey));}catch{}if(!a||a.expires<Date.now())throw Error('Expired request');return a;}
function nativeRedirect(value){
  const u=new URL(value);if(u.origin+u.pathname!==cfg.auth.native_callback||u.username||u.password||u.hash)throw Error('Invalid return');
  for(const k of u.searchParams.keys())if(!['code','state','error','error_description','error_uri'].includes(k)||u.searchParams.getAll(k).length!==1)throw Error('Invalid return data');
  if(u.searchParams.get('state')!==attempt.state)throw Error('Invalid return state');sessionStorage.removeItem(attemptKey);location.replace(u.href);
}
async function social(provider){save({...attempt,authorizationID});const {error}=await client.auth.signInWithOAuth({provider,options:{redirectTo:location.origin+'/auth/callback',scopes:provider==='google'?'openid email profile':undefined}});if(error)throw error;}
async function showConsent(){
  const {data,error}=await client.auth.oauth.getAuthorizationDetails(authorizationID);if(error||!data)throw Error('Authorization unavailable');
  if(!('authorization_id' in data)){nativeRedirect(data.redirect_url);return;}
  if(data.client.id!==cfg.auth.client_id||data.redirect_uri!==cfg.auth.native_callback||data.scope.split(' ').filter(Boolean).some(s=>!['openid','email','profile'].includes(s)))throw Error('Unregistered request');
  $('login').hidden=true;$('consent').hidden=false;$('permissions').textContent='Requested identity permissions: '+data.scope;message('Approve the trainer’s sign-in request.');
}
async function signedIn(){const {data,error}=await client.auth.getClaims();if(error||!data?.claims){$('login').hidden=false;message('Choose how to sign in.');return;}await showConsent();}
async function decision(approved){const {data,error}=await client.auth.oauth[approved?'approveAuthorization':'denyAuthorization'](authorizationID,{skipBrowserRedirect:true});if(error||!data?.redirect_url)throw Error('Consent failed');nativeRedirect(data.redirect_url);}
function action(id,fn){$(id).addEventListener('click',()=>Promise.resolve().then(fn).catch(()=>message('Sign-in could not finish. Return to the trainer and try again.')));}
async function boot(){
  cfg=await (await fetch('/v1/config',{cache:'no-store',credentials:'omit'})).json();if(cfg.error)throw Error('Unavailable');$('policy').href=cfg.terms_url;
  client=createClient(cfg.auth.supabase_url,cfg.auth.publishable_key,{auth:{flowType:'pkce',storage:localStorage,persistSession:true,autoRefreshToken:true,detectSessionInUrl:false}});
  const u=new URL(location.href);
  if(u.pathname==='/auth/start'){
    const authorize=nativeAuthorize(u.searchParams.get('native_authorize'),{supabaseURL:cfg.auth.supabase_url,clientID:cfg.auth.client_id,callback:cfg.auth.native_callback}),parsed=new URL(authorize),provider=u.searchParams.get('provider');
    if(!['discord','google','twitch','email'].includes(provider))throw Error('Unsupported provider');save({provider,state:parsed.searchParams.get('state'),expires:Date.now()+300000});location.replace(authorize);return;
  }
  // Password recovery can complete without a live native OAuth attempt. Supabase
  // still requires the original browser's PKCE verifier; no implicit token URL.
  if(u.pathname!=='/auth/recovery'){try{attempt=validAttempt();authorizationID=u.searchParams.get('authorization_id')||attempt.authorizationID;}catch(e){if(u.pathname!=='/auth/callback')throw e;}}
  if(u.pathname==='/auth/authorize'){if(!authorizationID||authorizationID.length>256)throw Error('Missing request');attempt={...attempt,authorizationID};save(attempt);}
  else if(u.pathname==='/auth/callback'||u.pathname==='/auth/recovery'){
    const code=u.searchParams.get('code');history.replaceState(null,'',u.pathname);if(!code||code.length>4096)throw Error('Invalid return');const {error}=await client.auth.exchangeCodeForSession(code);if(error)throw Error('Return failed');
    if(u.pathname==='/auth/recovery'){$('recovery').hidden=false;message('Choose a new password.');bindRecovery();return;}
    if(!attempt){message('Email confirmed. Return to the trainer and start sign-in again.');return;}
  }
  action('discord',()=>social('discord'));action('google',()=>social('google'));action('twitch',()=>social('twitch'));
  $('email-form').addEventListener('submit',async e=>{e.preventDefault();try{const {error}=await client.auth.signInWithPassword({email:$('email').value,password:$('password').value});$('password').value='';if(error)message('Email sign-in failed.');else await signedIn();}catch{message('Email sign-in failed.');}});
  action('signup',async()=>{const {error}=await client.auth.signUp({email:$('email').value,password:$('password').value,options:{emailRedirectTo:location.origin+'/auth/callback'}});$('password').value='';message(error?'Email signup failed.':'Confirm your email, then return to the trainer.');});
  action('recover',async()=>{const {error}=await client.auth.resetPasswordForEmail($('email').value,{redirectTo:location.origin+'/auth/recovery'});message(error?'Recovery could not start.':'If the address is registered, a recovery message will arrive.');});
  action('approve',()=>decision(true));action('deny',()=>decision(false));
  if(['discord','google','twitch'].includes(attempt.provider)&&!attempt.providerStarted){attempt={...attempt,providerStarted:true};save(attempt);await social(attempt.provider);return;}
  await signedIn();
}
function bindRecovery(){$('password-form').addEventListener('submit',async e=>{e.preventDefault();const {error}=await client.auth.updateUser({password:$('new-password').value});$('new-password').value='';message(error?'Password could not be saved.':'Password updated. Return to the trainer and sign in again.');});}
boot().catch(()=>message('This sign-in request is unavailable or expired. Return to the trainer.'));
