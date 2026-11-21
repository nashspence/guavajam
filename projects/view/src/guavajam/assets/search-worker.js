/* Queries a disposable, read-only SQLite derivative. Signed sources remain authoritative. */
import sqlite3InitModule from './sqlite3.mjs';
import {loadDatabase} from './search-download.js';

let db, scope;
let subjectNames = new Map();
const PAGE_SIZE = 20;
const ORDER = Object.freeze({
  relevance: 'score ASC, w.latest_published DESC, w.title COLLATE NOCASE, w.id',
  recent: 'w.latest_published DESC, w.title COLLATE NOCASE, w.id',
  oldest: 'w.latest_published ASC, w.title COLLATE NOCASE, w.id',
  title: 'w.title COLLATE NOCASE ASC, w.id',
  editions: 'w.edition_count DESC, w.title COLLATE NOCASE, w.id'
});
const WORK_ORDER = Object.freeze({
  relevance: 'score ASC, position DESC, document_path, node_position, id',
  publication: 'position DESC, document_path, node_position, id',
  oldest: 'position ASC, document_path, node_position, id',
  title: 'title COLLATE NOCASE, position DESC, document_path, node_position, id',
  'date-newest': 'published DESC, position DESC, document_path, node_position, id',
  'date-oldest': 'published ASC, position ASC, document_path, node_position, id'
});

const PASSAGE_ORDER = Object.freeze({
  relevance: 'score ASC, document_order, node_position, id',
  publication: 'document_order ASC, node_position ASC, id',
  oldest: 'document_order DESC, node_position DESC, id',
  title: 'title COLLATE NOCASE, document_order, node_position, id'
});

function literalQuery(value) {
  // Plain words with prefix matching, never user-supplied SQL or FTS operators.
  const words = String(value).slice(0, 256).match(/[\p{L}\p{N}]+/gu) || [];
  return words.slice(0, 12).map(word => '"' + word + '"*').join(' AND ');
}

const WORK_FACETS = `,
    (SELECT json_group_array(name) FROM (
      SELECT name FROM keywords WHERE work_id=w.id ORDER BY name COLLATE NOCASE, name
    )) AS keywords_json,
    (SELECT json_group_array(json_object('key', key, 'label', label)) FROM (
      SELECT key, label FROM subjects WHERE work_id=w.id ORDER BY label COLLATE NOCASE, key
    )) AS subjects_json`;

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
      w.latest_revision, w.edition_count, r.path, r.url AS repository, m.score, m.url AS hit_url ${WORK_FACETS}
    FROM works w JOIN repositories r ON w.repository_id = r.id
      JOIN ranked m ON m.work_id = w.id AND m.position = 1`;
    binds = [fts];
  } else {
    base = `SELECT w.id, w.title, w.authors, w.abstract, w.latest_published,
      w.latest_revision, w.edition_count, r.path, r.url AS repository, 0 AS score, NULL AS hit_url ${WORK_FACETS}
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
  for (const row of rows) {
    const subjects = JSON.parse(row.subjects_json || '[]');
    for (const item of subjects) item.label = subjectNames.get(item.key) || item.label;
    row.subjects_json = JSON.stringify(subjects);
  }
  return {rows, total, page: number, pageSize: PAGE_SIZE};
}

