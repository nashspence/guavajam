// Exercise the actual rendered draft and authenticated loopback handler in browsers.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,firefox,webkit} from 'playwright';
import {previewServer} from '../../core/src/guavajam_core/resources/storage/preview.mjs';

const fixture=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const token='d'.repeat(64),endpoint='https://private-storage.publisher.test';
const env={ASSET_HOST:new URL(fixture.origin).host,S3_ENDPOINT:endpoint,S3_REGION:'us-east-1',
  S3_BUCKET:'assets',S3_ACCESS_KEY_ID:'fixture-readonly',S3_SECRET_ACCESS_KEY:'fixture-secret'};
const originalFetch=globalThis.fetch;
let storageReads=0;
globalThis.fetch=async(target,options)=>{
  const url=new URL(target);
  assert.equal(url.origin,endpoint);
  assert(!options.headers.Cookie);
  const key=url.pathname.slice('/assets/'.length),pin=fixture.preview_manifest.assets[key];
  assert(pin);assert.equal(options.headers['if-match'],pin.etag);
  assert.equal(url.searchParams.get('versionId'),pin.version_id);
  const record=fixture.assets['/'+key],raw=Buffer.from(record.body,'base64');
  let body=raw,status=200;
  const headers={'Content-Type':record.mime,'ETag':pin.etag,'x-amz-version-id':pin.version_id};
  if(options.headers.range){
    const [,first,last]=/^bytes=(\d*)-(\d*)$/.exec(options.headers.range);
    const start=first?Number(first):Math.max(0,raw.length-Number(last));
    const end=first&&last?Math.min(Number(last),raw.length-1):raw.length-1;
    body=raw.subarray(start,end+1);status=206;
    headers['Content-Range']=`bytes ${start}-${end}/${raw.length}`;
  }
  headers['Content-Length']=String(body.length);storageReads++;
  return new Response(options.method==='HEAD'?null:body,{status,headers});
};
const listener=previewServer(env,fixture.preview_root,fixture.preview_manifest,token);
await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
const origin='http://127.0.0.1:'+listener.address().port;
try{
  for(const [engine,type] of Object.entries({chromium,firefox,webkit})){
    const browser=await type.launch(),context=await browser.newContext();
    try{
      const page=await context.newPage();page.setDefaultTimeout(15000);
      const anonymous=await page.goto(origin+'/works/sample-work/preview/index.html');
      assert.equal(anonymous.status(),404);
      const before=storageReads;
      const key=Object.keys(fixture.preview_manifest.assets)[0];
      assert.equal((await context.request.get(origin+'/_assets/'+key)).status(),404);
      assert.equal(storageReads,before);
      await page.goto(origin+'/_session?token='+token);
      assert.equal(page.url(),origin+'/');
      const [cookie]=await context.cookies();assert(cookie.httpOnly);assert.equal(cookie.sameSite,'Strict');
      await page.getByRole('link',{name:/Review draft sample-work/}).click();
      assert.equal(await page.locator('aside').first().innerText(),'PRIVATE DRAFT PREVIEW — NOT A PUBLICATION. This site is disposable; no public release is authorized.');
      assert.equal(await page.evaluate(()=>document.referrer),'');
      assert((await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content')).includes("media-src 'self'"));
      const requests=[];page.on('request',request=>requests.push(request.url()));
      for(const selector of ['video','audio']){
        const media=page.locator(selector);await media.scrollIntoViewIfNeeded();
        assert((await media.locator('source').getAttribute('src')).startsWith('/_assets/'));
        await media.evaluate(element=>{element.muted=true;element.play().catch(error=>{element.dataset.playError=error.name;});});
        await page.waitForFunction(selector=>{const element=document.querySelector(selector);return element.currentTime>0||element.dataset.playError;},selector);
        assert((await media.evaluate(element=>element.currentTime))>0,engine+' '+selector+' playback failed: '+await media.evaluate(element=>element.dataset.playError));
        await media.evaluate(element=>element.pause());
      }
      await page.locator('video').evaluate(video=>{video.textTracks[0].mode='showing';});
      await page.waitForFunction(()=>document.querySelector('video').textTracks[0].cues?.length>0);
      assert(requests.every(value=>{const url=new URL(value);return url.origin===origin&&['http:','blob:'].includes(url.protocol);}));
      const source=await context.request.get(origin+'/works/sample-work/preview/source.gfm');
      assert.equal(source.headers()['cache-control'],'private, no-store');
      assert((await source.text()).includes(fixture.origin));
      assert.equal((await context.request.get(origin+'/PREVIEW.json')).status(),404);
      const response=await context.request.get(origin+'/_assets/'+key,{headers:{Range:'bytes=0-9'}});
      // Native media supports ranges; small images are delivered whole.
      assert.equal(response.headers()['cache-control'],'private, no-store');
      const fresh=await browser.newContext();
      assert.equal((await fresh.request.get(origin+'/_assets/'+key)).status(),404);await fresh.close();
      console.log(engine+' private session, source custody, same-origin native playback/captions and anonymous denial passed.');
    }finally{await context.close();await browser.close();}
  }
}finally{
  globalThis.fetch=originalFetch;listener.closeAllConnections();await new Promise(resolve=>listener.close(resolve));
}
