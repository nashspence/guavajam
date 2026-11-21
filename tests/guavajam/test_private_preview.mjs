import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {request as httpRequest} from 'node:http';
import {join} from 'node:path';
import worker,{previewFetch} from '../../src/guavajam/resources/storage/worker.js';
import {previewServer,privateHtml} from '../../src/guavajam/resources/storage/preview.mjs';
const digest='c'.repeat(64),key='sha256/'+digest+'.mp4';
const env={ASSET_HOST:'assets.publisher.test',S3_ENDPOINT:'https://storage.publisher.test',S3_REGION:'us-east-1',S3_BUCKET:'assets',S3_ACCESS_KEY_ID:'private-id',S3_SECRET_ACCESS_KEY:'private-secret'};
const pin={schema:'guavajam.asset.v1',sha256:digest,extension:'mp4',version_id:'fixed',etag:'"exact"',content_type:'video/mp4',byte_count:100,retain_until:null,retention_mode:null};
function data(range=false,etag='"exact"'){
 return new Response(new Uint8Array(range?10:100),{status:range?206:200,headers:{'Content-Type':'video/mp4','Content-Length':String(range?10:100),ETag:etag,'x-amz-version-id':'fixed',...(range?{'Content-Range':'bytes 0-9/100'}:{})}});
}

test('a stage pin is NOT a release, including HEAD/ranges and guessed private overrides',async()=>{
 const fetch=globalThis.fetch;let dataReads=0;
 globalThis.fetch=async target=>{
  if(String(target).includes('/released/sha256/'))return new Response('',{status:404});
  if(String(target).includes('/.pins/'))return Response.json(pin);
  dataReads++;return data();
 };
 try{
  for(const method of ['GET','HEAD'])for(const extra of [{},{Range:'bytes=0-9'},{Authorization:'Bearer private-token','X-Preview':'true'}]){
   const result=await worker.fetch(new Request('https://'+env.ASSET_HOST+'/'+key,{method,headers:extra}),{...env,PREVIEW:true});
   assert.equal(result.status,404);assert.equal(result.headers.get('Cache-Control'),'no-store');
  }
  assert.equal(dataReads,0);
  const preview=await previewFetch(new Request('https://'+env.ASSET_HOST+'/'+key),env,pin);
  assert.equal(preview.status,200);assert.equal((await preview.arrayBuffer()).byteLength,100);
  assert.equal(preview.headers.get('Cache-Control'),'private, no-store');
  assert.equal(preview.headers.get('Access-Control-Allow-Origin'),null);
  // A successful private read cannot warm a public authorization cache.
  assert.equal((await worker.fetch(new Request('https://'+env.ASSET_HOST+'/'+key),env)).status,404);
 }finally{globalThis.fetch=fetch;}
});

test('only an exact release envelope enables anonymous content; an old pin is rejected',async()=>{
 const fetch=globalThis.fetch;let envelope=pin;
 globalThis.fetch=async target=>{
  if(String(target).includes('/released/sha256/')){
   const raw=JSON.stringify(envelope);
   return new Response(raw,{headers:{'Content-Type':'application/json','Content-Length':String(Buffer.byteLength(raw))}});
  }
  return data();
 };
 try{
  assert.equal((await worker.fetch(new Request('https://'+env.ASSET_HOST+'/'+key),env)).status,503);
  envelope={schema:'guavajam.asset-release.v1',origin:'https://'+env.ASSET_HOST,publication:'a'.repeat(40),asset:pin};
  const result=await worker.fetch(new Request('https://'+env.ASSET_HOST+'/'+key),env);
  assert.equal(result.status,200);assert.equal(result.headers.get('X-GuavaJam-Asset-Publication'),'a'.repeat(40));
  assert.match(result.headers.get('Cache-Control'),/^public,/);
 }finally{globalThis.fetch=fetch;}
});

