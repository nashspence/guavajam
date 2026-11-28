// Transfer failures must not allocate unbounded memory or leak SQLite-owned buffers.
import assert from 'node:assert/strict';
import {after, before, test} from 'node:test';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {loadDatabase} from '../src/a_guavajam_static_website_generator/assets/search-download.js';

const raw = new Uint8Array(1024);
raw.set(new TextEncoder().encode('SQLite format 3\0'));
const header = new DataView(raw.buffer);
header.setUint16(16, 512);
header.setUint32(28, 2);
for (let i = 100; i < raw.length; i++) raw[i] = (i * 31) % 253;
const compressed = gzipSync(raw);
const digest = value => createHash('sha256').update(value).digest('hex');
const badHeader = raw.slice();
badHeader[0] = 0;
const badGzip = gzipSync(badHeader);
const filename = 'versions.' + digest(compressed) + '.sqlite.gz';
const requests = [];
let server, base;

before(async () => {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    requests.push(url.pathname);
    if (url.pathname === '/redirect') {
      res.writeHead(302, {Location: '/' + filename});
      res.end();
      return;
    }
    let payload = url.pathname === '/raw' ? raw :
      url.pathname === '/bad-header' ? badGzip :
      url.pathname === '/truncated' ? compressed.subarray(0, -7) : compressed;
    if (url.pathname === '/corrupt-digest') { payload = payload.slice(); payload[4] ^= 1; }
    const headers = {'Content-Type': 'application/octet-stream'};
    if (url.pathname !== '/chunked') headers['Content-Length'] = payload.length;
    if (url.pathname === '/http-gzip') headers['Content-Encoding'] = 'gzip';
    res.writeHead(200, headers);
    if (url.pathname === '/chunked') {
      let position = 0;
      const write = () => {
        if (position >= payload.length) { res.end(); return; }
        res.write(payload.subarray(position, position + 17));
        position += 17;
        setImmediate(write);
      };
      write();
    } else res.end(payload);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}/`;
});
after(() => new Promise(resolve => server.close(resolve)));

function sqlite({deserializeError = false, pragmaError = false} = {}) {
  const heap = new Uint8Array(1024 * 1024);
  const allocations = new Map();
  const stats = {allocated: 0, freed: 0, closed: 0};
  let next = 256;
  const dealloc = pointer => {
    assert(allocations.has(pointer), 'never free the same allocation twice');
    allocations.delete(pointer);
    stats.freed++;
  };
  const dbs = new Map();
  const result = {
    allocations, stats,
    wasm: {
      alloc(size) {
        const pointer = next;
        next += size + 16;
        assert(next < heap.length);
        allocations.set(pointer, size);
        stats.allocated++;
        return pointer;
      },
      dealloc,
      heap8u: () => heap,
    },
    capi: {
      SQLITE_DESERIALIZE_FREEONCLOSE: 1,
      SQLITE_DESERIALIZE_READONLY: 4,
      sqlite3_deserialize(pointer, schema, data, size, capacity, flags) {
        assert.equal(schema, 'main');
        assert.equal(flags, 5);
        assert.equal(capacity, size + 20);
        assert.deepEqual(heap.subarray(data + size, data + capacity), new Uint8Array(20));
        if (deserializeError) { dealloc(data); return 1; }
        const db = dbs.get(pointer);
        db.data = data;
        db.bytes = heap.subarray(data, data + size);
        return 0;
      }
    },
    oo1: {DB: class {
      constructor() { this.pointer = dbs.size + 1; dbs.set(this.pointer, this); }
      checkRc(code) { if (code) throw new Error('SQLite deserialize failure'); }
      exec(sql) {
        assert.equal(sql, 'PRAGMA query_only=ON');
        if (pragmaError) throw new Error('SQLite pragma failure');
        this.readOnly = true;
      }
      close() {
        stats.closed++;
        if (this.data !== undefined) { dealloc(this.data); this.data = undefined; }
      }
    }}
  };
  return result;
}

function descriptor(path = filename) {
  return {database: base + path, databaseSize: raw.length,
    transferSize: compressed.length, encoding: 'gzip', sha256: digest(raw),
    transferSha256: digest(compressed), baseURL: base};
}

test('gzip streams exactly into one allocation, with a zeroed defensive tail', async () => {
  const api = sqlite();
  const {db, metrics} = await loadDatabase(api, descriptor('chunked'));
  assert.deepEqual(db.bytes, raw);
  assert(db.readOnly);
  assert.equal(metrics.responseBytes, compressed.length);
  assert.equal(metrics.decodedBytes, raw.length);
  assert.equal(metrics.allocatedBytes, raw.length + 20);
  assert.equal(metrics.jsFullBufferBytes, 0);
  assert.equal(api.stats.allocated, 1);
  assert.equal(api.allocations.size, 1);
  db.close();
  assert.equal(api.allocations.size, 0);
});

test('HTTP Content-Encoding does not cause a second gzip decode', async () => {
  const api = sqlite();
  const {db, metrics} = await loadDatabase(api, descriptor('http-gzip'));
  assert.deepEqual(db.bytes, raw);
  assert.equal(metrics.responseBytes, raw.length);
  assert.equal(metrics.transferredBytes, null, 'Fetch exposes decoded bytes, not the wire byte count');
  db.close();
});

test('the plain SQLite comparison infers a bounded size from their SQLite header', async () => {
  const api = sqlite();
  const {db, metrics} = await loadDatabase(api, {database: base + 'raw', baseURL: base});
  assert.deepEqual(db.bytes, raw);
  assert.equal(metrics.cache, 'no-cache');
  db.close();
});

test('cache reuse is limited to immutable filenames and retries force a fresh fetch', async () => {
  for (const [path, retry, expected] of [[filename, false, 'force-cache'],
    [filename, true, 'reload'], [filename + '?change=1', false, 'no-cache']]) {
    const {db, metrics} = await loadDatabase(sqlite(), {...descriptor(path), retry});
    assert.equal(metrics.cache, expected);
    db.close();
  }
});

test('invalid declared sizes and foreign origins fail before downloading or allocating', async () => {
  const before = requests.length;
  for (const change of [{databaseSize: 0}, {databaseSize: 256 * 1024 * 1024 + 1},
    {databaseSize: 3.5}, {transferSize: 0}, {transferSize: undefined},
    {encoding: 'br'}, {sha256: 'invalid'}, {transferSha256: 'invalid'}, {database: 'https://example.org/' + filename}]) {
    const api = sqlite();
    await assert.rejects(loadDatabase(api, {...descriptor(), ...change}));
    assert.equal(api.stats.allocated, 0);
  }
  assert.equal(requests.length, before);
});

test('compressed and expanded size mismatches are rejected without leaked allocations', async () => {
  for (const change of [{transferSize: compressed.length - 1}, {transferSize: compressed.length + 1},
    {databaseSize: 512}, {databaseSize: 1536}]) {
    const api = sqlite();
    await assert.rejects(loadDatabase(api, {...descriptor('chunked'), ...change}), /size|truncated/);
    assert.equal(api.allocations.size, 0);
    assert.equal(api.stats.allocated, api.stats.freed);
  }
});

test('truncated gzip and corrupt SQLite headers clean up partially filled buffers', async () => {
  for (const [path, transferSize] of [['truncated', compressed.length - 7], ['bad-header', badGzip.length]]) {
    const api = sqlite();
    await assert.rejects(loadDatabase(api, {...descriptor(path), transferSize}));
    assert.equal(api.allocations.size, 0);
    assert.equal(api.stats.allocated, api.stats.freed);
  }
});

test('SQLite owns the serialization buffer on both successful and failing deserialization', async () => {
  for (const failure of [{deserializeError: true}, {pragmaError: true}]) {
    const api = sqlite(failure);
    await assert.rejects(loadDatabase(api, descriptor()), /SQLite/);
    assert.equal(api.stats.allocated, 1);
    assert.equal(api.stats.freed, 1);
    assert.equal(api.stats.closed, 1);
  }
});

test('missing native gzip support fails cleanly', async () => {
  const decoder = globalThis.DecompressionStream;
  const api = sqlite();
  try {
    globalThis.DecompressionStream = undefined;
    await assert.rejects(loadDatabase(api, descriptor()));
  } finally { globalThis.DecompressionStream = decoder; }
  assert.equal(api.allocations.size, 0);
});

test('real redirects are rejected without fetching their destination', async () => {
  const before = requests.length;
  const api = sqlite();
  await assert.rejects(loadDatabase(api, descriptor('redirect')));
  assert.deepEqual(requests.slice(before), ['/redirect']);
  assert.equal(api.stats.allocated, 0);
});

test('encoded and decoded digest failures release every owned buffer', async () => {
  for (const [path, change] of [['corrupt-digest', {}], [filename, {sha256: 'f'.repeat(64)}]]) {
    const api = sqlite();
    await assert.rejects(loadDatabase(api, {...descriptor(path), ...change}), /SHA-256/);
    assert.equal(api.allocations.size, 0);
    assert.equal(api.stats.allocated, api.stats.freed);
  }
});
