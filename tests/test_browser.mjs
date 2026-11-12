import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,firefox,webkit} from 'playwright';
import axe from 'axe-core';
const fixtureFile=path.resolve(process.argv[2]),output=path.resolve(process.argv[3]);
const fixture=JSON.parse(await fs.readFile(fixtureFile,'utf8'));
const root=path.join(path.dirname(fixtureFile),'view');
await fs.mkdir(output,{recursive:true});
const reports=[];
async function routeAssets(route){
 const request=route.request(),url=new URL(request.url());
 // Axe creates a local blob worker in Firefox; it performs no network read.
 if(url.protocol==='file:'||url.protocol==='data:'||(url.protocol==='blob:'&&url.pathname.startsWith('null/')))return route.continue();
 assert.equal(url.origin,fixture.origin,'view requested an unapproved remote resource: '+url.href);
 const record=fixture.assets[url.pathname];assert(record,'unknown fixture asset request');
 const raw=Buffer.from(record.body,'base64');const range=request.headers().range;
 let status=200,body=raw;const headers={'Content-Type':record.mime,'Accept-Ranges':'bytes','Access-Control-Allow-Origin':'*'};
 if(range){const match=/^bytes=(\d+)-(\d*)$/.exec(range);assert(match,'unexpected native player range');const start=Number(match[1]),end=match[2]?Math.min(Number(match[2]),raw.length-1):raw.length-1;
  if(start>=raw.length){status=416;body=Buffer.alloc(0);headers['Content-Range']='bytes */'+raw.length;}
  else{status=206;body=raw.subarray(start,end+1);headers['Content-Range']=`bytes ${start}-${end}/${raw.length}`;}}
 headers['Content-Length']=String(body.length);await route.fulfill({status,headers,body});
}
async function open(context,name,width=1280,scheme='light',enlarge=false){
 const page=await context.newPage();page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme:scheme});
 await page.goto(pathToFileURL(path.join(root,name)).href);await page.evaluate(()=>document.fonts.ready);
 if(enlarge)await page.evaluate(()=>{const root=document.documentElement;root.style.fontSize=parseFloat(getComputedStyle(root).fontSize)*2+'px';root.style.lineHeight='1.8';root.style.letterSpacing='.12em';root.style.wordSpacing='.16em';});
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));return page;
}
for(const [engine,type] of Object.entries({chromium,firefox,webkit})){
 const browser=await type.launch();const context=await browser.newContext({deviceScaleFactor:1,reducedMotion:'reduce'});await context.route('**/*',routeAssets);
 try{
  for(const name of ['index.html','supporting/note.html','supporting/lantern-current.html','supporting/lantern-alternate.html']){
   for(const [width,scheme,enlarge] of [[360,'light',false],[1280,'dark',false],[360,'light',true]]){
    const page=await open(context,name,width,scheme,enlarge);
    const shape=await page.evaluate(()=>({h1:document.querySelectorAll('h1').length,ids:[...document.querySelectorAll('[id]')].map(n=>n.id),width:document.documentElement.scrollWidth,viewport:innerWidth,scripts:document.scripts.length,main:document.querySelectorAll('main').length,csp:document.querySelector('meta[http-equiv="Content-Security-Policy"]').content}));
    assert.equal(shape.h1,1);assert.equal(shape.main,1);assert.equal(shape.scripts,0);assert.equal(shape.ids.length,new Set(shape.ids).size);if(shape.width>shape.viewport+1){const overflow=await page.evaluate(()=>[...document.querySelectorAll('main *')].map(n=>({tag:n.tagName,id:n.id,cls:n.className?.baseVal??n.className,left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right})).filter(n=>n.right>innerWidth+1||n.left< -1).slice(0,12));assert.fail(`${engine} ${name} ${width} enlarged=${enlarge} overflows: ${JSON.stringify(overflow)}`);}assert(shape.csp.includes("script-src 'none'"));
    await page.evaluate(axe.source);const audit=await page.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}));
    assert.deepEqual(audit.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[],`${engine} accessibility ${name}`);
    reports.push({engine,name,width,scheme,enlarge,ids:shape.ids.length,violations:0});
    if(name==='index.html'&&width===360&&!enlarge)await page.screenshot({path:path.join(output,engine+'-mobile.png'),fullPage:true});
    await page.close();
   }
  }
  // Every fragment enters a fresh document with the same screen settings.
  for(const [name,anchor] of [['index.html','eq-1'],['index.html','wide'],['supporting/note.html','claim'],['supporting/note.html','document-metadata'],['supporting/note.html','quote-heading'],['supporting/note.html','quote-body']]){
   const page=await open(context,name,360,'light',true);await page.goto(pathToFileURL(path.join(root,name)).href+'#'+anchor);await page.waitForTimeout(100);
   const target=await page.locator('[id="'+anchor+'"]').evaluate(node=>{const rect=node.getBoundingClientRect(),mark=getComputedStyle(node,'::before');return {top:rect.top,bottom:rect.bottom,height:innerHeight,marker:mark.content,position:mark.position};});
   assert(target.top>=-1&&target.top<target.height-10&&target.bottom>0,`${engine} obscured fragment ${anchor}: ${JSON.stringify(target)}`);
   if(anchor==='quote-heading'||anchor==='quote-body')assert.notEqual(target.marker,'none',`${engine} missing quotation target marker`);
   await page.close();
  }
  const page=await open(context,'index.html',360);await page.keyboard.press('Tab');assert.equal(await page.locator(':focus').textContent(),'Skip to content');await page.keyboard.press('Enter');assert.equal(await page.locator(':focus').evaluate(n=>n.tagName),'MAIN');
  const code=page.locator('pre').filter({hasText:'long_identifier'});await code.focus();const before=await code.evaluate(n=>n.scrollLeft);await page.keyboard.press('ArrowRight');await page.waitForTimeout(200);assert((await code.evaluate(n=>n.scrollLeft))>before,engine+' code overflow is not keyboard scrollable');
  // Native controls use exact generated MP3/H.264/AAC bytes and a loaded WebVTT track.
  const movie=page.locator('video');await movie.evaluate(video=>{video.muted=true;video.play().catch(e=>{video.dataset.playError=e.name;});});await page.waitForFunction(()=>{const v=document.querySelector('video');return v.currentTime>0||v.dataset.playError;});assert((await movie.evaluate(v=>v.currentTime))>0,engine+' video did not advance: '+await movie.evaluate(v=>v.dataset.playError));
  assert.equal(await movie.evaluate(v=>v.textTracks.length),1);await movie.evaluate(v=>{v.textTracks[0].mode='showing';});await page.waitForFunction(()=>document.querySelector('video').textTracks[0].cues?.length>0,{},{timeout:15000});await movie.evaluate(v=>v.pause());
  const sound=page.locator('audio');await sound.evaluate(audio=>{audio.muted=true;audio.play().catch(e=>{audio.dataset.playError=e.name;});});await page.waitForFunction(()=>{const a=document.querySelector('audio');return a.currentTime>0||a.dataset.playError;});assert((await sound.evaluate(v=>v.currentTime))>0,engine+' audio did not advance: '+await sound.evaluate(v=>v.dataset.playError));
  await page.close();
  if(engine==='chromium')for(const name of ['index.html','supporting/note.html','supporting/lantern-current.html'])for(const format of ['A4','Letter']){
   const print=await open(context,name);await print.emulateMedia({media:'print',colorScheme:'light'});await print.pdf({path:path.join(output,path.basename(name,'.html')+'-'+format+'.pdf'),format,printBackground:true,preferCSSPageSize:false});await print.close();
  }
  console.log(engine+' screen, fragment, keyboard and native playback checks passed.');
 }finally{await context.close();await browser.close();}
}
await fs.writeFile(path.join(output,'browser-report.json'),JSON.stringify({revision:fixture.revision,checks:reports},null,2)+'\n');
console.log(`GuavaJam: ${reports.length} screen/accessibility checks, 18 fresh fragment entries, native playback/captions in three engines, and six PDFs passed.`);
