// Experimental measurement worker. The production downloader is unchanged.
import sqlite3InitModule from './sqlite3.mjs';
import {loadDatabase} from './search-download.js';
globalThis.sqlite3ApiConfig = {disable: {vfs: {
  opfs: true, 'opfs-vfs': true, 'opfs-wl': true, 'opfs-sahpool': true, kvvfs: true
}}};

const canonical = value => {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) =>
    [key, key === 'citation_json' && item ? canonical(JSON.parse(item)) : key === 'score' ? Number(item.toPrecision(11)) : canonical(item)]));
  return value;
};

self.onmessage = async ({data}) => {
  let db;
  try {
    const started = performance.now();
    const sqlite3 = await sqlite3InitModule({locateFile: name => new URL(name, import.meta.url).href});
    const moduleMs = performance.now() - started;
    const loaded = await loadDatabase(sqlite3, data);
    db = loaded.db;
    self.postMessage({phase: 'loaded', moduleMs, metrics: loaded.metrics});
    const queries = [];
    for (const item of data.queries) {
      const params = Object.fromEntries(['kind','term','edition','document','sort','page'].map(k=>[k,item[k]]));
      const modes = {};
      let oracle;
      for (const mode of ['baseline', 'optimized']) {
        const timings = [];
        let rows, count;
        for (let i = 0; i < data.queryRepetitions + 1; i++) {
          const begin = performance.now();
          count = db.selectObjects(item[mode + '_count'].sql, item[mode + '_count'].bind)[0].total;
          rows = db.selectObjects(item[mode + '_page'].sql, item[mode + '_page'].bind);
          const elapsed = performance.now() - begin;
          if (i) timings.push(elapsed);
        }
        const result = canonical({count, rows});
        const expected = canonical({count:item.expected_count, rows:item.expected_rows});
        if (JSON.stringify(result) !== JSON.stringify(expected)) throw Error('Oracle mismatch: ' + JSON.stringify(params) + ' ' + mode);
        if (oracle && JSON.stringify(result) !== oracle) throw Error('Pipeline mismatch');
        oracle = JSON.stringify(result);
        timings.sort((a,b)=>a-b);
        modes[mode] = {medianMs:timings[Math.floor(timings.length / 2)], timings};
      }
      queries.push({params, count:item.expected_count, rows:item.expected_rows.length, identical:true, ...modes});
    }
    const heapAfterQueries = sqlite3.wasm.heap8u().byteLength;
    db.close(); db = null;
    self.postMessage({phase:'complete', ok:true, queries, sqliteVersion:sqlite3.version.libVersion,
      metrics:{...loaded.metrics, moduleMs, heapAfterQueries}, elapsedMs:performance.now()-started});
  } catch (error) {
    db?.close();
    self.postMessage({phase:'complete', ok:false, error:String(error.stack || error)});
  }
};
