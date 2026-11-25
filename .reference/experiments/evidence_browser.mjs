#!/usr/bin/env node
// Local HTTP browser qualification of source-exact evidence windows, not WAN benchmarking.
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const [repoArg='repos/guavajam',chromeArg='tools/browser/chrome-headless-shell-linux64/chrome-headless-shell',
  databaseArg='results/storage/latest/evidence.sqlite',casesArg='results/storage/latest/evidence-browser-cases.json',
  outputArg='results/browser/evidence-windows.json',provenanceArg='native-heading exploratory corpus, not final GuavaJam NAV extraction']=process.argv.slice(2);
const repo=path.resolve(repoArg),database=path.resolve(databaseArg),output=path.resolve(outputArg);
const {chromium}=await import(pathToFileURL(path.join(repo,'node_modules/playwright/index.mjs')));
const assets=path.join(repo,'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets');
const runtime=path.join(repo,'node_modules/@sqlite.org/sqlite-wasm/dist');
const compressed=await fs.readFile(database+'.gz');
const rawBytes=(await fs.stat(database)).size;
const relative='work.'+crypto.createHash('sha256').update(compressed).digest('hex')+'.sqlite.gz';
const files=new Map([
  ['index.html',{body:Buffer.from('<!doctype html><html><title>Evidence experiment</title></html>'),type:'text/html'}],
  [relative,{body:compressed,type:'application/gzip'}],
]);
for(const [name,source] of [['sqlite3.mjs',path.join(runtime,'index.mjs')],['sqlite3.wasm',path.join(runtime,'sqlite3.wasm')],
  ['search-download.js',path.join(assets,'search-download.js')],['evidence_worker.mjs',new URL('./evidence_worker.mjs',import.meta.url)]]){
  files.set(name,{body:await fs.readFile(source),type:name.endsWith('.wasm')?'application/wasm':'text/javascript'});
}
const requests=[];
const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname.slice(1)||'index.html',file=files.get(name);
  if(!file){res.writeHead(404);res.end();return;}
  requests.push({name,bytes:file.body.length});
  res.writeHead(200,{'Content-Type':file.type,'Content-Length':file.body.length,'Cache-Control':'public,max-age=31536000,immutable'});
  res.end(file.body);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({executablePath:path.resolve(chromeArg),args:['--no-sandbox']});
const cases=JSON.parse(await fs.readFile(casesArg,'utf8'));
let report;
try{
  const context=await browser.newContext(),page=await context.newPage();
  await page.goto(origin+'/index.html');
  const result=await page.evaluate(async input=>new Promise((resolve,reject)=>{
    const start=performance.now(),worker=new Worker(new URL('evidence_worker.mjs',location.href),{type:'module'});
    const deadline=setTimeout(()=>{worker.terminate();reject(Error('Evidence worker deadline'));},180000);
    let readyMs;
    worker.onerror=e=>{clearTimeout(deadline);worker.terminate();reject(Error(e.message));};
    worker.onmessage=({data})=>{
      if(data.phase==='loaded')readyMs=performance.now()-start;
      if(data.phase==='complete'){clearTimeout(deadline);worker.terminate();resolve({...data,readyMs,roundTripMs:performance.now()-start});}
    };
    worker.postMessage(input);
  }),{database:origin+'/'+relative,databaseSize:rawBytes,transferSize:compressed.length,encoding:'gzip',cases});
  report={browser:browser.version(),playwright:'1.64.1',wasmPackage:'3.54.1-build1',
    scope:'Cold local HTTP load with unchanged production stream loader; three warm timing repetitions; '+provenanceArg,
    semantics:'Primary matching/ranking/citation unchanged; source-exact bounded evidence intentionally chooses a different excerpt',
    databaseRawBytes:rawBytes,databaseGzipBytes:compressed.length,
    databaseNetworkBytes:requests.filter(r=>r.name===relative).reduce((n,r)=>n+r.bytes,0),...result};
  await context.close();
  assert(result.ok,result.error);
}finally{
  if(report){await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(report,null,2)+'\n');}
  await browser.close();await new Promise(r=>server.close(r));
}
console.log(JSON.stringify({output,ok:report.ok,browser:report.browser,sqliteVersion:report.sqliteVersion,
  readyMs:report.readyMs,heapAfterQueries:report.metrics.heapAfterQueries,
  cases:report.cases.map(c=>({kind:c.kind,fullMs:c.full_evidence.medianMs,windowMs:c.window_evidence.medianMs}))}));
