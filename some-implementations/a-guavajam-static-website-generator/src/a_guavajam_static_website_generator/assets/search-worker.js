/* Queries a disposable, read-only SQLite derivative. Signed sources remain authoritative. */
import sqlite3InitModule from './sqlite3.mjs';
import {loadDatabase} from './search-download.js';
import {SEARCH_BUDGETS} from './search-limits.js';

let db, displayDB, sqlite3, scope, generation, chunkTransport, deadline;
let activeRequest = 0, queryPhase = 'initializing';
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
  relevance: 'score ASC, position DESC, document_order, node_position, id',
  publication: 'document_order ASC, node_position ASC, id',
  oldest: 'document_order DESC, node_position DESC, id',
  title: 'title COLLATE NOCASE, document_order, node_position, id'
});

function literalQuery(value, operator = 'AND') {
  // Plain words with prefix matching, never user-supplied SQL or FTS operators.
  const words = String(value).slice(0, 256).match(/[\p{L}\p{N}]+/gu) || [];
  return words.slice(0, 12).map(word => '"' + word + '"*').join(' ' + operator + ' ');
}

// Search membership, ranking and explanatory snippets share the same FTS
// record. A displayed current title is never evidence for a historical hit.
const MATCH_FIELDS = [['body', 4], ['authors', 1], ['title', 0], ['abstract', 2], ['labels', 3], ['git', 5]];
function matchExcerpts(table, fields = MATCH_FIELDS) {
  return fields.map(([name, column]) =>
    `snippet(${table}, ${column}, char(57344), char(57345), '…', 24) AS ${name}_excerpt`).join(', ');
}
function matchEvidence(alias, content = 'document', fields = MATCH_FIELDS) {
  const choose = value => 'CASE ' + fields.map(([name]) =>
    `WHEN instr(${alias}.${name}_excerpt, char(57344)) > 0 THEN ${value(name)}`).join(' ') + ' ELSE NULL END';
  return `${choose(name => `${alias}.${name}_excerpt`)} AS excerpt,
    ${choose(name => `'${name === 'body' ? content : 'metadata'}'`)} AS excerpt_context,
    ${choose(name => `'${name}'`)} AS excerpt_field`;
}

function displayHighlights(result, term) {
  const query = literalQuery(term, 'OR');
  if (!query || !result.rows.length) return {...result, highlights: []};
  if (result.rows.some(row => !row.excerpt?.includes('\uE000'))) {
    throw new Error('A text result must include highlighted match evidence');
  }
  // At most one result page's visible fields is tokenized in a separate
  // worker-local memory database; the downloaded index remains read-only.
  // It uses the same unicode61 rules as matching and source excerpts.
  const text = new Set();
  const add = value => { if (typeof value === 'string' && value) text.add(value); };
  for (const row of result.rows) {
    for (const field of ['title', 'authors', 'abstract', 'published', 'latest_published', 'document_path']) add(row[field]);
    add(row.revision?.slice(0, 12));
    if (row.message) {
      const [subject, ...body] = row.message.split('\n'); add(subject); add(body.join('\n'));
    }
    for (const tag of JSON.parse(row.keywords_json || '[]')) add(tag);
    for (const subject of JSON.parse(row.subjects_json || '[]')) add(subject.label);
    if (row.citation_json) {
      const citation = JSON.parse(row.citation_json);
      for (const [, value] of [...citation.description, ...citation.source]) add(value);
    }
  }
  if (!displayDB) {
    displayDB = new sqlite3.oo1.DB();
    displayDB.exec("CREATE VIRTUAL TABLE display_fts USING fts5(value, tokenize='unicode61 remove_diacritics 2')");
  }
  displayDB.transaction(() => {
    displayDB.exec('DELETE FROM display_fts');
    for (const value of text) displayDB.exec({sql: 'INSERT INTO display_fts(value) VALUES (?)', bind: [value]});
  });
  const highlights = displayDB.selectObjects(`SELECT value, highlight(display_fts, 0, char(57344), char(57345)) AS marked
    FROM display_fts WHERE display_fts MATCH ?`, [query]);
  return {...result, highlights: highlights.map(row => [row.value, row.marked])};
}


