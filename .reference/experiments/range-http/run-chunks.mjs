#!/usr/bin/env node
// Actual browser, static compressed physical chunks, unchanged logical SQLite.
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import {gzipSync} from 'node:zlib';
import assert from 'node:assert/strict';
import {pathToFileURL,fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const [guavajamArg,chromeArg,dbArg,casesArg,outArg,profilesArg='local,controlled',termsArg='transaction,absentmatchingwordxyz,contract']=process.argv.slice(2);
assert(outArg,'usage: run-chunks.mjs GUAVAJAM CHROME DATABASE CASES OUTPUT [profiles] [terms]');
const guavajam=path.resolve(guavajamArg),database=path.resolve(dbArg),output=path.resolve(outArg);
const {chromium}=await import(pathToFileURL(path.join(guavajam,'node_modules/playwright/index.mjs')));
const runtime=path.join(guavajam,'node_modules/@sqlite.org/sqlite-wasm/dist');
const packageDir=path.join(here,'node_modules/sqlite-wasm-http');
const driver=await fs.readFile(path.join(packageDir,'dist/vfs-sync-http.js'),'utf8');
let mapped=driver.replace("from 'lru-cache'","from './lru-cache.mjs'");
const readStart=mapped.indexOf('        xRead: function ('),readEnd=mapped.indexOf('        xSync: function (',readStart);
assert(readStart>0&&readEnd>readStart);
mapped=mapped.slice(0,readStart)+`        xRead: function(fid,dest,n,offset){
            try {
                if(!openFiles[fid])return capi.SQLITE_NOTFOUND;
                const raw=options.readRange(Number(offset),n);
                if(raw.byteLength!==n)return capi.SQLITE_IOERR_SHORT_READ;
                if(dest instanceof Uint8Array)dest.set(raw);else wasm.heap8u().set(raw,dest);
                return capi.SQLITE_OK;
            }catch(error){console.error(error);return capi.SQLITE_IOERR;}
        },
`+mapped.slice(readEnd);
const openStart=mapped.indexOf('        xOpen: function ('),openEnd=mapped.indexOf('\n    };\n    sqlite3.vfs.installVfs',openStart);
assert(openStart>0&&openEnd>openStart);
mapped=mapped.slice(0,openStart)+`        xOpen: function(vfs,name,fid,flags,pOutFlags){
            if(!name||typeof name!=='number')return capi.SQLITE_CANTOPEN;
            const fh=Object.create(null);fh.fid=fid;fh.url=wasm.cstrToJs(name);fh.sq3File=new sqlite3_file(fid);
            fh.sq3File.$pMethods=httpIoMethods.pointer;fh.size=BigInt(options.rawBytes);openFiles[fid]=fh;
            wasm.poke(pOutFlags,capi.SQLITE_OPEN_READONLY,'i32');return capi.SQLITE_OK;
        }`+mapped.slice(openEnd);
const hookStart=mapped.indexOf('    sqlite3.oo1.DB.dbCtorHelper.setVfsPostOpenSql('),hookEnd=mapped.indexOf('\n    });',hookStart)+8;
assert(hookStart>0&&hookEnd>hookStart);mapped=mapped.slice(0,hookStart)+mapped.slice(hookEnd);
const files=new Map(),hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const add=(name,raw,type,compress=false)=>files.set(name,{raw:compress?gzipSync(raw):raw,type,compressed:compress});
add('index.html',Buffer.from('<!doctype html><html lang="en"><title>Compressed chunks experiment</title></html>'),'text/html');
add('vfs-chunks.js',Buffer.from(mapped),'text/javascript',true);
for(const [name,source] of [['sqlite3.mjs',path.join(runtime,'index.mjs')],['sqlite3.wasm',path.join(runtime,'sqlite3.wasm')],
  ['worker-chunks.mjs',path.join(here,'worker-chunks.mjs')],['fflate.mjs',path.join(here,'node_modules/fflate/esm/browser.js')],
  ['lru-cache.mjs',path.join(here,'node_modules/lru-cache/index.mjs')],['endianness.js',path.join(packageDir,'dist/endianness.js')],['vfs-http-types.js',path.join(packageDir,'dist/vfs-http-types.js')]]){
  add(name,await fs.readFile(source),name.endsWith('.wasm')?'application/wasm':'text/javascript',true);
}
const databaseBytes=await fs.readFile(database),databaseHash=hash(databaseBytes),chunkBytes=1048576;
const manifest={databaseSha256:databaseHash,rawBytes:databaseBytes.length,chunkBytes,chunks:[]};
for(let offset=0;offset<databaseBytes.length;offset+=chunkBytes){
  const raw=databaseBytes.subarray(offset,Math.min(offset+chunkBytes,databaseBytes.length)),encoded=gzipSync(raw,{level:9});
  const name=hash(encoded)+'.chunk.gz';add(name,encoded,'application/octet-stream');
  manifest.chunks.push({offset,rawBytes:raw.length,gzipBytes:encoded.length,rawSha256:hash(raw),gzipSha256:hash(encoded),url:name});
}
const manifestName=databaseHash+'.manifest.json';add(manifestName,Buffer.from(JSON.stringify(manifest)),'application/json',true);
const requests=[];let active='';const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const server=http.createServer(async(req,res)=>{
  const [profile,...rest]=new URL(req.url,'http://localhost').pathname.slice(1).split('/'),name=rest.join('/')||'index.html',file=files.get(name);
  if(!file){res.writeHead(404);res.end();return;}
  const record={sample:active,method:req.method,name,bytes:0,range:req.headers.range??null};requests.push(record);
  if(profile==='controlled')await delay(40);
  res.writeHead(200,{'Content-Type':file.type,'Content-Length':file.raw.length,'Cache-Control':'public,max-age=31536000,immutable',ETag:'"'+hash(file.raw)+'"',...(file.compressed?{'Content-Encoding':'gzip'}:{})});
  if(req.method==='HEAD'){res.end();return;}
  if(profile!=='controlled'){record.bytes=file.raw.length;res.end(file.raw);return;}
  let closed=false;res.on('close',()=>{closed=true;});
  for(let i=0;i<file.raw.length&&!closed;i+=65536){const chunk=file.raw.subarray(i,i+65536);await delay(chunk.length/1250000*1000);if(!closed){record.bytes+=chunk.length;res.write(chunk);}}
  res.end();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin='http://127.0.0.1:'+server.address().port,allCases=JSON.parse(await fs.readFile(casesArg,'utf8'));
const browser=await chromium.launch({executablePath:path.resolve(chromeArg),args:['--no-sandbox']});
const report={browser:browser.version(),node:process.version,zlib:process.versions.zlib,sqliteWasm:'3.54.1-build1',fflate:'0.8.2',database,databaseBytes:databaseBytes.length,databaseSha256:databaseHash,
  harnessSha256:hash(await fs.readFile(fileURLToPath(import.meta.url))),workerSha256:hash(await fs.readFile(path.join(here,'worker-chunks.mjs'))),
  sourceDriver:'sqlite-wasm-http@1.2.0 registration scaffold with xOpen/xRead replaced by physical chunk adapter; obsolete post-open hook removed',driverOriginalSha256:hash(Buffer.from(driver)),driverServedSha256:hash(Buffer.from(mapped)),
  queryCasesSha256:hash(await fs.readFile(casesArg)),manifest,conditions:'Fresh context/worker per case; same-origin static immutable 1 MiB gzip9 chunks fetched by sync XHR, ordinary application/octet-stream with no Content-Encoding on chunks; fflate synchronous gunzip; decoded LRU32MiB + compressed LRU8MiB + SQLite cache8MiB; uncontrolled additional browser HTTP cache. Controlled40ms per response +10Mbit/s per response. Length checks only; chunk SHA256 declared but not checked in browser. One complete logical database, no server SQL.',samples:[]};
await fs.mkdir(path.dirname(output),{recursive:true});const save=()=>fs.writeFile(output,JSON.stringify(report,null,2)+'\n');
try{
  for(const profile of profilesArg.split(','))for(const term of termsArg.split(',')){
    const query=allCases.find(q=>q.kind==='passages'&&q.term===term&&q.edition==='all'&&q.sort==='relevance'&&q.page===1);
    assert(query&&typeof query.expected_count==='number','Missing baseline oracle '+term);active=profile+'/'+term;
    const context=await browser.newContext(),page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(origin+'/'+profile+'/index.html');
    const result=await page.evaluate(async options=>await new Promise(resolve=>{
      const start=performance.now(),worker=new Worker(new URL('worker-chunks.mjs',location.href),{type:'module'});let opened=null;
      const timer=setTimeout(()=>{worker.terminate();resolve({ok:false,error:'120 second worker deadline',opened,roundTripMs:performance.now()-start});},120000);
      worker.onerror=event=>{clearTimeout(timer);worker.terminate();resolve({ok:false,error:event.message,opened,roundTripMs:performance.now()-start});};
      worker.onmessage=({data})=>{if(data.phase==='opened'){opened=data;return;}if(data.phase==='complete'){clearTimeout(timer);worker.terminate();resolve({...data,opened,roundTripMs:performance.now()-start});}};
      worker.postMessage(options);
    }),{database:origin+'/'+profile+'/'+databaseHash+'.sqlite',manifest:origin+'/'+profile+'/'+manifestName,query});
    const sampleRequests=requests.filter(r=>r.sample===active),chunkRequests=sampleRequests.filter(r=>r.name.endsWith('.chunk.gz'));
    const sample={profile,term,...result,errors,chunkRequests:chunkRequests.length,databaseNetworkBytes:chunkRequests.reduce((a,r)=>a+r.bytes,0),
      rangeRequests:sampleRequests.filter(r=>r.range).length,manifestNetworkBytes:sampleRequests.filter(r=>r.name.endsWith('.manifest.json')).reduce((a,r)=>a+r.bytes,0),
      runtimeNetworkBytes:sampleRequests.filter(r=>!r.name.endsWith('.manifest.json')&&!r.name.endsWith('.chunk.gz')).reduce((a,r)=>a+r.bytes,0)};
    report.samples.push(sample);await save();console.log(JSON.stringify(sample));await context.close();
  }
}finally{await save();await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
