// Isolated physical-chunk research VFS. One complete logical SQLite database.
import sqlite3InitModule from './sqlite3.mjs';
import {installSyncHttpVfs} from './vfs-chunks.js';
import {gunzipSync} from './fflate.mjs';
globalThis.sqlite3ApiConfig={disable:{vfs:{opfs:true,'opfs-vfs':true,'opfs-wl':true,'opfs-sahpool':true,kvvfs:true}}};

const canonical=value=>{
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>
    [key,key==='citation_json'&&item?canonical(JSON.parse(item)):key==='score'?Number(item.toPrecision(11)):canonical(item)]));
  return value;
};
class ByteLRU{
  constructor(budget){this.budget=budget;this.size=0;this.peak=0;this.entries=new Map();}
  get(key){const value=this.entries.get(key);if(value){this.entries.delete(key);this.entries.set(key,value);}return value;}
  put(key,value){
    if(value.byteLength>this.budget)return;
    while(this.size+value.byteLength>this.budget){const oldest=this.entries.keys().next().value;this.size-=this.entries.get(oldest).byteLength;this.entries.delete(oldest);}
    this.entries.set(key,value);this.size+=value.byteLength;this.peak=Math.max(this.peak,this.size);
  }
}

self.onmessage=async({data})=>{
  let db;const started=performance.now();
  try{
    const [sqlite3,manifest]=await Promise.all([
      sqlite3InitModule({locateFile:name=>new URL(name,import.meta.url).href}),
      fetch(data.manifest).then(response=>{if(!response.ok)throw Error('Manifest HTTP '+response.status);return response.json();})
    ]);
    const moduleMs=performance.now()-started;
    if(!Number.isSafeInteger(manifest.rawBytes)||manifest.chunkBytes!==1048576)throw Error('Invalid research manifest');
    const decoded=new ByteLRU(32*1048576),compressed=new ByteLRU(8*1048576);
    const stats={readCalls:0,networkGets:0,compressedBytes:0,inflateCalls:0,inflatedBytes:0,inflateMs:0};
    const getChunk=index=>{
      let raw=decoded.get(index);if(raw)return raw;
      const info=manifest.chunks[index];if(!info)throw Error('Missing chunk descriptor');
      let encoded=compressed.get(index);
      if(!encoded){
        const xhr=new XMLHttpRequest();xhr.open('GET',new URL(info.url,data.manifest),false);xhr.responseType='arraybuffer';xhr.send();
        if(xhr.status!==200||!(xhr.response instanceof ArrayBuffer)||xhr.response.byteLength!==info.gzipBytes)throw Error('Chunk HTTP/length mismatch');
        encoded=new Uint8Array(xhr.response);stats.networkGets++;stats.compressedBytes+=encoded.byteLength;compressed.put(index,encoded);
      }
      const before=performance.now();raw=gunzipSync(encoded);stats.inflateMs+=performance.now()-before;
      stats.inflateCalls++;stats.inflatedBytes+=raw.byteLength;
      if(raw.byteLength!==info.rawBytes)throw Error('Decoded chunk length mismatch');
      decoded.put(index,raw);return raw;
    };
    const readRange=(offset,length)=>{
      stats.readCalls++;if(!Number.isSafeInteger(offset)||offset<0||offset+length>manifest.rawBytes)throw Error('Read outside manifest');
      const index=Math.floor(offset/manifest.chunkBytes),start=offset%manifest.chunkBytes;
      const raw=getChunk(index);if(start+length<=raw.byteLength)return raw.subarray(start,start+length);
      const result=new Uint8Array(length);let written=0;
      while(written<length){const absolute=offset+written,block=getChunk(Math.floor(absolute/manifest.chunkBytes)),within=absolute%manifest.chunkBytes;
        const amount=Math.min(length-written,block.byteLength-within);if(amount<=0)throw Error('Invalid chunk boundary');result.set(block.subarray(within,within+amount),written);written+=amount;}
      return result;
    };
    installSyncHttpVfs(sqlite3,{rawBytes:manifest.rawBytes,readRange});
    db=new sqlite3.oo1.DB({filename:data.database,flags:'r',vfs:'http'});
    db.exec('PRAGMA journal_mode=DELETE; PRAGMA busy_timeout=20000; PRAGMA cache_size=-8192; PRAGMA mmap_size=0; PRAGMA temp_store=MEMORY; PRAGMA query_only=ON;');
    const openedMs=performance.now()-started;
    self.postMessage({phase:'opened',moduleMs,openedMs,sqliteVersion:sqlite3.version.libVersion});
    const query=data.query,begin=performance.now();
    const count=db.selectObjects(query.optimized_count.sql,query.optimized_count.bind)[0].total,countMs=performance.now()-begin;
    const pageStart=performance.now(),rows=db.selectObjects(query.optimized_page.sql,query.optimized_page.bind),pageMs=performance.now()-pageStart;
    const actual=canonical({count,rows}),expected=canonical({count:query.expected_count,rows:query.expected_rows});
    if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error('Result differs from native baseline oracle');
    self.postMessage({phase:'complete',ok:true,moduleMs,openedMs,countMs,pageMs,totalMs:performance.now()-started,count,pageRows:rows.length,
      oracleEqual:true,sqliteVersion:sqlite3.version.libVersion,wasmHeapBytes:sqlite3.wasm.heap8u().byteLength,sqliteCacheSetting:db.selectValue('PRAGMA cache_size'),
      stats,decodedCache:{budget:decoded.budget,bytes:decoded.size,peak:decoded.peak},compressedCache:{budget:compressed.budget,bytes:compressed.size,peak:compressed.peak}});
  }catch(error){self.postMessage({phase:'complete',ok:false,error:String(error.stack||error),totalMs:performance.now()-started});}
  finally{db?.close();}
};
