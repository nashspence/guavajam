// Browser-only benchmark harness. Production loading is imported unchanged.
import sqlite3InitModule from './sqlite3.mjs';
import {loadDatabase} from './search-download.js';

globalThis.sqlite3ApiConfig = {disable: {vfs: {
  opfs: true, 'opfs-vfs': true, 'opfs-wl': true, 'opfs-sahpool': true, kvvfs: true
}}};

async function buffered(sqlite3, options, compressed) {
  const start = performance.now();
  const response = await fetch(options.database, {
    mode: 'same-origin', redirect: 'error', cache: compressed ? 'force-cache' : 'no-cache'
  });
  if (!response.ok) throw new Error('Benchmark artifact unavailable');
  const stream = compressed ? response.body.pipeThrough(new DecompressionStream('gzip')) : response.body;
  const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
  if (bytes.byteLength !== options.databaseSize) throw new Error('Benchmark size mismatch');
  const downloadMs = performance.now() - start;
  const db = new sqlite3.oo1.DB();
  const copyStart = performance.now();
  const pointer = sqlite3.wasm.allocFromTypedArray(bytes);
  const copyMs = performance.now() - copyStart;
  // FREEONCLOSE also transfers ownership on deserialization failure.
  db.checkRc(sqlite3.capi.sqlite3_deserialize(db.pointer, 'main', pointer,
    bytes.byteLength, bytes.byteLength,
    sqlite3.capi.SQLITE_DESERIALIZE_FREEONCLOSE | sqlite3.capi.SQLITE_DESERIALIZE_READONLY));
  db.exec('PRAGMA query_only=ON');
  return {db, metrics: {totalMs: performance.now() - start, downloadMs, copyMs,
    decodedBytes: bytes.byteLength, jsFullBufferBytes: bytes.byteLength,
    wasmHeapBytes: sqlite3.wasm.heap8u().byteLength, allocatedBytes: bytes.byteLength}};
}

self.onmessage = async ({data}) => {
  let db;
  try {
    const start = performance.now();
    const sqlite3 = await sqlite3InitModule({locateFile: name => new URL(name, import.meta.url).href});
    const moduleMs = performance.now() - start;
    const loaded = data.mode === 'gzip-stream'
      ? await loadDatabase(sqlite3, data)
      : await buffered(sqlite3, data, data.mode === 'gzip-buffer');
    db = loaded.db;
    self.postMessage({phase: 'loaded', metrics: {...loaded.metrics, moduleMs}});
    const results = [];
    for (const query of data.queries) {
      const rows = db.selectObjects(query.sql, query.params || []);
      const count = query.countSql ? db.selectValue(query.countSql, query.params || []) : rows.length;
      // Warm the statement/OS paths before comparing query CPU. No time gate.
      db.selectObjects(query.sql, query.params || []);
      const timings = [];
      for (let i = 0; i < 5; i++) {
        const before = performance.now();
        db.selectObjects(query.sql, query.params || []);
        timings.push(performance.now() - before);
      }
      results.push({name: query.name, rows, count, timings});
    }
    const heapAfterQueries = sqlite3.wasm.heap8u().byteLength;
    db.close(); db = undefined;
    self.postMessage({phase: 'complete', ok: true, results,
      metrics: {...loaded.metrics, moduleMs, heapAfterQueries}, sqliteVersion: sqlite3.version.libVersion});
  } catch (error) {
    db?.close();
    self.postMessage({phase: 'complete', ok: false, error: String(error.stack || error)});
  }
};