const CORPUS = Object.freeze({
  collection: {table: 'work_fts', fields: [['title', 0], ['authors', 1], ['abstract', 2], ['labels', 3]], weights: '4,2,1,2'},
  versions: {table: 'version_fts', fields: [['name', 0], ['title', 1], ['message', 2]], weights: '4,2,1'},
  documents: {table: 'document_fts', fields: [['title', 0], ['abstract', 1]], weights: '4,1'},
  passages: {table: 'passage_fts', fields: MATCH_FIELDS, weights: '4,2,1,2,1,0.5'},
  history: {table: 'passage_fts', fields: MATCH_FIELDS, weights: '4,2,1,2,1,0.5'}
});

function memberships(filters, owner, bind) {
  const clauses = [];
  const keys = new Map();
  if (!Array.isArray(filters) || filters.length > 64) throw new Error('Invalid metadata filters');
  for (const item of filters) {
    if (typeof item?.key !== 'string' || !Array.isArray(item.values) || item.values.length > 64 ||
        item.values.some(value => typeof value !== 'string')) throw new Error('Invalid metadata filter values');
    const values = keys.get(item.key) || new Set();
    for (const value of item.values) values.add(value);
    keys.set(item.key, values);
  }
  for (const [key, values] of keys) {
    if (!values.size) continue;
    clauses.push('EXISTS (SELECT 1 FROM document_facets facet WHERE facet.document_id=' + owner +
      ' AND facet.key=? AND facet.value IN (' + [...values].map(() => '?').join(',') + '))');
    bind.push(key, ...values);
  }
  return clauses;
}

