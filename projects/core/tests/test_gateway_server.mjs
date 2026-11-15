import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {configuration,server} from '../src/guavajam_core/resources/storage/server.mjs';
const env={ASSET_HOST:'assets.publisher.test',S3_ENDPOINT:'https://objects.example.test',S3_REGION:'test-region',S3_BUCKET:'test-bucket',S3_ACCESS_KEY_ID:'fixture-id',S3_SECRET_ACCESS_KEY:'fixture-secret'};

test('credential files are bounded and distinct from direct values',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'gateway-'));
 try{
  const file=join(directory,'key');await writeFile(file,'fixture-secret\n');
  const values={...env};delete values.S3_SECRET_ACCESS_KEY;values.S3_SECRET_ACCESS_KEY_FILE=file;
  assert.equal(configuration(values).S3_SECRET_ACCESS_KEY,'fixture-secret');
  assert.throws(()=>configuration({...env,S3_SECRET_ACCESS_KEY_FILE:file}));
  assert.throws(()=>configuration({...env,ASSET_HOST:'localhost:8080'}));
  await writeFile(file,'x'.repeat(65537));assert.throws(()=>configuration(values));
 }finally{await rm(directory,{recursive:true});}
});

test('HTTP adapter preserves the shared gateway identity, ranges and isolation',async()=>{
 const digest='b'.repeat(64),key='sha256/'+digest+'.mp4';
 const mapping={schema:'press.asset.v1',sha256:digest,extension:'mp4',version_id:null,etag:'"fixture-etag"',retention_mode:null,retain_until:null,byte_count:1000,content_type:'video/mp4'};
 const realFetch=globalThis.fetch;
 const listener=server(env);await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+listener.address().port;
 globalThis.fetch=async(target,options)=>{
  if(!String(target).startsWith(env.S3_ENDPOINT))return realFetch(target,options);
  assert.equal(options.cache,'no-store');
  if(String(target).includes('/.pins/')){
   const raw=JSON.stringify(mapping);return new Response(raw,{headers:{'Content-Type':'application/json','Content-Length':String(Buffer.byteLength(raw))}});
  }
  assert.equal(options.headers['if-match'],'"fixture-etag"');
  const partial=Boolean(options.headers.range);const length=partial?10:1000;
  return new Response(options.method==='HEAD'?null:new Uint8Array(length),{status:partial?206:200,headers:{'Content-Type':'video/mp4','Content-Length':String(length),ETag:'"fixture-etag"',...(partial?{'Content-Range':'bytes 10-19/1000'}:{})}});
 };
 try{
  let result=await realFetch(origin+'/'+key,{headers:{Host:'untrusted.example',Authorization:'never-forward'}});
  assert.equal(result.status,200);assert.equal((await result.arrayBuffer()).byteLength,1000);
  result=await realFetch(origin+'/'+key,{headers:{Range:'bytes=10-19'}});assert.equal(result.status,206);assert.equal((await result.arrayBuffer()).byteLength,10);
  result=await realFetch(origin+'/'+key,{method:'HEAD'});assert.equal(result.status,200);assert.equal(await result.text(),'');
  assert.equal((await realFetch(origin+'/'+key+'?override=1')).status,404);
  assert.equal((await realFetch(origin+'/'+key,{method:'POST'})).status,405);
  assert.equal((await realFetch(origin+'/sha256/.pins/private.json')).status,404);
  assert.equal((await realFetch(origin+'/healthz')).status,200);
 }finally{globalThis.fetch=realFetch;listener.closeAllConnections();await new Promise(resolve=>listener.close(resolve));}
});
