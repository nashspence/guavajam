#!/usr/bin/env node
// Run the unchanged production worker, including anchor targeting/highlights.
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {gzipSync} from 'node:zlib';
const [gArg,chromeArg,dirArg,outArg,variantsArg='baseline,compact,segmented'] = process.argv.slice(2);
assert(outArg,'usage: production_worker_parity.mjs GUAVAJAM CHROME DATA_DIRECTORY OUTPUT [VARIANTS]');
const g=path.resolve(gArg),directory=path.resolve(dirArg),output=path.resolve(outArg);
const {chromium}=await import(pathToFileURL(path.join(g,'node_modules/playwright/index.mjs')));
const assets=path.join(g,'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets');
const files=new Map();
files.set('index.html',{body:Buffer.from('<!doctype html><title>Worker parity</title>'),type:'text/html'});
for(const [name,p] of [['sqlite3.mjs',path.join(g,'node_modules/@sqlite.org/sqlite-wasm/dist/index.mjs')],
 ['sqlite3.wasm',path.join(g,'node_modules/@sqlite.org/sqlite-wasm/dist/sqlite3.wasm')],
 ['search-worker.js',path.join(assets,'search-worker.js')],['search-download.js',path.join(assets,'search-download.js')]]) {
 files.set(name,{body:gzipSync(await fs.readFile(p)),gzip:true,type:name.endsWith('.wasm')?'application/wasm':'text/javascript'});
}
const cases=[];
for(const variant of variantsArg.split(',')){
 const raw=await fs.stat(path.join(directory,variant+'.sqlite'));
 const body=await fs.readFile(path.join(directory,variant+'.sqlite.gz'));
 const name='work.'+crypto.createHash('sha256').update(body).digest('hex')+'.sqlite.gz';
 files.set(name,{body,type:'application/gzip'});
 cases.push({variant,name,rawBytes:raw.size,gzipBytes:body.length});
}
const server=http.createServer((req,res)=>{
 const file=files.get(new URL(req.url,'http://localhost').pathname.slice(1)||'index.html');
 if(!file){res.writeHead(404);res.end();return;}
 res.writeHead(200,{'Content-Type':file.type,'Content-Length':file.body.length,'Cache-Control':'public,max-age=31536000,immutable',...(file.gzip?{'Content-Encoding':'gzip'}:{})});res.end(file.body);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({executablePath:path.resolve(chromeArg),args:['--no-sandbox']});
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?
 Object.fromEntries(Object.entries(value).filter(([k])=>k!=='id'||typeof value.id!=='number'||!('ok' in value)).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,k==='citation_json'&&v?canonical(JSON.parse(v)):canonical(v)])):value;
const queries=[];
for(const kind of ['documents','passages','editions'])for(const term of ['contract','transaction','Café','absentmatchingwordxyz']){
 queries.push({action:'search',kind,term,edition:'all',sort:'relevance',page:1});
}
queries.push({action:'search',kind:'passages',term:'contract',edition:'latest',sort:'publication',page:2});
queries.push({action:'search',kind:'editions',term:'transaction v1',edition:'all',sort:'oldest',page:1});
queries.push({action:'search',kind:'documents',term:'',edition:'latest',sort:'title',page:1});
const oracle=[];
const report={browser:browser.version(),wasmPackage:'3.54.1-build1',worker:'unchanged search-worker.js at GuavaJam54fce07',
 scope:'complete production-worker messages including exact-source anchor targeting and display highlights; no DOM or signed-source rendering',cases:[]};
await fs.mkdir(path.dirname(output),{recursive:true});
try{
 for(const item of cases){
  if(item.rawBytes>256*1024*1024){report.cases.push({...item,blocked:'production 256MiB guard'});continue;}
  const context=await browser.newContext(), page=await context.newPage();await page.goto(origin);
  const result=await page.evaluate(async({origin,item,queries})=>{
   const worker=new Worker(new URL('search-worker.js',location.href),{type:'module'});let nextId=0;
   const call=data=>new Promise((resolve,reject)=>{
    const id=++nextId,begin=performance.now();const timer=setTimeout(()=>reject(Error('worker timeout')),120000);
    worker.onmessage=({data})=>{if(data.id===id){clearTimeout(timer);resolve({data,ms:performance.now()-begin});}};
    worker.onerror=e=>{clearTimeout(timer);reject(Error(e.message));};worker.postMessage({...data,id});
   });
   const initialized=await call({action:'init',scope:'work',database:origin+'/'+item.name,databaseSize:item.rawBytes,transferSize:item.gzipBytes,encoding:'gzip',facets:false});
   if(!initialized.data.ok)throw Error(initialized.data.error);
   const results=[];for(const query of queries)results.push(await call(query));worker.terminate();return {initialized,results};
  },{origin,item,queries});
  const checks=[];
  for(let i=0;i<queries.length;i++){
   const {data,ms}=result.results[i];assert(data.ok,data.error);
   const normalized=JSON.stringify(canonical(data));
   if(!oracle[i])oracle[i]=normalized;else assert.equal(normalized,oracle[i],'worker parity '+item.variant+' '+JSON.stringify(queries[i]));
   checks.push({query:queries[i],total:data.total,rows:data.rows.length,ms,identical:true,
    canonicalResponseSha256:crypto.createHash('sha256').update(normalized).digest('hex')});
  }
  report.cases.push({...item,loadMs:result.initialized.ms,checks});await fs.writeFile(output,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({variant:item.variant,queries:checks.length,parity:true,loadMs:result.initialized.ms}));
  await context.close();
 }
}finally{await fs.writeFile(output,JSON.stringify(report,null,2)+'\n');await browser.close();await new Promise(r=>server.close(r));}