function searchWork({term = '', kind = 'documents', edition = 'latest', document = '',
                     supporting = 'all', sort = 'publication', page = 1}) {
  const selected = ['editions', 'documents', 'passages'].includes(kind) ? kind : 'documents';
  const fts = literalQuery(term);
  const bind = [];
  let base;
  if (selected === 'editions') {
    if (fts) {
      base = `WITH hits AS MATERIALIZED (
        SELECT s.edition_id, s.id, s.url,
          bm25(search_fts, 4.0, 2.0, 1.0, 2.0, 1.0, 0.5) AS score
        FROM search_fts JOIN search_records s ON s.id=search_fts.rowid
          JOIN documents d ON d.id=s.document_id
        WHERE search_fts MATCH ? AND (? = '' OR d.path = ?)
      ), ranked AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY edition_id ORDER BY score, id DESC) AS rank
        FROM hits
      )
      SELECT e.id, 'v' || (e.position + 1) AS title, 'editions/' || e.revision || '/read/index.html' AS url,
        e.revision, e.published, e.position, e.message, '' AS document_title, '' AS document_path,
        '' AS anchor, '' AS scope, 0 AS node_position, m.score, m.url AS hit_url
      FROM editions e JOIN works w ON w.id=e.work_id
        JOIN ranked m ON m.edition_id=e.id AND m.rank=1`;
      bind.push(fts, String(document), String(document));
    } else {
      base = `SELECT e.id, 'v' || (e.position + 1) AS title, 'editions/' || e.revision || '/read/index.html' AS url,
        e.revision, e.published, e.position, e.message, '' AS document_title, '' AS document_path,
        '' AS anchor, '' AS scope, 0 AS node_position, 0 AS score, NULL AS hit_url
      FROM editions e JOIN works w ON w.id=e.work_id`;
    }
  } else if (selected === 'documents') {
    base = fts ? `WITH hits AS MATERIALIZED (
      SELECT s.document_id, bm25(search_fts, 4.0, 2.0, 1.0, 2.0, 1.0, 0.5) AS score
      FROM search_fts JOIN search_records s ON s.id=search_fts.rowid WHERE search_fts MATCH ?
    ) ` : '';
    if (fts) bind.push(fts);
    base += `SELECT d.id, d.title, d.url, e.revision, e.published, e.position,
      d.title AS document_title, d.path AS document_path, '' AS anchor, 'document' AS scope,
      0 AS node_position, ${fts ? 'm.score' : '0'} AS score, NULL AS hit_url
    FROM documents d JOIN editions e ON e.id=d.edition_id JOIN works w ON w.id=e.work_id
      ${fts ? 'JOIN hits m ON m.document_id=d.id' : ''}`;
  } else {
    // Prefer readable author/title matches over their serialized provenance
    // copies when no source-body match is available.
    base = fts ? `WITH hits AS MATERIALIZED (
      SELECT s.id, bm25(passage_fts, 4.0, 2.0, 1.0, 2.0, 1.0, 0.5) AS score,
        snippet(passage_fts, 4, char(57344), char(57345), '…', 24) AS body_excerpt,
        snippet(passage_fts, 1, char(57344), char(57345), '…', 24) AS author_excerpt,
        snippet(passage_fts, 0, char(57344), char(57345), '…', 24) AS title_excerpt,
        snippet(passage_fts, -1, char(57344), char(57345), '…', 24) AS matched_excerpt
      FROM passage_fts JOIN passage_records s ON s.id=passage_fts.rowid WHERE passage_fts MATCH ?
    ) ` : '';
    if (fts) bind.push(fts);
    base += `SELECT p.id, p.title, p.url, p.citation_url, e.revision, e.published, e.position,
      d.title AS document_title, d.path AS document_path, d.id AS document_order,
      p.anchor, p.scope, p.citation_json, p.position AS node_position,
      ${fts ? 'm.score' : '0'} AS score,
      ${fts ? `CASE
        WHEN instr(m.body_excerpt, char(57344)) > 0 THEN m.body_excerpt
        WHEN instr(m.author_excerpt, char(57344)) > 0 THEN m.author_excerpt
        WHEN instr(m.title_excerpt, char(57344)) > 0 THEN m.title_excerpt
        ELSE m.matched_excerpt END` : 'NULL'} AS excerpt,
      ${fts ? "CASE WHEN instr(m.body_excerpt, char(57344)) > 0 THEN 'passage' ELSE 'metadata' END" : 'NULL'} AS excerpt_context,
      NULL AS hit_url
    FROM passages p JOIN documents d ON d.id=p.document_id
      JOIN editions e ON e.id=p.edition_id JOIN works w ON w.id=e.work_id
      ${fts ? 'JOIN hits m ON m.id=p.id' : ''}`;
  }
  // Publication order is independent of a publisher's claimed Git dates.
  base += ` WHERE (? = 'all' OR (? = 'latest' AND e.revision=w.latest_revision) OR e.revision=?)`;
  bind.push(String(edition), String(edition), String(edition));
  if (selected === 'editions') {
    base += ` AND (? = '' OR EXISTS
      (SELECT 1 FROM documents selected WHERE selected.edition_id=e.id AND selected.path=?))`;
  } else {
    base += ` AND (? = '' OR d.path=?)`;
  }
  bind.push(String(document), String(document));
  if (selected === 'passages') {
    // One edition-wide directory; this opt-in scope excludes all supporting
    // documents as a group, not just a single selected path.
    base += ` AND (? <> 'manuscript' OR d.path = 'content/manuscript.md')`;
    bind.push(supporting === 'manuscript' ? 'manuscript' : 'all');
  }
  const total = db.selectObjects(`SELECT COUNT(*) AS total FROM (${base})`, bind)[0].total;
  const requested = Number.isSafeInteger(page) && page > 0 ? page : 1;
  const number = Math.min(requested, Math.max(1, Math.ceil(total / PAGE_SIZE)));
  const orders = selected === 'passages' ? PASSAGE_ORDER : WORK_ORDER;
  const order = Object.hasOwn(orders, sort) ? orders[sort] : orders.publication;
  const rows = db.selectObjects(`SELECT * FROM (${base}) ORDER BY ${order} LIMIT ? OFFSET ?`,
    [...bind, PAGE_SIZE, (number - 1) * PAGE_SIZE]);
  return {kind: selected, rows, total, page: number, pageSize: PAGE_SIZE};
}

