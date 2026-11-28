import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium,firefox,webkit} from 'playwright';
const [fixturePath,output] = process.argv.slice(2);
const fixture = JSON.parse(await fs.readFile(fixturePath,'utf8')), root=path.resolve(fixture.root);
let fault, requests=0, aborted=0;
const server = http.createServer(async(req,res) => {
  try {
    const pathname=new URL(req.url,'http://localhost').pathname;
    const target=path.resolve(root,'.'+decodeURIComponent(pathname));
    assert(target.startsWith(root+path.sep));
    let data=await fs.readFile(target), status=200;
    if(pathname.endsWith('.sqlite-chunk.gz')) {
      requests++;
      const active=fault; fault=undefined;
      if(active==='missing') status=404;
      if(active==='truncate') data=data.subarray(0,-1);
      if(active==='digest') {data=Buffer.from(data);data[4]^=1;}
      if(active==='delay') {
        res.on('close',()=>{if(!res.writableEnded)aborted++;});
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      if(active==='encoded') {
        data=(await import('node:zlib')).gunzipSync(data);
        res.setHeader('Content-Encoding','identity');
      }
    }
    res.writeHead(status,{'Content-Type':target.endsWith('.html')?'text/html':target.endsWith('.wasm')?'application/wasm':
      /\.(?:mjs|js)$/.test(target)?'text/javascript':'application/octet-stream',
      'Content-Length':data.length,'Cache-Control':'no-store'});
    res.end(data);
  } catch {res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:'+server.address().port+'/';
const reports=[];
try {
  for(const name of (process.env.GUAVAJAM_BROWSER_ENGINES||'chromium,firefox,webkit').split(',')) {
    const browser=await ({chromium,firefox,webkit})[name].launch();
    try {
      const context=await browser.newContext();
      const page=await context.newPage();
      await page.goto(base+'test.html');
      await page.evaluate(()=>{window.messages=[];window.setup=()=>{
        window.worker?.terminate();
        window.messages=[];
        window.worker=new Worker('/search-worker.js',{type:'module'});
        window.worker.onmessage=event=>window.messages.push(event.data);
        window.worker.onerror=event=>window.messages.push({id:0,ok:false,error:event.message});
      };window.setup();});
      async function send(data,id) {
        await page.evaluate(data=>window.worker.postMessage(data),data);
        await page.waitForFunction(id=>window.messages.some(x=>x.id===id&&!x.progress),id,{timeout:30000});
        return page.evaluate(id=>window.messages.find(x=>x.id===id&&!x.progress),id);
      }
      const init={action:'init',id:0,scope:'history',database:base+fixture.descriptor.url,
        databaseSize:fixture.descriptor.size,transferSize:fixture.descriptor.transfer_size,
        encoding:fixture.descriptor.encoding,sha256:fixture.descriptor.sha256,
        transferSha256:fixture.descriptor.transfer_sha256,expectedGeneration:fixture.descriptor.generation};
      let ready=await send(init,0);
      assert.equal(ready.ok,true,JSON.stringify(ready));
      let id=0;
      for(const oracle of fixture.oracles) {
        const result=await send({action:'search',id:++id,scope:'history',generation:ready.generation,
          term:oracle.term,sort:'relevance',page:1},id);
        assert.equal(result.ok,true,JSON.stringify(result));
        assert.equal(result.total,oracle.total);
        assert.deepEqual(result.rows.map(x=>x.id),oracle.ids);
        assert(result.rows.every(x=>x.excerpt?.includes('\uE000')));
        for(let n=0;n<result.rows.length;n++)
          assert(Math.abs(result.rows[n].score-oracle.scores[n])<1e-9);
        assert(result.transport.networkPeak<=1);
        assert(result.transport.decodedPeak<=32*1024**2);
        assert(result.transport.compressedPeak<=8*1024**2);
      }
      const failures=[];
      for(const mode of ['missing','truncate','digest']) {
        await page.evaluate(()=>window.setup());fault=mode;
        const failed=await send(init,0);assert.equal(failed.ok,false,mode+JSON.stringify(failed));
        failures.push(mode);
        await page.evaluate(()=>window.setup());
        const retry=await send({...init,retry:true},0);assert.equal(retry.ok,true,JSON.stringify(retry));
      }
      await page.evaluate(()=>window.setup());
      assert.equal((await send({...init,expectedGeneration:'0'.repeat(64)},0)).ok,false);
      await page.evaluate(()=>window.setup()); fault='encoded';
      assert.equal((await send(init,0)).ok,true,'decoded content-encoding path');
      await page.evaluate(()=>window.setup()); fault='delay';
      const count=requests, abortBefore=aborted;
      await page.evaluate(data=>window.worker.postMessage(data),init);
      await page.waitForFunction(()=>true);
      const started=Date.now();
      while(requests===count&&Date.now()-started<10000)await new Promise(resolve=>setTimeout(resolve,20));
      assert(requests>count);
      await page.evaluate(()=>window.worker.terminate());
      await new Promise(resolve=>setTimeout(resolve,1800));
      assert(aborted>abortBefore,'Terminating the real worker aborts its synchronous chunk request');
      await page.evaluate(()=>window.setup());
      assert.equal((await send({...init,retry:true},0)).ok,true);
      // Exercise the same VFS's cross-chunk reads and both LRU evictions using
      // a native-oracled 40-MiB scan, then reread the evicted first payload.
      await page.evaluate(()=>{window.scanWorker=new Worker('/scan-worker.js',{type:'module'});
        window.scanWorker.onmessage=e=>window.scan=e.data;});
      await page.evaluate(data=>window.scanWorker.postMessage(data),init);
      await page.waitForFunction(()=>window.scan!==undefined,null,{timeout:60000});
      const scan=await page.evaluate(()=>window.scan);
      assert.equal(scan.ok,true,JSON.stringify(scan));assert.equal(scan.total,fixture.payload_size);
      assert.equal(scan.first,fixture.first_payload);
      assert(scan.metrics.decodedEvictions>0,'scan forces decoded LRU eviction');
      assert(scan.metrics.compressedEvictions>0,'scan forces compressed LRU eviction');
      assert(scan.metrics.decodedPeak<=32*1024**2&&scan.metrics.compressedPeak<=8*1024**2);
      assert(scan.metrics.networkPeak===1);
      reports.push({browser:name,passed:true,oracles:fixture.oracles.length,failures,
        cancellation_aborted:true,scan:scan.metrics});
      await context.close();
    } finally {await browser.close();}
  }
} finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
await fs.writeFile(output,JSON.stringify({passed:true,reports},null,2));