test('private server authenticates the whole site, pins its inventory and rejects rebinding',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'private-preview-'));
 const content='<!doctype html><title>PRIVATE</title><p>Private preview</p>';
 await writeFile(join(directory,'index.html'),content);
 await writeFile(join(directory,'credentials.env'),'DO NOT SERVE');
 const token='f'.repeat(64);
 const manifest={schema:'guavajam.preview.v1',origin:'https://'+env.ASSET_HOST,candidate_signed:false,assets:{[key]:pin},files:{'index.html':createHash('sha256').update(content).digest('hex')}};
 const listener=previewServer(env,directory,manifest,token);
 await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+listener.address().port,fetch=globalThis.fetch;let calls=0;
 globalThis.fetch=async(target,options)=>{
  if(!String(target).startsWith(env.S3_ENDPOINT))return fetch(target,options);
  calls++;assert(!options.headers.Cookie);assert.equal(options.headers['if-match'],'"exact"');
  assert.match(String(target),/versionId=fixed$/);return data(Boolean(options.headers.range));
 };
 try{
  assert.equal((await fetch(origin+'/')).status,404);
  assert.equal((await fetch(origin+'/_assets/'+key)).status,404);assert.equal(calls,0);
  const session=await fetch(origin+'/_session?token='+token,{redirect:'manual'});
  assert.equal(session.status,303);assert.match(session.headers.get('Set-Cookie'),/HttpOnly; SameSite=Strict/);
  const Cookie=session.headers.get('Set-Cookie').split(';')[0];
  const page=await fetch(origin+'/',{headers:{Cookie}});
  assert.match(await page.text(),/Private preview/);assert.equal(page.headers.get('Cache-Control'),'private, no-store');
  for(const path of ['/credentials.env','/PREVIEW.json','/_assets/sha256/'+'b'.repeat(64)+'.mp4','/?token='+token]){
   assert.equal((await fetch(origin+path,{headers:{Cookie}})).status,404);
  }
  const wrongHost=await new Promise((resolve,reject)=>{const req=httpRequest(origin+'/',{headers:{Cookie,Host:'attacker.example'}},res=>{res.resume();resolve(res.statusCode);});req.on('error',reject);req.end();});
  assert.equal(wrongHost,404);
  assert.equal((await fetch(origin+'/',{headers:{Cookie,Origin:'https://attacker.example'}})).status,404);
  assert.equal((await fetch(origin+'/',{headers:{Cookie,'Sec-Fetch-Site':'cross-site'}})).status,404);
  const media=await fetch(origin+'/_assets/'+key,{headers:{Cookie,Range:'bytes=0-9'}});
  assert.equal(media.status,206);assert.equal((await media.arrayBuffer()).byteLength,10);
  assert.equal(media.headers.get('Cache-Control'),'private, no-store');
  assert.equal(calls,1);
  await writeFile(join(directory,'index.html'),'CHANGED AFTER APPROVAL');
  assert.equal((await fetch(origin+'/',{headers:{Cookie}})).status,503);
 }finally{
  globalThis.fetch=fetch;listener.closeAllConnections();await new Promise(resolve=>listener.close(resolve));
  await rm(directory,{recursive:true});
 }
});

test('preview refuses stale object identities rather than serving latest',async()=>{
 const fetch=globalThis.fetch;globalThis.fetch=async()=>data(false,'"changed"');
 try{assert.equal((await previewFetch(new Request('https://'+env.ASSET_HOST+'/'+key),env,pin)).status,502);}
 finally{globalThis.fetch=fetch;}
});

