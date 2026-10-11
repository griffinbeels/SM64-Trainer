import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {deflateSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {canonical} from '../src/errors.js';
import {Store} from '../src/store.js';
import {config} from '../src/config.js';
export const digest=b=>createHash('sha256').update(b).digest('hex');
export const alice='10000000-0000-4000-8000-000000000001',bob='10000000-0000-4000-8000-000000000002';
export const target={kind:'star',entity_key:'1-0',name:'Fixture star'};
const entries=[{target,clock:'igt',game_version:'us',strategies:[{name:'Standard',cutoffs:[{step:0,frames:1000},{step:1,frames:200},{step:2,frames:50}]}],overall_cutoffs:[{step:0,frames:1000},{step:1,frames:200},{step:2,frames:50}]}];
export const catalog={v:1,generation:digest(canonical(entries)),entries};
export class LocalD1{
  constructor(){this.sqlite=new DatabaseSync(':memory:');for(const migration of ['0001_community.sql','0002_friend_beta.sql','0003_auth_entry_budget.sql'])this.sqlite.exec(readFileSync(new URL('../migrations/'+migration,import.meta.url),'utf8'));}
  prepare(sql){const db=this.sqlite;let args=[];const query={bind(...v){args=v;return query;},async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},execute(){const result=db.prepare(sql).run(...args);return {success:true,meta:{changes:result.changes}};},async run(){return query.execute();}};return query;}
  async batch(statements){this.sqlite.exec('BEGIN IMMEDIATE');try{const results=statements.map(s=>s.execute());this.sqlite.exec('COMMIT');return results;}catch(e){this.sqlite.exec('ROLLBACK');throw e;}}
  close(){this.sqlite.close();}
}
export class LocalR2{
  constructor(){this.objects=new Map();this.metadata=new Map();this.multipart=new Map();this.failDelete=false;}
  async put(key,bytes){if(this.objects.has(key))return null;this.objects.set(key,bytes.slice());return {key};}
  async get(key){const bytes=this.objects.get(key);return bytes?{body:bytes.slice()}:null;}
  async head(key){const bytes=this.objects.get(key);return bytes?{size:bytes.length,customMetadata:this.metadata.get(key)||{}}:null;}
  async createMultipartUpload(key,options){const id=crypto.randomUUID();this.multipart.set(id,{key,options,bytes:null});return this.resumeMultipartUpload(key,id);}
  resumeMultipartUpload(key,id){const owner=this;return {uploadId:id,async uploadPart(_n,bytes){const m=owner.multipart.get(id);if(!m)throw Error('NoSuchUpload');m.bytes=bytes.slice();return {partNumber:1,etag:'fixture'};},async complete(){const m=owner.multipart.get(id);if(!m)throw Error('NoSuchUpload');owner.objects.set(key,m.bytes);owner.metadata.set(key,m.options.customMetadata);owner.multipart.delete(id);return {size:m.bytes.length};},async abort(){owner.multipart.delete(id);}};}
  async delete(key){if(this.failDelete)throw Error('Simulated object deletion failure');this.objects.delete(key);}
}
export async function environment(){
  const env={DB:new LocalD1(),GHOSTS:new LocalR2(),SERVICE_ENABLED:'true',SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-fixture-only',OAUTH_CLIENT_ID:'client-fixture',NATIVE_CALLBACK:'http://127.0.0.1:8072/api/community/auth/callback',TERMS_VERSION:'test-1',PRIVACY_VERSION:'test-1',OPERATOR_NAME:'Fixture operator',OPERATOR_CONTACT:'fixture@example.invalid',POLICY_URL:'https://community.example.invalid/policies',CURSOR_SECRET:'fixture-only-not-a-real-secret-0123456789',CATALOG_GENERATION:catalog.generation};
  env.SUPABASE_SECRET_KEY='fixture-service-only-not-real';
  env.DB.sqlite.prepare('UPDATE settings SET generation=? WHERE id=1').run(catalog.generation);
  const store=new Store(env.DB,config(env));const accounts=[];
  for(const [subject,name]of [[alice,'Alice'],[bob,'Bob']]){await store.sql("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,sharing,terms_version,privacy_version,created_at,updated_at) VALUES(?,?,?,1,'active',?,?,1,'test-1','test-1',0,0)",subject,config(env).issuer,subject,name,name.toLowerCase()).run();accounts.push(await store.account({issuer:config(env).issuer,sub:subject}));}
  return {env,store,accounts};
}
export function nativeClip({version=7,count=300,marker=1}={}){
  const formats={4:[652,636,5],5:[1268,1252,6],6:[1276,1260,7],7:[1284,1268,8]},[width,sample,sv]=formats[version];
  const raw=Buffer.alloc(160+count*width),h=raw.subarray(0,160);h.write('SM64GHO'+version);h.writeUInt32LE(version,8);h.writeUInt32LE(160,12);h.writeUInt32LE(width,16);h.writeUInt32LE(count,20);h.writeUInt32LE(1,24);h.writeUInt32LE(sv,28);h.writeUInt32LE(1,32);h.writeUInt32LE(1,36);h[40]=marker;h.writeBigUInt64LE(1000000n,56);h.writeBigInt64LE(1n,64);h.writeDoubleLE(1000000000,72);h.writeBigUInt64LE(1n,80);h.writeBigUInt64LE(BigInt(count),88);h.writeUInt32LE(100,96);h.writeUInt32LE(100+count-1,100);h.writeInt32LE(9,104);h.writeInt32LE(1,108);h.writeUInt32LE(1,112);h.writeUInt32LE(100,116);h.writeBigUInt64LE(1n,128);h.writeBigUInt64LE(1n,144);
  for(let i=0;i<count;i++){const r=raw.subarray(160+i*width,160+(i+1)*width);r.writeUInt32LE(width,0);r.writeUInt32LE(version,4);r.writeUInt32LE(1,8);r.writeUInt32LE(3,12);r.writeUInt32LE(sample,16);r.writeUInt32LE(sv,20);r.writeUInt32LE(1,24);r.writeUInt32LE(31,28);r.writeBigUInt64LE(BigInt(i+1),32);r.writeBigUInt64LE(BigInt(i+1),40);r.writeBigInt64LE(BigInt(i+1),48);r.writeUInt32LE(100+i,56);r.writeUInt16LE(i,62);r.writeInt16LE(9,64);r.writeInt16LE(1,66);r.writeUInt32LE(1,72);for(let c=0;c<6;c++)r.writeFloatLE(1,76+c*4);r.writeUInt32LE(1,128);r.writeFloatLE(45,248);r.writeUInt32LE(5,260);r.writeUInt32LE(i,272);r.writeBigUInt64LE(1n,292);r.writeBigUInt64LE(1n,300);r.writeBigUInt64LE(BigInt(i),308);r.writeBigUInt64LE(1n,316);}
  return raw;
}
export function ghc1(raw){
  const width=raw.readUInt32LE(16),count=raw.readUInt32LE(20),envelope=Buffer.alloc(20);envelope.write('GHC1');envelope.writeUInt32LE(raw.length,4);envelope.writeUInt32LE(160,8);envelope.writeUInt32LE(width,12);envelope.writeUInt32LE(count,16);const chunks=[envelope,raw.subarray(0,160)];
  for(let at=0;at<count;at+=256){const n=Math.min(256,count-at),records=raw.subarray(160+at*width,160+(at+n)*width),shuffled=Buffer.alloc(records.length);for(let c=0;c<width;c++)for(let r=0;r<n;r++)shuffled[c*n+r]=records[r*width+c];const zip=deflateSync(shuffled,{level:6}),block=Buffer.alloc(8);block.writeUInt32LE(n);block.writeUInt32LE(zip.length,4);chunks.push(block,zip);}return Buffer.concat(chunks);
}
export function upload(raw=nativeClip(),frames=100){const bytes=ghc1(raw);return {bytes,body:{target,clock:'igt',rom:'us',strategy:'Standard',frames,raw_sha256:digest(raw),raw_bytes:raw.length,encoded_sha256:digest(bytes),encoded_bytes:bytes.length,codec:'ghc1',generation:catalog.generation}};}
