// Experimental adapter: existing HTTP VFS 1.2.0 + unchanged SQLite WASM 3.54.1.
import sqlite3InitModule from './sqlite3.mjs';
import {installSyncHttpVfs} from './vfs-sync-http.js';
globalThis.sqlite3ApiConfig = {disable:{vfs:{opfs:true,'opfs-vfs':true,'opfs-wl':true,'opfs-sahpool':true,kvvfs:true}}};

const canonical=value=>{
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>
    [key,key==='citation_json'&&item?canonical(JSON.parse(item)):key==='score'?Number(item.toPrecision(11)):canonical(item)]));
  return value;
};

self.onmessage=async({data})=>{
  let db;
  const started=performance.now();
  try{
    const sqlite3=await sqlite3InitModule({locateFile:name=>new URL(name,import.meta.url).href});
    const moduleMs=performance.now()-started;
    installSyncHttpVfs(sqlite3,{cacheSize:8192,maxPageSize:4096,timeout:20000});
    db=new sqlite3.oo1.DB({filename:data.database,flags:'r',vfs:'http'});
    db.exec('PRAGMA journal_mode=DELETE; PRAGMA busy_timeout=20000; PRAGMA cache_size=-8192; PRAGMA mmap_size=0; PRAGMA temp_store=MEMORY; PRAGMA query_only=ON;');
    const openedMs=performance.now()-started;
    self.postMessage({phase:'opened',moduleMs,openedMs,sqliteVersion:sqlite3.version.libVersion});
    const query=data.query;
    const begin=performance.now();
    const count=db.selectObjects(query.optimized_count.sql,query.optimized_count.bind)[0].total;
    const countMs=performance.now()-begin;
    const pageStart=performance.now();
    const rows=db.selectObjects(query.optimized_page.sql,query.optimized_page.bind);
    const pageMs=performance.now()-pageStart;
    const actual=canonical({count,rows}),expected=canonical({count:query.expected_count,rows:query.expected_rows});
    if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error('Result differs from native baseline oracle');
    self.postMessage({phase:'complete',ok:true,moduleMs,openedMs,countMs,pageMs,totalMs:performance.now()-started,
      count,pageRows:rows.length,oracleEqual:true,sqliteVersion:sqlite3.version.libVersion,wasmHeapBytes:sqlite3.wasm.heap8u().byteLength,
      sqliteCacheSetting:db.selectValue('PRAGMA cache_size'),vfsCacheBytes:8*1024*1024});
  }catch(error){self.postMessage({phase:'complete',ok:false,error:String(error.stack||error),totalMs:performance.now()-started});}
  finally{db?.close();}
};