self.onmessage = async event => {
  const {action, id = 0} = event.data || {};
  try {
    if (action === 'init') {
      if (db) throw new Error('Search is already initialized');
      scope = event.data.scope === 'work' ? 'work' : 'collection';
      const database = new URL(event.data.database || './index.sqlite', import.meta.url);
      if (database.origin !== new URL(import.meta.url).origin || database.username || database.password ||
          !['http:', 'https:'].includes(database.protocol)) {
        throw new Error('Search database must be served from this website');
      }
      // This database lives only in worker memory; never initialize persistent
      // browser storage or the optional OPFS proxy, even on isolated hosts.
      globalThis.sqlite3ApiConfig = {disable: {vfs: {
        opfs: true, 'opfs-vfs': true, 'opfs-wl': true, 'opfs-sahpool': true, kvvfs: true
      }}};
      const sqlite3 = await sqlite3InitModule({
        locateFile: name => new URL(name, import.meta.url).href
      });
      db = (await loadDatabase(sqlite3, {database: database.href,
        databaseSize: event.data.databaseSize, transferSize: event.data.transferSize,
        encoding: event.data.encoding, retry: event.data.retry === true})).db;
      if (db.selectValue("SELECT value FROM metadata WHERE key='schema'") !== 'guavajam.search.v1' ||
          db.selectValue("SELECT value FROM metadata WHERE key='scope'") !== scope) {
        throw new Error('Unsupported search database schema');
      }
      if (scope === 'work') {
        if (db.selectValue('SELECT COUNT(*) FROM works') !== 1) throw new Error('Invalid work search database');
        const editions = db.selectObjects(`SELECT revision AS key, 'v' || (position + 1) AS name, published, 1 AS total
          FROM editions ORDER BY position DESC`);
        const documents = db.selectObjects(`WITH ranked AS (
          SELECT d.path, d.title, ROW_NUMBER() OVER (PARTITION BY d.path ORDER BY e.position DESC) AS rank,
            COUNT(*) OVER (PARTITION BY d.path) AS total
          FROM documents d JOIN editions e ON e.id=d.edition_id
        ) SELECT path AS key, title AS name, total FROM ranked WHERE rank=1 ORDER BY path`);
        self.postMessage({id, ok: true, ready: true, editions, documents});
        return;
      }
      const keywords = db.selectObjects(`SELECT name AS key, name, COUNT(*) AS total
        FROM keywords GROUP BY name ORDER BY name COLLATE NOCASE, name`);
      const rawSubjects = db.selectObjects(`SELECT key, label AS name, COUNT(*) AS total
        FROM subjects GROUP BY key, label ORDER BY label COLLATE NOCASE, key`);
      const names = db.selectObjects(`SELECT DISTINCT es.key, es.subject AS name
        FROM edition_subjects es JOIN editions e ON e.id=es.edition_id
        JOIN works w ON w.id=e.work_id AND w.latest_revision=e.revision`);
      const collisions = new Map();
      for (const item of names) collisions.set(item.name, (collisions.get(item.name) || 0) + 1);
      subjectNames = new Map(names.map(item => [item.key,
        collisions.get(item.name) === 1 ? item.name : null]));
      const subjects = rawSubjects.map(item => ({...item, name: subjectNames.get(item.key) || item.name}));
      self.postMessage({id, ok: true, ready: true, keywords, subjects});
    } else if (action === 'search' && db) {
      self.postMessage({id, ok: true, ...(scope === 'work' ? searchWork(event.data) : search(event.data))});
    } else {
      throw new Error('Search database is not initialized');
    }
  } catch (error) {
    if (action === 'init' && db) { db.close(); db = undefined; }
    self.postMessage({id, ok: false, error: String(error.message || error)});
  }
};
