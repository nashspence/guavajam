#!/usr/bin/env node
// Local controlled HTTP Range experiment, not deployment or production driver.
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import {gzipSync} from 'node:zlib';
import assert from 'node:assert/strict';
import {pathToFileURL,fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const [guavajamArg,chromeArg,dbArg,casesArg,outArg,profilesArg='local,controlled',termsArg='transaction,absentmatchingwordxyz,contract',adapterArg='original',validatorArg='none']=process.argv.slice(2);
assert(outArg,'usage: run.mjs GUAVAJAM CHROME DATABASE CASES OUTPUT [profiles] [terms] [original|postopen-adapted] [none|etag]');
assert(['none','etag'].includes(validatorArg));
const guavajam=path.resolve(guavajamArg),database=path.resolve(dbArg),output=path.resolve(outArg);
const {chromium}=await import(pathToFileURL(path.join(guavajam,'node_modules/playwright/index.mjs')));
const runtime=path.join(guavajam,'node_modules/@sqlite.org/sqlite-wasm/dist');
const packageDir=path.join(here,'node_modules/sqlite-wasm-http');
const packageInfo=JSON.parse(await fs.readFile(path.join(packageDir,'package.json'),'utf8'));
assert.equal(packageInfo.version,'1.2.0');
const driver=await fs.readFile(path.join(packageDir,'dist/vfs-sync-http.js'),'utf8');
assert.equal(driver.split("from 'lru-cache'").length,2);
let driverMapped=driver.replace("from 'lru-cache'","from './lru-cache.mjs'");
if(adapterArg==='postopen-adapted'){
  const start=driverMapped.indexOf('    sqlite3.oo1.DB.dbCtorHelper.setVfsPostOpenSql(');
  assert(start>0,'Expected legacy hook');
  const end=driverMapped.indexOf('\n    });',start)+8;
  assert(end>start,'Expected hook closing');
  driverMapped=driverMapped.slice(0,start)+driverMapped.slice(end);
}else assert.equal(adapterArg,'original');
const files=new Map();
const add=(name,raw,type,compress=false)=>files.set(name,{raw:compress?gzipSync(raw):raw,type,compressed:compress});
add('index.html',Buffer.from('<!doctype html><html lang="en"><title>HTTP Range experiment</title></html>'),'text/html');
add('vfs-sync-http.js',Buffer.from(driverMapped),'text/javascript',true);
for(const [name,source] of [['sqlite3.mjs',path.join(runtime,'index.mjs')],['sqlite3.wasm',path.join(runtime,'sqlite3.wasm')],
  ['worker.mjs',path.join(here,'worker.mjs')],['lru-cache.mjs',path.join(here,'node_modules/lru-cache/index.mjs')],
  ['endianness.js',path.join(packageDir,'dist/endianness.js')],['vfs-http-types.js',path.join(packageDir,'dist/vfs-http-types.js')]]){
  add(name,await fs.readFile(source),name.endsWith('.wasm')?'application/wasm':'text/javascript',true);
}
const databaseBytes=await fs.readFile(database);
const databaseHash=crypto.createHash('sha256').update(databaseBytes).digest('hex');
add(databaseHash+'.sqlite',databaseBytes,'application/vnd.sqlite3');
const requests=[];let active='';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const server=http.createServer(async(req,res)=>{
  const [profile,...rest]=new URL(req.url,'http://localhost').pathname.slice(1).split('/');
  const name=rest.join('/')||'index.html',file=files.get(name);
  if(!file){res.writeHead(404);res.end();return;}
  const record={sample:active,method:req.method,name,bytes:0,range:req.headers.range??null};requests.push(record);
  const headers={'Content-Type':file.type,'Cache-Control':'public,max-age=31536000,immutable',...(file.compressed?{'Content-Encoding':'gzip'}:{})};
  let body=file.raw,status=200;
  if(name.endsWith('.sqlite')){
    headers['Accept-Ranges']='bytes';
    if(validatorArg==='etag')headers.ETag='"'+databaseHash+'"';
    if(req.headers.range){
      const match=/^bytes=(\d+)-(\d+)$/.exec(req.headers.range);
      if(!match||Number(match[1])>=body.length){res.writeHead(416);res.end();return;}
      const start=Number(match[1]),end=Math.min(Number(match[2]),body.length-1);
      headers['Content-Range']=`bytes ${start}-${end}/${body.length}`;body=body.subarray(start,end+1);status=206;
    }
  }
  headers['Content-Length']=body.length;record.status=status;
  if(profile==='controlled')await delay(40);
  res.writeHead(status,headers);
  if(req.method==='HEAD'){res.end();return;}
  if(profile!=='controlled'){record.bytes=body.length;res.end(body);return;}
  let closed=false;res.on('close',()=>{closed=true;});
  for(let i=0;i<body.length&&!closed;i+=65536){
    const chunk=body.subarray(i,i+65536);await delay(chunk.length/1250000*1000);
    if(!closed){record.bytes+=chunk.length;res.write(chunk);}
  }
  res.end();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin='http://127.0.0.1:'+server.address().port;
const allCases=JSON.parse(await fs.readFile(casesArg,'utf8'));
const browser=await chromium.launch({executablePath:path.resolve(chromeArg),args:['--no-sandbox']});
const report={browser:browser.version(),sqliteWasm:'3.54.1-build1',driver:'sqlite-wasm-http@1.2.0 synchronous HTTP VFS',
  driverOriginalSha256:crypto.createHash('sha256').update(driver).digest('hex'),driverServedSha256:crypto.createHash('sha256').update(driverMapped).digest('hex'),driverAdapter:adapterArg==='original'?'Only bare lru-cache import mapped to its unchanged package ESM file. Driver logic unchanged; official GuavaJam SQLite runtime reused.':'Bare lru-cache import mapped; removed obsolete DB.dbCtorHelper.setVfsPostOpenSql registration. Wrapper explicitly sets journal_mode, busy_timeout and requested8MiBcache after open. HTTP read/cache logic unchanged; official GuavaJam SQLite runtime reused.',
  database:database,databaseBytes:databaseBytes.length,databaseSha256:databaseHash,
  rangeCacheValidator:validatorArg==='etag'?'Strong SHA256ETag for immutable database URL':'No ETag; immutable URL and Cache-Control only',
  conditions:'Fresh browser context/worker per case; same-origin HTTP; no COOP/COEP required; immutable identity-encoded SQLite ranges;8MiB SQLite cache and8MiB VFS cache;controlled profile40ms before response+10Mbit/s per response;runtime assets gzip;120second worker deadline',samples:[]};
await fs.mkdir(path.dirname(output),{recursive:true});
const save=()=>fs.writeFile(output,JSON.stringify(report,null,2)+'\n');
try{
  for(const profile of profilesArg.split(','))for(const term of termsArg.split(',')){
    const query=allCases.find(q=>q.kind==='passages'&&q.term===term&&q.edition==='all'&&q.sort==='relevance'&&q.page===1);
    assert(query&&typeof query.expected_count==='number','Missing oracle query '+term);
    active=profile+'/'+term;
    const context=await browser.newContext(),page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(origin+'/'+profile+'/index.html');
    const result=await page.evaluate(async options=>await new Promise(resolve=>{
      const start=performance.now(),worker=new Worker(new URL('worker.mjs',location.href),{type:'module'});
      let opened=null;
      const timer=setTimeout(()=>{worker.terminate();resolve({ok:false,error:'120 second worker deadline',opened,roundTripMs:performance.now()-start});},120000);
      worker.onerror=event=>{clearTimeout(timer);worker.terminate();resolve({ok:false,error:event.message,opened,roundTripMs:performance.now()-start});};
      worker.onmessage=({data})=>{
        if(data.phase==='opened'){opened=data;return;}
        if(data.phase==='complete'){clearTimeout(timer);worker.terminate();resolve({...data,opened,roundTripMs:performance.now()-start});}
      };
      worker.postMessage(options);
    }),{database:origin+'/'+profile+'/'+databaseHash+'.sqlite',query});
    const sampleRequests=requests.filter(r=>r.sample===active),dbRequests=sampleRequests.filter(r=>r.name.endsWith('.sqlite'));
    const sample={profile,term,...result,errors,dbRequests:dbRequests.length,rangeRequests:dbRequests.filter(r=>r.range).length,
      databaseNetworkBytes:dbRequests.reduce((a,r)=>a+r.bytes,0),runtimeNetworkBytes:sampleRequests.filter(r=>!r.name.endsWith('.sqlite')).reduce((a,r)=>a+r.bytes,0)};
    report.samples.push(sample);await save();console.log(JSON.stringify(sample));
    await context.close();
  }
}finally{await save();await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
