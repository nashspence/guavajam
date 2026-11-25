// Research worker: unchanged production download/decompression, pinned SQLite WASM.
import sqlite3InitModule from './sqlite3.mjs';
import {loadDatabase} from './search-download.js';
globalThis.sqlite3ApiConfig={disable:{vfs:{opfs:true,'opfs-vfs':true,'opfs-wl':true,'opfs-sahpool':true,kvvfs:true}}};

function canonical(value){
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>
    [key,key.endsWith('_json')&&item?canonical(JSON.parse(item)):key==='score'?Number(item.toPrecision(11)):canonical(item)]));
  return value;
}
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));

self.onmessage=async({data})=>{
  let db;
  try{
    const start=performance.now();
    const sqlite3=await sqlite3InitModule({locateFile:name=>new URL(name,import.meta.url).href});
    const moduleMs=performance.now()-start;
    const loaded=await loadDatabase(sqlite3,data);db=loaded.db;
    self.postMessage({phase:'loaded',metrics:loaded.metrics,moduleMs});
    const cases=[];
    for(const item of data.cases){
      const originalPage=db.selectObjects(item.original_page.sql,item.original_page.bind);
      if(!same(originalPage,item.expected_original_page))throw Error('Original page identity changed: '+item.label);
      const timings={};
      for(const mode of ['full_evidence','window_evidence']){
        const query=item[mode],samples=[];
        let rows;
        for(let rep=0;rep<4;rep++){
          const begin=performance.now();rows=db.selectObjects(query.sql,query.bind);
          if(rep)samples.push(performance.now()-begin);
        }
        samples.sort((a,b)=>a-b);
        timings[mode]={medianMs:samples[1],samples,heapBytes:sqlite3.wasm.heap8u().byteLength};
        if(rows.length!==1||!rows[0].excerpt.includes('\uE000'))throw Error('Missing highlighted evidence');
        if(mode==='window_evidence'){
          if(!same(rows,item.expected_window_rows))throw Error('Window oracle mismatch: '+item.label);
          const row=rows[0];
          const body=db.selectObjects('SELECT value FROM text_pool WHERE id=?',[row.body_id])[0].value;
          const bytes=new TextEncoder().encode(body);
          const sourceWindow=new TextDecoder('utf-8',{fatal:true}).decode(bytes.subarray(row.start_byte,row.start_byte+row.byte_count));
          const plain=row.excerpt.replaceAll('\uE000','').replaceAll('\uE001','').replace(/^…/,'').replace(/…$/,'');
          if(!sourceWindow.includes(plain))throw Error('Evidence does not equal its source window');
          if(row.byte_count>8192)throw Error('Window byte budget exceeded');
        }
      }
      cases.push({label:item.label,kind:item.kind,term:item.term,recordId:item.record_id,bodyId:item.body_id,
        originalPageAndCitationUnchanged:true,sourceExactHighlightedEvidence:true,...timings});
      self.postMessage({phase:'case',result:cases.at(-1)});
    }
    const heapAfterQueries=sqlite3.wasm.heap8u().byteLength;
    db.close();db=null;
    self.postMessage({phase:'complete',ok:true,sqliteVersion:sqlite3.version.libVersion,cases,
      metrics:{...loaded.metrics,moduleMs,heapAfterQueries},elapsedMs:performance.now()-start});
  }catch(error){
    db?.close();self.postMessage({phase:'complete',ok:false,error:String(error.stack||error)});
  }
};
