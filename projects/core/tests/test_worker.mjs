import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
globalThis.crypto ||= webcrypto;
import worker,{signedRead} from '../src/guavajam_core/resources/storage/worker.js';
const digest='b'.repeat(64),key='sha256/'+digest+'.mp4',url='https://assets.publisher.test/'+key;
const env={ASSET_HOST:'assets.publisher.test',S3_ENDPOINT:'https://objects.example.test',S3_REGION:'test-region',S3_BUCKET:'test-bucket',S3_ACCESS_KEY_ID:'example-id',S3_SECRET_ACCESS_KEY:'example-secret'};
function setup(extension='mp4',size=1000){
 const entry={schema:'press.asset.v1',sha256:digest,extension,version_id:'fixed_version',etag:'"fixed-etag"',retention_mode:'governance',byte_count:size,content_type:({mp4:'video/mp4',mp3:'audio/mpeg',bin:'application/octet-stream',vtt:'text/vtt',svg:'image/svg+xml'})[extension],retain_until:2000000000000};
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
function upstream({size=1000,start,end,version='fixed_version',etag='"fixed-etag"',mime='video/mp4',status}={}){
 const partial=start!==undefined;const length=partial?end-start+1:size;
 return new Response(new Uint8Array(length),{status:status??(partial?206:200),headers:{'Content-Length':String(length),'Content-Type':mime,ETag:etag,...(version===null?{}:{'x-amz-version-id':version}),...(partial?{'Content-Range':`bytes ${start}-${end}/${size}`}:{})}});
}
test('S3 signing binds method, object identity and ETag without query credentials',async()=>{
 const signed=await signedRead(env,key,'fixed_version','GET','bytes=1-2',new Date('2026-11-12T00:00:00Z'),'"fixed-etag"');
 assert.equal(signed.url,'https://objects.example.test/test-bucket/'+key+'?versionId=fixed_version');
 assert.match(signed.headers.Authorization,/Credential=example-id\/20261112\/test-region\/s3\/aws4_request/);
 assert.equal(signed.headers.range,'bytes=1-2');assert.equal(signed.headers['x-amz-date'],'20261112T000000Z');
 assert(!signed.url.includes('example-secret'));
 await assert.rejects(()=>signedRead({...env,S3_ENDPOINT:'http://evil.example/'},key,'fixed_version','GET'));
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
   globalThis.fetch=async (target,options)=>{assert.equal(options.headers.range,range);assert(!('cf' in options));return upstream({start,end});};
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
  for(const factory of [()=>upstream({version:'replacement'}),()=>upstream({mime:'text/html'}),()=>upstream({status:302}),()=>new Response('x',{headers:{'Content-Type':'video/mp4','Content-Length':'1','x-amz-version-id':'fixed_version','ETag':'"fixed-etag"'}})]){
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
 globalThis.fetch=async()=>new Response(new Uint8Array(10),{headers:{'Content-Length':'1000','Content-Type':'video/mp4','x-amz-version-id':'fixed_version','ETag':'"fixed-etag"'}});
 try{const result=await run(new Request(url),setup(),globalThis.fetch);assert.equal(result.status,200);await assert.rejects(()=>result.arrayBuffer());}finally{globalThis.fetch=original;}
});


test('Python and Worker match independently generated SigV4 reference vectors',async()=>{
 const {readFile}=await import('node:fs/promises');
 const cases=JSON.parse(await readFile(new URL('../../../tests/fixtures/storage/signing.json',import.meta.url),'utf8'));
 for(const c of cases){
  const signed=await signedRead(c.env,c.key,c.version,c.method,c.range,new Date(c.time),c.etag);
  assert.equal(signed.url,c.url,c.name);assert.equal(signed.headers.Authorization,c.authorization,c.name);
 }
});
test('unversioned GET, HEAD and ranges use If-Match and expose no fabricated version or retention',async()=>{
 const config=setup();Object.assign(config.__mapping,{version_id:null,retain_until:null,retention_mode:null});
 for(const [method,range] of [['GET',null],['HEAD',null],['GET','bytes=10-19']]){
  const result=await run(new Request(url,{method,headers:range?{Range:range}:undefined}),config,async(target,options)=>{
   assert(!target.includes('versionId'));assert.equal(options.headers['if-match'],'"fixed-etag"');
   return upstream({version:null,...(range?{start:10,end:19}:{})});
  });
  assert.equal(result.status,range?206:200);assert.equal(result.headers.get('ETag'),'"fixed-etag"');
  assert.equal(result.headers.get('X-Press-Asset-Version'),null);assert.equal(result.headers.get('X-Press-Retain-Until'),'0');
  if(method==='GET')assert.equal((await result.arrayBuffer()).byteLength,range?10:1000);
 }
});
test('unversioned replacement and ignored If-Match fail closed for full and partial reads',async()=>{
 const config=setup();Object.assign(config.__mapping,{version_id:null,retain_until:null,retention_mode:null});
 for(const range of [null,'bytes=0-9']){
  const request=new Request(url,{headers:range?{Range:range}:undefined});
  for(const factory of [()=>upstream({version:null,etag:'"replacement"',...(range?{start:0,end:9}:{})}),()=>new Response('',{status:412})]){
   const result=await run(request,config,async()=>factory());assert.equal(result.status,502);assert.equal(result.headers.get('Cache-Control'),'no-store');
  }
 }
});
test('versioning without Object Lock needs no retention claim',async()=>{
 const config=setup();Object.assign(config.__mapping,{retain_until:null,retention_mode:null});
 const result=await run(new Request(url),config,async()=>upstream());
 assert.equal(result.status,200);assert.equal(result.headers.get('X-Press-Retain-Until'),'0');
});
test('opaque version IDs are encoded rather than restricted to B2 characters',async()=>{
 const config=setup(),version='opaque.+/=%';config.__mapping.version_id=version;
 const result=await run(new Request(url),config,async(target)=>{
  assert.equal(new URL(target).searchParams.get('versionId'),version);return upstream({version});
 });
 assert.equal(result.status,200);assert.equal(result.headers.get('X-Press-Asset-Version'),encodeURIComponent(version));
});
test('malformed ETags and impossible retention claims never reach the asset origin',async()=>{
 for(const bad of [{etag:null},{etag:'W/"weak"'},{etag:'*'},{version_id:null},{retention_mode:null},{version_id:'null'}]){
  const config=setup();Object.assign(config.__mapping,bad);
  assert.equal((await run(new Request(url),config,async()=>{throw Error('must not read');})).status,503);
 }
});

test('the mutable null version uses an ETag but advertises no immutable version',async()=>{
 const config=setup();Object.assign(config.__mapping,{version_id:'null',retain_until:null,retention_mode:null});
 const result=await run(new Request(url),config,async(target,options)=>{
  assert.equal(new URL(target).searchParams.get('versionId'),'null');assert.equal(options.headers['if-match'],'"fixed-etag"');
  return upstream({version:'null'});
 });
 assert.equal(result.status,200);assert.equal(result.headers.get('X-Press-Asset-Version'),null);
});