function plan(data, fts, projection = 'winners') {
  const corpus = CORPUS[scope], bind = [], where = [];
  let from, fields, identifier, order, resultKind, winners;
  if (scope === 'collection') {
    identifier = 'w.id';
    winners = 'w.id AS id,w.title AS title,w.latest_published,w.edition_count';
    from = 'works w JOIN repositories r ON r.id=w.repository_id';
    fields = "w.id AS id,w.title AS title,w.authors,w.abstract,w.latest_published,w.latest_revision,w.edition_count,r.path,r.url AS repository," +
      "CASE WHEN w.latest_revision='' THEN NULL ELSE w.edition_count-1 END AS match_position," +
      "'' AS match_document," +
      "(SELECT json_group_array(name) FROM (SELECT name FROM keywords WHERE work_id=w.id ORDER BY name COLLATE NOCASE,name)) AS keywords_json," +
      "(SELECT json_group_array(json_object('key',key,'label',label)) FROM " +
      "(SELECT key,label FROM subjects WHERE work_id=w.id ORDER BY label COLLATE NOCASE,key)) AS subjects_json";
    for (const [value, sql] of [
      [data.keyword, 'EXISTS (SELECT 1 FROM keywords k WHERE k.work_id=w.id AND k.name=?)'],
      [data.subject, 'EXISTS (SELECT 1 FROM subjects s WHERE s.work_id=w.id AND s.key=?)'],
      [data.from, 'w.latest_published>=?'], [data.to, 'w.latest_published<=?']]) {
      if (value) { where.push(sql); bind.push(String(value)); }
    }
    order = Object.hasOwn(ORDER, data.sort) ? ORDER[data.sort] : ORDER.recent;
  } else if (scope === 'versions') {
    identifier = 'e.id'; resultKind = 'editions';
    winners = "e.id AS id,e.name AS title,e.position AS position,e.published,'' AS document_path,0 AS node_position";
    from = 'editions e';
    fields = "e.id AS id,e.name AS title,'editions/' || e.revision || '/read/index.html' AS url," +
      "e.revision,e.published,e.position AS position,e.message,'' AS document_path,0 AS node_position," +
      "e.position AS match_position,'' AS match_document";
    order = Object.hasOwn(WORK_ORDER, data.sort) ? WORK_ORDER[data.sort] : WORK_ORDER.publication;
  } else {
    from = 'documents d JOIN editions e ON e.id=d.edition_id';
    if (scope === 'documents') {
      identifier = 'd.id'; resultKind = 'documents';
      winners = 'd.id AS id,d.title AS title,e.position AS position,e.published,d.path AS document_path,0 AS node_position';
      fields = "d.id AS id,d.title AS title,d.abstract,d.url,e.revision,e.published,e.position AS position," +
        "d.title AS document_title,d.path AS document_path,'' AS anchor,'document' AS scope," +
        "0 AS node_position,e.position AS match_position,d.title AS match_document";
      order = Object.hasOwn(WORK_ORDER, data.sort) ? WORK_ORDER[data.sort] : WORK_ORDER.publication;
    } else {
      // Counts and ranking need occurrence/shape facts, not citation views.
      // Reconstruct display/citation fields only after choosing page IDs.
      from += projection === 'display' ? ' JOIN passages p ON p.document_id=d.id' :
        ' JOIN passage_occurrences p ON p.document_id=d.id';
      if (projection === 'winners') from += ' JOIN passage_shapes shape ON shape.id=p.shape_id';
      identifier = 'p.id'; resultKind = 'passages';
      winners = 'p.id AS id,shape.title AS title,e.position AS position,e.published,d.path AS document_path,d.id AS document_order,shape.position AS node_position';
      fields = "p.id AS id,p.title AS title,p.url,p.citation_url,p.citation_json,e.revision,e.published,e.position AS position," +
        "d.title AS document_title,d.path AS document_path,d.id AS document_order,p.anchor,p.scope," +
        "p.position AS node_position,e.position AS match_position,d.title AS match_document";
      const orders = scope === 'history' ? WORK_ORDER : PASSAGE_ORDER;
      order = Object.hasOwn(orders, data.sort) ? orders[data.sort] : orders.publication;
      if (data.supporting === 'manuscript') where.push("d.path='content/manuscript.md'");
    }
    if (data.document) { where.push('d.path=?'); bind.push(String(data.document)); }
    where.push(...memberships(data.metadata || [], 'd.id', bind));
  }
  if (fts) {
    from += ' JOIN ' + corpus.table + ' ON ' + corpus.table + '.rowid=' + identifier;
    where.push(corpus.table + ' MATCH ?'); bind.push(fts);
  }
  return {from, fields, identifier, winners, where: where.length ? ' WHERE ' + where.join(' AND ') : '', bind, order, resultKind};
}

function boundedExcerpt(text) {
  const match = text.indexOf('\uE000');
  if (match < 0) throw new Error('Missing genuine highlighted source evidence');
  let start = Math.max(0, match - 160), end = Math.min(text.length, start + 2048);
  if (start && /[\uDC00-\uDFFF]/.test(text[start])) start--;
  if (end < text.length && /[\uDC00-\uDFFF]/.test(text[end])) end--;
  let result = text.slice(start, end);
  if (result.lastIndexOf('\uE000') > result.lastIndexOf('\uE001')) result += '\uE001';
  return (start ? '…' : '') + result + (end < text.length ? '…' : '');
}

