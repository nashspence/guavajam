/* Queries a disposable, read-only SQLite derivative. Signed sources remain authoritative. */
import sqlite3InitModule from './sqlite3.mjs';

let db;
const PAGE_SIZE = 20;
const MAX_DATABASE_BYTES = 256 * 1024 * 1024;
const ORDER = Object.freeze({
  relevance: 'score ASC, w.latest_published DESC, w.title COLLATE NOCASE, w.id',
  recent: 'w.latest_published DESC, w.title COLLATE NOCASE, w.id',
  oldest: 'w.latest_published ASC, w.title COLLATE NOCASE, w.id',
  title: 'w.title COLLATE NOCASE ASC, w.id',
  editions: 'w.edition_count DESC, w.title COLLATE NOCASE, w.id'
});

function literalQuery(value) {
  // Plain words with prefix matching, never user-supplied SQL or FTS operators.
  const words = String(value).slice(0, 256).match(/[\p{L}\p{N}]+/gu) || [];
  return words.slice(0, 12).map(word => '"' + word + '"*').join(' AND ');
}

function search({term = '', keyword = '', subject = '', sort = 'recent', page = 1}) {
  const fts = literalQuery(term);
  let base, binds;
  if (fts) {
    // Materialize before grouping: bm25 must run in the FTS MATCH context.
    // The winning record supplies BOTH score and URL, including historical hits.
    base = `WITH hits AS MATERIALIZED (
      SELECT s.work_id, s.id, s.url,
        bm25(search_fts, 4.0, 2.0, 1.0, 2.0, 1.0, 0.5) AS score
      FROM search_fts JOIN search_records s ON s.id = search_fts.rowid
      WHERE search_fts MATCH ?
    ), ranked AS (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY work_id ORDER BY score, id DESC) AS position
      FROM hits
    )
    SELECT w.id, w.title, w.authors, w.abstract, w.latest_published,
      w.edition_count, r.path, r.url AS repository, m.score, m.url AS hit_url
    FROM works w JOIN repositories r ON w.repository_id = r.id
      JOIN ranked m ON m.work_id = w.id AND m.position = 1`;
    binds = [fts];
  } else {
    base = `SELECT w.id, w.title, w.authors, w.abstract, w.latest_published,
      w.edition_count, r.path, r.url AS repository, 0 AS score, NULL AS hit_url
    FROM works w JOIN repositories r ON w.repository_id = r.id`;
    binds = [];
  }
  base += ` WHERE (? = '' OR EXISTS
               (SELECT 1 FROM keywords k WHERE k.work_id = w.id AND k.name = ?))
             AND (? = '' OR EXISTS
               (SELECT 1 FROM subjects s WHERE s.work_id = w.id AND s.key = ?))`;
  binds.push(String(keyword), String(keyword), String(subject), String(subject));
  const total = db.selectObjects(`SELECT COUNT(*) AS total FROM (${base})`, binds)[0].total;
  const requested = Number.isSafeInteger(page) && page > 0 ? page : 1;
  const number = Math.min(requested, Math.max(1, Math.ceil(total / PAGE_SIZE)));
  const order = Object.hasOwn(ORDER, sort) ? ORDER[sort] : ORDER.recent;
  const rows = db.selectObjects(base + ` ORDER BY ${order} LIMIT ? OFFSET ?`,
    [...binds, PAGE_SIZE, (number - 1) * PAGE_SIZE]);
  return {rows, total, page: number, pageSize: PAGE_SIZE};
}

self.onmessage = async event => {
  const {action, id = 0} = event.data || {};
  try {
    if (action === 'init') {
      if (db) throw new Error('Search is already initialized');
      // This database lives only in worker memory; never initialize persistent
      // browser storage or the optional OPFS proxy, even on isolated hosts.
      globalThis.sqlite3ApiConfig = {disable: {vfs: {
        opfs: true, 'opfs-vfs': true, 'opfs-wl': true, 'opfs-sahpool': true, kvvfs: true
      }}};
      const sqlite3 = await sqlite3InitModule({
        locateFile: name => new URL(name, import.meta.url).href
      });
      const response = await fetch(new URL('./index.sqlite', import.meta.url), {cache: 'no-cache'});
      if (!response.ok) throw new Error('Cannot fetch the local search database');
      if (Number(response.headers.get('content-length')) > MAX_DATABASE_BYTES) {
        throw new Error('Search database exceeds the memory limit');
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (!bytes.length || bytes.byteLength > MAX_DATABASE_BYTES) {
        throw new Error('Search database is empty or exceeds the memory limit');
      }
      db = new sqlite3.oo1.DB();
      const ptr = sqlite3.wasm.allocFromTypedArray(bytes);
      db.checkRc(sqlite3.capi.sqlite3_deserialize(db.pointer, 'main', ptr,
        bytes.byteLength, bytes.byteLength,
        sqlite3.capi.SQLITE_DESERIALIZE_FREEONCLOSE | sqlite3.capi.SQLITE_DESERIALIZE_READONLY));
      db.exec('PRAGMA query_only=ON');
      if (db.selectValue("SELECT value FROM metadata WHERE key='schema'") !== 'guavajam.search.v1') {
        throw new Error('Unsupported search database schema');
      }
      const keywords = db.selectObjects(`SELECT name AS key, name, COUNT(*) AS total
        FROM keywords GROUP BY name ORDER BY name COLLATE NOCASE, name`);
      const subjects = db.selectObjects(`SELECT key, label AS name, COUNT(*) AS total
        FROM subjects GROUP BY key, label ORDER BY label COLLATE NOCASE, key`);
      self.postMessage({id, ok: true, ready: true, keywords, subjects});
    } else if (action === 'search' && db) {
      self.postMessage({id, ok: true, ...search(event.data)});
    } else {
      throw new Error('Search database is not initialized');
    }
  } catch (error) {
    if (action === 'init' && db) { db.close(); db = undefined; }
    self.postMessage({id, ok: false, error: String(error.message || error)});
  }
};
