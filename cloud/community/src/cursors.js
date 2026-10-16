import {canonical, requireThat, hex64} from './errors.js';
const b64=b=>btoa(String.fromCharCode(...b)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
const un64=t=>Uint8Array.from(atob(t.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
async function key(secret){requireThat(typeof secret==='string'&&secret.length>=32,'configuration','Pagination unavailable.',503);return crypto.subtle.importKey('raw',new TextEncoder().encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);}
export async function encodeCursor(secret,identity,after,page){const b=new TextEncoder().encode(canonical({identity,after,page,expires:Math.floor(Date.now()/1000)+300}));return b64(b)+'.'+b64(new Uint8Array(await crypto.subtle.sign('HMAC',await key(secret),b)));}
// The signed body; any unsigned, malformed or unreadable cursor (or missing secret) is invalid_cursor.
async function signedBody(secret,text){
  try{const [body,sig]=text.split('.'),b=un64(body),signature=un64(sig);requireThat(b64(b)===body&&b64(signature)===sig&&signature.length===32);requireThat(await crypto.subtle.verify('HMAC',await key(secret),signature,b));return JSON.parse(new TextDecoder().decode(b));}catch{requireThat(false,'invalid_cursor','Invalid cursor.');}
}
// The previous page's last sort key: [name, id, hash] for a prefix search, else [integer, integer, hash].
const afterKey=(after,identity)=>Array.isArray(after)&&after.length===3&&hex64(after[2])&&(identity.prefix?typeof after[0]==='string'&&after[0].length<=256&&/^[0-9a-f-]{36}$/.test(after[1]):Number.isSafeInteger(after[0])&&Number.isSafeInteger(after[1]));
export async function decodeCursor(secret,text,identity){
  requireThat(typeof text==='string'&&text.length<=2048&&/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(text),'invalid_cursor','Invalid cursor.');
  const d=await signedBody(secret,text),tuple=afterKey(d.after,identity);
  requireThat(canonical(d.identity)===canonical(identity)&&Number.isSafeInteger(d.page)&&d.page>=1&&d.page<=4&&Number.isFinite(d.expires)&&d.expires>=Date.now()/1000&&tuple,'invalid_cursor','Page expired or query changed.');return d;
}
