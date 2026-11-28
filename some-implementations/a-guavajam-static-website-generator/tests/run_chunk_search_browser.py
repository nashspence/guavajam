"""Real installed worker/VFS, native full-scope oracle and actual HTTP fault recovery."""
import argparse,json,os,sqlite3,subprocess,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(REPO_ROOT/'tests/support'),str(Path(__file__).parent)]
from test_scoped_search import ScopedSearchTests
from a_guavajam_static_website_generator.search_index import emit_browser_assets
from a_guavajam_static_website_generator.search_chunks import emit_chunks

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 os.environ.setdefault('GUAVAJAM_SQLITE_WASM_DIR',str(REPO_ROOT/'node_modules/@sqlite.org/sqlite-wasm/dist'))
 fixture=ScopedSearchTests
 try:
  fixture.setUpClass()
  emit_browser_assets(fixture.first)
  database=fixture.work/'history.sqlite'
  with sqlite3.connect(database) as db:
   oracles=[]
   for term in ['Currentbodyselector','Historicalsupportselector','Currenttitle','Windowstartselector Windowendselector','Oversizedprefixselector']:
    query=' AND '.join('"'+word+'"*' for word in term.split())
    rows=db.execute('SELECT passage_fts.rowid,bm25(passage_fts,4,2,1,2,1,0.5) AS score FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=d.edition_id WHERE passage_fts MATCH ? ORDER BY score,e.position DESC,d.path,p.position,p.id LIMIT 20',(query,)).fetchall()
    total=db.execute('SELECT count(*) FROM passage_fts WHERE passage_fts MATCH ?',(query,)).fetchone()[0]
    oracles.append({'term':term,'total':total,'ids':[row[0] for row in rows],'scores':[row[1] for row in rows]})
   db.execute('CREATE TABLE transport_payload(id INTEGER PRIMARY KEY,body BLOB)')
   for _ in range(40):db.execute('INSERT INTO transport_payload(body) VALUES (randomblob(?))',(1024**2,))
   first=db.execute('SELECT hex(substr(body,1,16)) FROM transport_payload WHERE id=1').fetchone()[0]
  descriptor=emit_chunks(database)
  descriptor['url']='sources/work/'+descriptor['url']
  (fixture.first/'test.html').write_text('<!doctype html><title>Transport qualification</title>')
  (fixture.first/'scan-worker.js').write_text("import sqlite3InitModule from './sqlite3.mjs';\nimport {loadChunkDatabase} from './search-chunks.js';\nimport {SEARCH_BUDGETS} from './search-limits.js';\nself.onmessage=async event=>{\n  try {\n    globalThis.sqlite3ApiConfig={disable:{vfs:{opfs:true,'opfs-vfs':true,'opfs-wl':true,'opfs-sahpool':true,kvvfs:true}}};\n    const sqlite3=await sqlite3InitModule({locateFile:name=>new URL(name,import.meta.url).href,\n      wasmMemory:new WebAssembly.Memory({initial:128,maximum:SEARCH_BUDGETS.wasmHeap/65536})});\n    const transport=await loadChunkDatabase(sqlite3,event.data);\n    transport.db.exec('PRAGMA hard_heap_limit='+SEARCH_BUDGETS.sqliteHeap);\n    let total=0;\n    transport.db.exec({sql:'SELECT body FROM transport_payload ORDER BY id',\n      rowMode:'array',callback:row=>{total+=row[0].length;}});\n    const first=transport.db.selectValue('SELECT hex(substr(body,1,16)) FROM transport_payload WHERE id=1');\n    self.postMessage({ok:true,total,first,metrics:transport.store.metrics()});\n    transport.db.close();transport.dispose();\n  }catch(error){self.postMessage({ok:false,error:String(error.message||error)});}\n};")
  manifest={'root':str(fixture.first),'descriptor':descriptor,'oracles':oracles,'payload_size':40*1024**2,'first_payload':first}
  path=fixture.root/'chunk-fixture.json';path.write_text(json.dumps(manifest))
  return subprocess.run(['node',str(Path(__file__).with_name('test_chunk_search_browser.mjs')),str(path),str(args.output)],check=False).returncode
 finally:fixture.doClassCleanups()
if __name__=='__main__':raise SystemExit(main())
