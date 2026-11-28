import assert from 'node:assert/strict';
import {test} from 'node:test';
import {gzipSync} from 'node:zlib';
import {randomBytes, createHash} from 'node:crypto';
import {validateManifest, inflateChunk, ChunkStore, ByteCache } from '../src/a_guavajam_static_website_generator/assets/search-chunks.js';
import {SEARCH_BUDGETS} from '../src/a_guavajam_static_website_generator/assets/search-limits.js';
const digest = value => createHash('sha256').update(value).digest('hex');
function chunk(data, offset = 0) {
  const encoded = new Uint8Array(gzipSync(data, {mtime: 0}));
  const value = {offset, size: data.length, transfer_size: encoded.length,
    sha256: digest(data), transfer_sha256: digest(encoded)};
  value.url = value.transfer_sha256 + '.sqlite-chunk.gz';
  return {data, encoded, value};
}
test('bounded streaming gzip matches native output, including incompressible and extreme expansion', () => {
  for (const data of [randomBytes(1024 * 1024), Buffer.alloc(1024 * 1024), Buffer.from('café \u{1f984} '.repeat(80000))]) {
    const c = chunk(data);
    assert.deepEqual(Buffer.from(inflateChunk(c.encoded, c.value)), data);
    assert.deepEqual(Buffer.from(inflateChunk(new Uint8Array(data), c.value)), data);
    assert.throws(() => inflateChunk(c.encoded, {...c.value, size: data.length - 1}), /budget|integrity/);
    assert.throws(() => inflateChunk(c.encoded, {...c.value, sha256: '0'.repeat(64)}), /integrity/);
    const corrupt = new Uint8Array(c.encoded); corrupt[4] ^= 1;
    assert.throws(() => inflateChunk(corrupt, c.value), /Encoded/);
    assert.throws(() => inflateChunk(c.encoded.subarray(0, -1), c.value), /Encoded/);
  }
});
test('manifest binds generation, source, complete ordered chunks, size, page alignment and hashes', () => {
  const c = chunk(Buffer.alloc(SEARCH_BUDGETS.chunk));
  const m = {schema: 'guavajam.search-chunks.v1', generation: 'a'.repeat(64), sha256: c.value.sha256,
    scope: 'history', source: 'b'.repeat(40), size: c.value.size, page_size: 4096,
    chunk_size: SEARCH_BUDGETS.chunk, chunks: [c.value]};
  const d = {expectedGeneration: m.generation, scope: m.scope, revision: m.source, sha256: m.sha256, databaseSize: m.size};
  assert.equal(validateManifest(m,d), m);
  for (const change of [{generation:'0'.repeat(64)}, {scope:'passages'}, {source:'wrong'}, {size:1},
    {page_size:3}, {chunks:[]}, {chunks:[{...c.value, offset:1}]},
    {chunks:[{...c.value, url:'https://example.org/secret'}]}])
    assert.throws(() => validateManifest({...m,...change},d), /Invalid/);
});
test('byte LRUs evict within independent limits and never retain corrupt responses', () => {
  const cache = new ByteCache(5);
  cache.set(1,new Uint8Array(3)); cache.set(2,new Uint8Array(3));
  assert.equal(cache.get(1), undefined); assert.equal(cache.bytes,3); assert.equal(cache.evictions,1);
  const c = chunk(Buffer.alloc(SEARCH_BUDGETS.chunk));
  let calls=0;
  const store = new ChunkStore({size:c.value.size,chunks:[c.value]},'https://example.org/a.json',
    {request:() => {calls++; return calls === 1 ? c.encoded.subarray(0,-1) : c.encoded;}});
  assert.throws(() => store.chunk(0),/integrity/);
  assert.equal(store.decoded.bytes,0); assert.equal(store.compressed.bytes,0);
  assert.equal(store.chunk(0).length,c.value.size); assert.equal(calls,2);
  store.chunk(0); assert.equal(calls,2);
  const target = new Uint8Array(20).fill(1);
  assert.equal(store.read(target,c.value.size-10),10);
  assert.deepEqual(target,new Uint8Array(20));
  assert.equal(store.stats.networkPeak,1);
  store.close(); assert.equal(store.decoded.bytes,0);
});