test('hosted preview pins HTTPS origin, requires a session and sets Secure cookies',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'hosted-preview-'));
 const content='<h1>Private workspace</h1>';await writeFile(join(directory,'index.html'),content);
 const manifest={schema:'guavajam.preview.v1',assets:{},files:{'index.html':createHash('sha256').update(content).digest('hex')}};
 const token='e'.repeat(64),webOrigin='https://preview.publisher.test';
 assert.throws(()=>previewServer({},directory,manifest,token,'http://preview.publisher.test'));
 const listener=previewServer({},directory,manifest,token,webOrigin);
 await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
 const address='http://127.0.0.1:'+listener.address().port;
 function get(path,headers={}){return new Promise((resolve,reject)=>{
  const req=httpRequest(address+path,{headers:{Host:'preview.publisher.test',...headers}},res=>{let body='';res.on('data',v=>body+=v);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body}));});req.on('error',reject);req.end();
 });}
 try{
  assert.equal((await get('/')).status,404);
  const session=await get('/_session?token='+token);assert.equal(session.status,303);
  assert.match(session.headers['set-cookie'][0],/HttpOnly; SameSite=Strict; Secure$/);
  const Cookie=session.headers['set-cookie'][0].split(';')[0];
  assert.equal((await get('/',{Cookie})).body,content);
  assert.equal((await get('/',{Cookie,Host:'attacker.test'})).status,404);
  assert.equal((await get('/',{Cookie,Origin:'http://preview.publisher.test'})).status,404);
  assert.equal((await get('/',{Cookie,'Sec-Fetch-Site':'cross-site'})).status,404);
  assert.equal((await get('/',{Cookie,Origin:webOrigin})).status,200);
 }finally{listener.closeAllConnections();await new Promise(resolve=>listener.close(resolve));await rm(directory,{recursive:true});}
});


test('signed canonical HTML is untouched at rest; private delivery rewrites only qualified URLs',()=>{
 const url='https://'+env.ASSET_HOST+'/'+key;
 const html='<!doctype html><head><meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;; media-src https://'+env.ASSET_HOST+'"></head><body>'+
  '<p><code>'+url+'</code> src="'+url+'"</p><video src="'+url+'"></video><a href="'+url+'">download</a>'+
  '<a href="https://other.example/x.mp4">Other</a></body>';
 const manifest={schema:'guavajam.preview.v1',origin:'https://'+env.ASSET_HOST,
  candidate_signed:true,assets:{[key]:pin},files:{}};
 const original=Buffer.from(html),hash=createHash('sha256').update(original).digest('hex');
 const presented=privateHtml(original,manifest).toString('utf8');
 assert.doesNotMatch(presented, /PRIVATE SIGNED PREVIEW|PRIVATE PROPOSED EDITION|aria-label="Private preview"/);
 assert.match(presented, new RegExp('src="/_assets/'+key+'"'));
 assert.match(presented, new RegExp('href="/_assets/'+key+'"'));
 assert.match(presented, /https:\/\/other\.example\/x\.mp4/);
 assert.match(presented, new RegExp('<code>'+url+'</code> src=\"'+url+'\"'));
 assert.match(presented,/media-src https:\/\/assets\.publisher\.test &#x27;self&#x27;/);
 assert.equal(createHash('sha256').update(original).digest('hex'),hash);
 assert.match(presented, /&#x27;self&#x27;/);
});

test('explicit localhost preview may set non-Secure private cookies; other HTTP hosts are rejected',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'preview-localhost-'));
 const manifest={schema:'guavajam.preview.v1',origin:'https://'+env.ASSET_HOST,candidate_signed:true,assets:{},files:{}};
 const token='d'.repeat(64);
 try{
  assert.throws(()=>previewServer(env,directory,manifest,token,'http://preview.example'),/origin/);
  const listener=previewServer(env,directory,manifest,token,'http://127.0.0.1:8082');
  await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
  const response=await new Promise((resolve,reject)=>{
   const req=httpRequest('http://127.0.0.1:'+listener.address().port+'/_session?token='+token,
     {headers:{Host:'127.0.0.1:8082'}},res=>{res.resume();resolve(res)});
   req.on('error',reject);req.end();
  });
  assert.equal(response.statusCode,303);
  assert.match(response.headers['set-cookie'][0],/HttpOnly; SameSite=Strict/);
  assert.doesNotMatch(response.headers['set-cookie'][0],/Secure/);
  await new Promise(resolve=>listener.close(resolve));
 }finally{await rm(directory,{recursive:true,force:true});}
});