function evidence(rows, fts, term) {
  if (!fts) return;
  const {table, fields} = CORPUS[scope];
  const passage = scope === 'passages' || scope === 'history';
  for (const row of rows) {
    let found, field;
    if (passage) {
      const words = literalQuery(term, 'OR');
      const bodyMatch = db.selectValue('SELECT 1 FROM passage_fts WHERE passage_fts MATCH ? AND rowid=?',
        ['body : (' + words + ')', row.id]);
      if (bodyMatch) {
        const window = db.selectValue(
          "SELECT snippet(window_fts,0,char(57344),char(57345),'…',24) FROM passage_windows w " +
          // CROSS JOIN keeps the selected occurrence's indexed source windows
          // outside the FTS lookup. A common word must not scan the complete
          // global window posting list again for every displayed winner.
          'CROSS JOIN window_fts ON window_fts.rowid=w.window_id WHERE w.passage_id=? AND window_fts MATCH ? ' +
          'ORDER BY w.block_position,w.window_position LIMIT 1', [row.id, words]);
        // Oversized whitespace-free units stay out of the auxiliary index.
        // Native exact-field evidence handles that bounded individual input.
        const excerpt = window || db.selectValue(
          "SELECT snippet(passage_fts,4,char(57344),char(57345),'…',24) " +
          'FROM passage_fts WHERE passage_fts MATCH ? AND rowid=?', [fts, row.id]);
        found = {body_excerpt: excerpt}; field = 'body';
      }
    }
    if (!field) {
      const eligible = passage ? fields.filter(([name]) => name !== 'body') : fields;
      found = db.selectObjects('SELECT ' + matchExcerpts(table, eligible) +
        ' FROM ' + table + ' WHERE ' + table + ' MATCH ? AND rowid=?', [fts, row.id])[0];
      field = eligible.find(([name]) => found?.[name + '_excerpt']?.includes('\uE000'))?.[0];
    }
    if (!field) throw new Error('A text result must include highlighted match evidence');
    row.excerpt = boundedExcerpt(found[field + '_excerpt']); row.excerpt_field = field;
    row.excerpt_context = field === 'body' ? 'passage' : 'metadata';
    row.hit_url = scope === 'collection' ?
      row.path + (row.latest_revision ? '/editions/' + row.latest_revision + '/read/index.html' : '/index.html') :
      scope === 'versions' ? row.url : null;
  }
}

function search(data) {
  const fts = literalQuery(data.term || ''), {table, weights} = CORPUS[scope];
  queryPhase = 'count';
  const query = plan(data, fts, 'count');
  // Count membership without snippets or citations; materialize evidence only
  // for winners on the requested page, preserving global corpus statistics.
  const total = db.selectValue('SELECT COUNT(*) FROM ' + query.from + query.where, query.bind.length ? query.bind : undefined);
  const requested = Number.isSafeInteger(data.page) && data.page > 0 ? data.page : 1;
  const number = Math.min(requested, Math.max(1, Math.ceil(total / PAGE_SIZE)));
  const score = fts ? 'bm25(' + table + ',' + weights + ')' : '0';
  queryPhase = 'rank';
  const ranked = plan(data, fts);
  const winners = db.selectObjects('SELECT ' + ranked.winners + ',' + score + ' AS score FROM ' +
    ranked.from + ranked.where + ' ORDER BY ' + ranked.order + ' LIMIT ? OFFSET ?',
    [...ranked.bind, PAGE_SIZE, (number - 1) * PAGE_SIZE]);
  // Display fields and citations are retrieved only for the selected page.
  queryPhase = 'display';
  const detail = plan(data, '', 'display');
  const rows = winners.map(winner => ({...db.selectObjects('SELECT ' + detail.fields + ' FROM ' +
    detail.from + ' WHERE ' + detail.identifier + '=?', [winner.id])[0], score: winner.score}));
  for (const row of rows) {
    if (row.subjects_json) row.subjects_json = JSON.stringify(JSON.parse(row.subjects_json).map(item =>
      ({...item, label: db.selectValue('SELECT CASE WHEN count(DISTINCT key)=1 THEN min(name) ELSE ? END ' +
        'FROM subjects WHERE name=(SELECT name FROM subjects WHERE key=? LIMIT 1)', [item.label, item.key])})));
  }
  queryPhase = 'evidence';
  evidence(rows, fts, data.term || '');
  queryPhase = 'highlights';
  return displayHighlights({kind: query.resultKind, rows, total, page: number, pageSize: PAGE_SIZE}, data.term || '');
}

