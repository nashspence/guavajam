#!/usr/bin/env node
// Actual HTTP + Chromium + pinned SQLite WASM; no interactive DOM qualification.
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import {pathToFileURL} from 'node:url';

const [gArg, chromeArg, dataArg, casesArg, outArg, variantsArg='baseline,external,compact', profilesArg='local,controlled', repeatsArg='1'] = process.argv.slice(2);
assert(outArg, 'usage: browser_matrix.mjs GUAVAJAM CHROME DATABASE_DIRECTORY QUERY_CASES OUTPUT [variants] [profiles] [repetitions]');
const guavajam=path.resolve(gArg), databaseDirectory=path.resolve(dataArg), output=path.resolve(outArg);
const {chromium} = await import(pathToFileURL(path.join(guavajam,'node_modules/playwright/index.mjs')));
const assets=path.join(guavajam,'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets');
const runtime=path.join(guavajam,'node_modules/@sqlite.org/sqlite-wasm/dist');
const allQueries=JSON.parse(await fs.readFile(casesArg,'utf8'));
// Include all query-case semantics once per layout. Large timing populations
// can be bounded by supplying a selected exported case file.
const queries=allQueries;
const files=new Map(), requests=[];
let activeSample='';
function add(name, data, type, encode=false) {
  const body=encode?gzipSync(data):data;
  files.set(name,{body,type,encode});
}
add('index.html',Buffer.from('<!doctype html><html lang="en"><title>Search research</title><h1>Search research</h1></html>'),'text/html');
for (const [name,source] of [['sqlite3.mjs',path.join(runtime,'index.mjs')],['sqlite3.wasm',path.join(runtime,'sqlite3.wasm')],
  ['search-download.js',path.join(assets,'search-download.js')],['search-worker.js',path.join(assets,'search-worker.js')],
  ['browser_worker.mjs',new URL('./browser_worker.mjs',import.meta.url)]]) {
  add(name,await fs.readFile(source),name.endsWith('.wasm')?'application/wasm':'text/javascript',true);
}
const cases=[];
for (const variant of variantsArg.split(',')) {
  const raw=await fs.stat(path.join(databaseDirectory,variant+'.sqlite'));
  const compressed=await fs.readFile(path.join(databaseDirectory,variant+'.sqlite.gz'));
  const digest=crypto.createHash('sha256').update(compressed).digest('hex');
  const relative='work.'+digest+'.sqlite.gz';
  add(relative,compressed,'application/gzip');
  cases.push({variant,relative,rawBytes:raw.size,gzipBytes:compressed.length});
}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const server=http.createServer(async(req,res)=>{
  const [profile,...rest]=new URL(req.url,'http://localhost').pathname.slice(1).split('/');
  const name=rest.join('/') || 'index.html';
  const file=files.get(name);
  if (!file) {res.writeHead(404);res.end();return;}
  const record={sample:activeSample,name,bytes:0};requests.push(record);
  if(profile==='controlled') await delay(40);
  res.writeHead(200,{'Content-Type':file.type,'Content-Length':file.body.length,
    'Cache-Control':'public,max-age=31536000,immutable',...(file.encode?{'Content-Encoding':'gzip'}:{})});
  if(profile!=='controlled'){record.bytes=file.body.length;res.end(file.body);return;}
  let closed=false;res.on('close',()=>{closed=true;});
  for(let pos=0;pos<file.body.length&&!closed;pos+=65536){
    const chunk=file.body.subarray(pos,pos+65536);
    await delay(chunk.length/1250000*1000);
    if(!closed){record.bytes+=chunk.length;res.write(chunk);}
  }
  res.end();
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({executablePath:path.resolve(chromeArg),args:['--no-sandbox']});
const report={browser:browser.version(),playwright:'1.64.1',wasmPackage:'3.54.1-build1',
  scope:'Actual HTTP, immutable cache, production stream loader and query SQL in WASM; no DOM/mobile/Firefox/WebKit qualification',
  controlledNetwork:'40 ms before each response; 10 Mbit/s per response; runtime assets HTTP-gzip',samples:[],blocked:[]};
const save=()=>fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
try{
 for(const item of cases){
  if(item.rawBytes>256*1024*1024){report.blocked.push({...item,reason:'Unchanged production downloader 256 MiB resident database bound'});await save();continue;}
  for(const profile of profilesArg.split(',')) for(let repetition=0;repetition<Number(repeatsArg);repetition++){
   const context=await browser.newContext();
   const page=await context.newPage();
   await page.goto(origin+'/'+profile+'/index.html');
   for(const temperature of ['cold','warm']){
    activeSample=[item.variant,profile,repetition,temperature].join('/');
    const result=await page.evaluate(async options=>await new Promise((resolve,reject)=>{
      const start=performance.now();
      const worker=new Worker(new URL('browser_worker.mjs',location.href),{type:'module'});
      const timeout=setTimeout(()=>{worker.terminate();reject(Error('Worker deadline 180 seconds'));},180000);
      let readyMs;
      worker.onerror=e=>{clearTimeout(timeout);worker.terminate();reject(Error(e.message));};
      worker.onmessage=({data})=>{
        if(data.phase==='loaded'){readyMs=performance.now()-start;return;}
        if(data.phase==='complete'){clearTimeout(timeout);worker.terminate();resolve({...data,readyMs,roundTripMs:performance.now()-start});}
      };
      worker.postMessage(options);
    }),{database:origin+'/'+profile+'/'+item.relative,databaseSize:item.rawBytes,transferSize:item.gzipBytes,encoding:'gzip',queries,queryRepetitions:3});
    const sampleRequests=requests.filter(x=>x.sample===activeSample);
    const sample={...item,profile,repetition,temperature,...result,
      databaseNetworkBytes:sampleRequests.filter(x=>x.name===item.relative).reduce((n,x)=>n+x.bytes,0),
      runtimeNetworkBytes:sampleRequests.filter(x=>x.name!==item.relative).reduce((n,x)=>n+x.bytes,0)};
    report.samples.push(sample);await save();
    console.log(JSON.stringify({variant:item.variant,profile,temperature,readyMs:result.readyMs,ok:result.ok,
      heapBytes:result.metrics?.heapAfterQueries,networkBytes:sample.databaseNetworkBytes,error:result.error}));
    assert(result.ok,result.error);
   }
   await context.close();
  }
 }
}finally{await save();await browser.close();await new Promise(r=>server.close(r));}
