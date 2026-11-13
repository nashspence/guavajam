import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
globalThis.crypto ||= webcrypto;
import worker,{signedRead} from '../src/guavajam_core/resources/storage/worker.js';
const digest='b'.repeat(64),key='sha256/'+digest+'.mp4',url='https://assets.publisher.test/'+key;
const env={ASSET_HOST:'assets.publisher.test',B2_S3_ENDPOINT:'https://s3.us-west-004.backblazeb2.com',B2_BUCKET_NAME:'test-bucket',B2_APPLICATION_KEY_ID:'example-id',B2_APPLICATION_KEY:'example-secret'};
function setup(extension='mp4',size=1000){
 const entry={schema:'press.asset.v1',sha256:digest,extension,version_id:'fixed_version',byte_count:size,content_type:({mp4:'video/mp4',mp3:'audio/mpeg',bin:'application/octet-stream',vtt:'text/vtt',svg:'image/svg+xml'})[extension],retain_until:2000000000000};
 return {...env,__mapping:entry};
}
async function run(request,config,handler){
 const original=globalThis.fetch;
 globalThis.fetch=async(target,options)=>{
  if(target.includes('/sha256/.pins/')){
   if(config.__mapping===null)return new Response('',{status:404});
   if(config.__mapping===undefined)throw Error('index unavailable');
   const raw=JSON.stringify(config.__mapping);
   return new Response(raw,{headers:{'Content-Type':'application/json','Content-Length':String(Buffer.byteLength(raw))}});
  }
  return handler(target,options);
 };
 try{return await worker.fetch(request,config);}finally{globalThis.fetch=original;}
}
function upstream({size=1000,start,end,version='fixed_version',mime='video/mp4',status}={}){
 const partial=start!==undefined;const length=partial?end-start+1:size;
 return new Response(new Uint8Array(length),{status:status??(partial?206:200),headers:{'Content-Length':String(length),'Content-Type':mime,'x-amz-version-id':version,...(partial?{'Content-Range':`bytes ${start}-${end}/${size}`}:{})}});
}
test('S3 signing binds method, exact key and immutable version without query credentials',async()=>{
 const signed=await signedRead(env,key,'fixed_version','GET','bytes=1-2',new Date('2026-11-12T00:00:00Z'));
 assert.equal(signed.url,'https://s3.us-west-004.backblazeb2.com/test-bucket/'+key+'?versionId=fixed_version');
 assert.match(signed.headers.Authorization,/Credential=example-id\/20261112\/us-west-004\/s3\/aws4_request/);
 assert.equal(signed.headers.Range,'bytes=1-2');assert.equal(signed.headers['X-Amz-Date'],'20261112T000000Z');
 assert(!signed.url.includes('example-secret'));
 await assert.rejects(()=>signedRead({...env,B2_S3_ENDPOINT:'https://evil.example/'},key,'fixed_version','GET'));
});
test('full reads and HEAD expose only the fixed version and approved media type',async()=>{
 const original=globalThis.fetch;const calls=[];
 globalThis.fetch=async (target,options)=>{calls.push([target,options]);return upstream();};
 try{
  const result=await run(new Request(url),setup(),globalThis.fetch);
  assert.equal(result.status,200);assert.equal((await result.arrayBuffer()).byteLength,1000);
  assert.equal(result.headers.get('X-Press-Asset-Version'),'fixed_version');assert.equal(result.headers.get('X-Press-Retain-Until'),'2000000000000');
  assert.equal(result.headers.get('Access-Control-Allow-Origin'),'*');assert.equal(result.headers.get('Accept-Ranges'),'bytes');
  assert.equal(calls[0][1].redirect,'manual');assert(!('cf' in calls[0][1]));assert.match(calls[0][0],/versionId=fixed_version$/);
  const head=await run(new Request(url,{method:'HEAD'}),setup(),globalThis.fetch);assert.equal(head.status,200);assert.equal(await head.text(),'');assert.equal(calls[1][1].method,'HEAD');
 }finally{globalThis.fetch=original;}
});
test('bounded, open-ended, suffix and clipped byte ranges preserve exact metadata',async()=>{
 const original=globalThis.fetch;
 try{
  for(const [range,start,end] of [['bytes=100-199',100,199],['bytes=900-',900,999],['bytes=-100',900,999],['bytes=950-2000',950,999]]){
   globalThis.fetch=async (target,options)=>{assert.equal(options.headers.Range,range);assert(!('cf' in options));return upstream({start,end});};
   const result=await run(new Request(url,{headers:{Range:range}}),setup(),globalThis.fetch);
   assert.equal(result.status,206);assert.equal(result.headers.get('Content-Range'),`bytes ${start}-${end}/1000`);assert.equal((await result.arrayBuffer()).byteLength,end-start+1);
  }
 }finally{globalThis.fetch=original;}
});
test('bad or unsatisfiable ranges cannot fetch an entire object',async()=>{
 const original=globalThis.fetch;globalThis.fetch=async()=>{throw Error('must not fetch');};
 try{
  for(const value of ['bytes=1000-','bytes=5-1','bytes=-0','bytes=1-2,4-5','bytes=9007199254740992-']){
   const result=await run(new Request(url,{headers:{Range:value}}),setup(),globalThis.fetch);assert.equal(result.status,416);assert.equal(result.headers.get('Content-Range'),'bytes */1000');
  }
 }finally{globalThis.fetch=original;}
});
test('wrong object version, range, type, encoding, length and redirect fail closed',async()=>{
 const original=globalThis.fetch;
 try{
  for(const factory of [()=>upstream({version:'replacement'}),()=>upstream({mime:'text/html'}),()=>upstream({status:302}),()=>new Response('x',{headers:{'Content-Type':'video/mp4','Content-Length':'1','x-amz-version-id':'fixed_version'}})]){
   globalThis.fetch=async()=>factory();const result=await run(new Request(url),setup(),globalThis.fetch);assert.equal(result.status,502);assert.equal(result.headers.get('Cache-Control'),'no-store');
  }
  globalThis.fetch=async()=>upstream({start:100,end:299});assert.equal((await run(new Request(url,{headers:{Range:'bytes=100-199'}}),setup(),globalThis.fetch)).status,502);
  globalThis.fetch=async()=>upstream();assert.equal((await run(new Request(url,{headers:{Range:'bytes=100-199'}}),setup(),globalThis.fetch)).status,502);
 }finally{globalThis.fetch=original;}
});
test('missing or corrupt index entries and arbitrary origins, paths and methods are rejected',async()=>{
 for(const bad of [url+'?versionId=other',url.replace('publisher.test','evil.test'),url.replace(digest,'BAD')])assert.equal((await run(new Request(bad),setup(),globalThis.fetch)).status,404);
 assert.equal((await run(new Request(url,{method:'POST'}),setup(),globalThis.fetch)).status,405);
 assert.equal((await run(new Request(url),{...env,__mapping:null},globalThis.fetch)).status,404);
 assert.equal((await run(new Request(url),env,globalThis.fetch)).status,503);
});
test('opaque bytes are attachments and empty files preserve their identity',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async()=>upstream({size:0,mime:'application/octet-stream'});
 try{
  const result=await run(new Request(url.replace('.mp4','.bin')),setup('bin',0),globalThis.fetch);assert.equal(result.status,200);assert.equal(await result.text(),'');assert.match(result.headers.get('Content-Disposition'),/^attachment;/);
 }finally{globalThis.fetch=original;}
});
test('stream truncation is detected instead of silently completing',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async()=>new Response(new Uint8Array(10),{headers:{'Content-Length':'1000','Content-Type':'video/mp4','x-amz-version-id':'fixed_version'}});
 try{const result=await run(new Request(url),setup(),globalThis.fetch);assert.equal(result.status,200);await assert.rejects(()=>result.arrayBuffer());}finally{globalThis.fetch=original;}
});