self.onmessage = async event => {
  const {action, id = 0} = event.data || {};
  try {
    if (action === 'init') {
      if (db) throw new Error('Search is already initialized');
      scope = event.data.scope;
      if (![event.data.sha256, event.data.transferSha256, event.data.expectedGeneration].every(
        value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)))
        throw new Error('Search requires an exact artifact digest and generation');
      if (!Object.hasOwn(CORPUS, scope)) throw new Error('Unsupported search scope');
      const database = new URL(event.data.database, import.meta.url);
      if (database.origin !== new URL(import.meta.url).origin || database.username || database.password ||
          !['http:', 'https:'].includes(database.protocol)) throw new Error('Search database must be served from this website');
      globalThis.sqlite3ApiConfig = {disable: {vfs: {
        opfs: true, 'opfs-vfs': true, 'opfs-wl': true, 'opfs-sahpool': true, kvvfs: true
      }}};
      const chunked = event.data.encoding === 'sqlite-chunks';
      const wasmMemory = chunked ? new WebAssembly.Memory({initial: 128, maximum: SEARCH_BUDGETS.wasmHeap / 65536}) : undefined;
      sqlite3 = await sqlite3InitModule({locateFile: name => new URL(name, import.meta.url).href,
        ...(wasmMemory ? {wasmMemory} : {})});
      if (chunked) {
        const {loadChunkDatabase} = await import('./search-chunks.js');
        chunkTransport = await loadChunkDatabase(sqlite3, {...event.data, database: database.href}, metrics => {
          self.postMessage({id: activeRequest, ok: true, progress: true, scope, generation,
            phase: queryPhase, transport: metrics});
        });
        db = chunkTransport.db;
      } else db = (await loadDatabase(sqlite3, {database: database.href,
        databaseSize: event.data.databaseSize, transferSize: event.data.transferSize,
        encoding: event.data.encoding, sha256: event.data.sha256, transferSha256: event.data.transferSha256, retry: event.data.retry === true})).db;
      if (db.selectValue("SELECT value FROM metadata WHERE key='schema'") !== 'guavajam.search.v1' ||
          db.selectValue("SELECT value FROM metadata WHERE key='scope'") !== scope ||
          event.data.revision && db.selectValue("SELECT value FROM metadata WHERE key='revision'") !== event.data.revision) {
        throw new Error('Search artifact does not match this scope or edition');
      }
      const heapBudget = chunked ? SEARCH_BUDGETS.sqliteHeap : event.data.databaseSize + SEARCH_BUDGETS.sqliteHeap;
      db.exec('PRAGMA hard_heap_limit=' + heapBudget + '; PRAGMA temp_store=MEMORY; PRAGMA cache_size=-8192;');
      // One worker performs one query at a time. The handler also interrupts
      // CPU-only operations that do not visit the VFS before the deadline.
      sqlite3.capi.sqlite3_progress_handler(db.pointer, 10000, () => performance.now() > deadline ? 1 : 0, 0);
      generation = db.selectValue("SELECT value FROM metadata WHERE key='generation'");
      if (generation !== event.data.expectedGeneration) throw new Error('Search artifact generation differs');
      self.postMessage({id, ok: true, ready: true, generation, scope});
    } else if (action === 'search' && db) {
      if (event.data.scope !== scope || event.data.generation !== generation) throw new Error('Search generation changed');
      activeRequest = id;
      deadline = performance.now() + SEARCH_BUDGETS.queryMilliseconds;
      chunkTransport?.store.begin();
      const result = search(event.data);
      self.postMessage({id, ok: true, generation, scope, ...result,
        ...(chunkTransport ? {transport: chunkTransport.store.metrics()} : {})});
    } else {
      throw new Error('Search database is not initialized');
    }
  } catch (error) {
    if (action === 'init') {
      if (db) { db.close(); db = undefined; }
      chunkTransport?.dispose(); chunkTransport = undefined;
    }
    self.postMessage({id, ok: false, scope, generation, error: String(error.message || error)});
  }
};
