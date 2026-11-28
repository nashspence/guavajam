/* Read-only HTTP VFS: physical chunks retain one logical SQLite/FTS corpus. */
import {Gunzip} from './fflate.mjs';
import {sha256} from './search-hash.js';

import {SEARCH_BUDGETS} from './search-limits.js';

const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function integer(value, maximum) { return Number.isSafeInteger(value) && value > 0 && value <= maximum; }

export function validateManifest(value, descriptor) {
  const b = SEARCH_BUDGETS, size = value?.size, page = value?.page_size;
  if (value?.schema !== 'guavajam.search-chunks.v1' || value.generation !== descriptor.expectedGeneration ||
      value.scope !== descriptor.scope || descriptor.revision && value.source !== descriptor.revision ||
      value.sha256 !== descriptor.sha256 || !hash(value.sha256) || !hash(value.generation) ||
      size !== descriptor.databaseSize || !integer(size, b.logical) ||
      !integer(page, 65536) || page < 512 || (page & (page - 1)) || size % page ||
      value.chunk_size !== b.chunk || !Array.isArray(value.chunks) ||
      value.chunks.length !== Math.ceil(size / b.chunk)) throw new Error('Invalid search chunk manifest');
  for (const [n, chunk] of value.chunks.entries()) {
    if (chunk.offset !== n * b.chunk || chunk.size !== Math.min(b.chunk, size - chunk.offset) ||
        !integer(chunk.transfer_size, b.chunk + 4096) || !hash(chunk.sha256) ||
        !hash(chunk.transfer_sha256) || chunk.url !== chunk.transfer_sha256 + '.sqlite-chunk.gz')
      throw new Error('Invalid ordered search chunk');
  }
  return value;
}

export function inflateChunk(bytes, chunk) {
  if (!(bytes instanceof Uint8Array)) throw new Error('Invalid search chunk bytes');
  if (bytes[0] !== 31 || bytes[1] !== 139) {
    // A static host may apply Content-Encoding itself. The decoded digest is
    // still mandatory; an encoded digest is checked whenever bytes expose it.
    if (bytes.length !== chunk.size || sha256(bytes) !== chunk.sha256)
      throw new Error('Decoded search chunk integrity failed');
    return bytes;
  }
  if (bytes.length !== chunk.transfer_size || sha256(bytes) !== chunk.transfer_sha256)
    throw new Error('Encoded search chunk integrity failed');
  const output = new Uint8Array(chunk.size);
  let copied = 0, ended = false;
  const stream = new Gunzip((part, final) => {
    if (copied + part.length > output.length) throw new Error('Search chunk exceeds decoded budget');
    output.set(part, copied); copied += part.length; ended ||= final;
  });
  // Feeding small inputs bounds inflate's transient output independently of
  // the untrusted gzip ISIZE trailer. Never allocate from that trailer.
  for (let offset = 0; offset < bytes.length; offset += 256)
    stream.push(bytes.subarray(offset, offset + 256), offset + 256 >= bytes.length);
  if (copied !== output.length || !ended || sha256(output) !== chunk.sha256)
    throw new Error('Decoded search chunk integrity failed');
  return output;
}

export class ByteCache {
  constructor(limit) { this.limit = limit; this.bytes = this.peak = this.evictions = 0; this.values = new Map(); }
  get(key) {
    const value = this.values.get(key);
    if (value) { this.values.delete(key); this.values.set(key, value); }
    return value;
  }
  set(key, value) {
    if (this.values.has(key)) { this.bytes -= this.values.get(key).length; this.values.delete(key); }
    while (this.bytes + value.length > this.limit && this.values.size) {
      const oldest = this.values.keys().next().value;
      this.bytes -= this.values.get(oldest).length; this.values.delete(oldest); this.evictions++;
    }
    if (value.length <= this.limit) { this.values.set(key, value); this.bytes += value.length; }
    this.peak = Math.max(this.peak, this.bytes);
    return value;
  }
  clear() { this.values.clear(); this.bytes = 0; }
}

export class ChunkStore {
  constructor(manifest, manifestURL, {retry = false, request, progress = () => {}} = {}) {
    this.manifest = manifest; this.base = manifestURL; this.retry = retry;
    this.decoded = new ByteCache(SEARCH_BUDGETS.decoded);
    this.compressed = new ByteCache(SEARCH_BUDGETS.compressed);
    this.request = request || this.http.bind(this); this.progress = progress;
    this.stats = {requests: 0, transferred: 0, reads: 0, networkActive: 0, networkPeak: 0};
    this.begin();
  }
  begin() {
    this.deadline = performance.now() + SEARCH_BUDGETS.queryMilliseconds;
    this.queryRequests = this.queryBytes = 0;
  }
  http(url, expected) {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, false); xhr.responseType = 'arraybuffer'; xhr.timeout = 15000;
    if (this.retry) xhr.setRequestHeader('Cache-Control', 'no-cache');
    xhr.send();
    if (xhr.status !== 200 || xhr.responseURL !== url || !xhr.response ||
        xhr.response.byteLength > Math.max(expected.transfer_size, expected.size))
      throw new Error('Search chunk request failed or exceeds its transfer budget');
    return new Uint8Array(xhr.response);
  }
  chunk(number) {
    if (performance.now() > this.deadline) throw new Error('Search exceeded its time budget');
    const hit = this.decoded.get(number);
    if (hit) return hit;
    const expected = this.manifest.chunks[number];
    if (!expected) throw new Error('Search read outside its logical database');
    let encoded = this.compressed.get(number);
    if (!encoded) {
      if (++this.queryRequests > SEARCH_BUDGETS.requests ||
          this.queryBytes + Math.max(expected.size, expected.transfer_size) > SEARCH_BUDGETS.transfer)
        throw new Error('Search exceeded its request budget');
      this.stats.networkActive++; this.stats.networkPeak = Math.max(this.stats.networkPeak, this.stats.networkActive);
      try {
        encoded = this.request(new URL(expected.url, this.base).href, expected);
        if (!(encoded instanceof Uint8Array) || encoded.length > Math.max(expected.size, expected.transfer_size))
          throw new Error('Search chunk exceeds transfer budget');
        this.queryBytes += encoded.length;
        this.stats.requests++; this.stats.transferred += encoded.length;
      } finally { this.stats.networkActive--; }
      // Verify before either cache. Failed data can never become a cache hit.
      const decoded = inflateChunk(encoded, expected);
      this.compressed.set(number, encoded); this.decoded.set(number, decoded);
      this.progress(this.stats); return decoded;
    }
    return this.decoded.set(number, inflateChunk(encoded, expected));
  }
  read(target, offset) {
    if (!Number.isSafeInteger(offset) || offset < 0 || target.length > SEARCH_BUDGETS.chunk)
      throw new Error('Invalid SQLite read bounds');
    this.stats.reads++;
    let copied = 0;
    while (copied < target.length && offset + copied < this.manifest.size) {
      const absolute = offset + copied, n = Math.floor(absolute / SEARCH_BUDGETS.chunk);
      const chunk = this.chunk(n), within = absolute % SEARCH_BUDGETS.chunk;
      const size = Math.min(target.length - copied, chunk.length - within);
      target.set(chunk.subarray(within, within + size), copied); copied += size;
    }
    if (copied < target.length) target.fill(0, copied);
    return copied;
  }
  close() { this.decoded.clear(); this.compressed.clear(); }
  metrics() { return {...this.stats, decodedPeak: this.decoded.peak, compressedPeak: this.compressed.peak,
    decodedEvictions: this.decoded.evictions, compressedEvictions: this.compressed.evictions}; }
}

async function manifestBytes(url, size, retry) {
  if (!integer(size, SEARCH_BUDGETS.manifest)) throw new Error('Invalid search manifest size');
  const response = await fetch(url, {mode: 'same-origin', redirect: 'error', cache: retry ? 'reload' : 'default'});
  if (!response.ok || !response.body) throw new Error('Search manifest request failed');
  const output = new Uint8Array(size), reader = response.body.getReader();
  let copied = 0;
  try {
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      if (copied + value.length > size) throw new Error('Search manifest exceeds budget');
      output.set(value, copied); copied += value.length;
    }
  } catch (error) { await reader.cancel(error).catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  if (copied !== size) throw new Error('Truncated search manifest');
  return output;
}

export async function loadChunkDatabase(sqlite3, descriptor, progress) {
  const url = new URL(descriptor.database, import.meta.url);
  if (url.origin !== new URL(import.meta.url).origin || url.username || url.password ||
      !['http:', 'https:'].includes(url.protocol) || !hash(descriptor.transferSha256))
    throw new Error('Invalid same-origin search manifest');
  const bytes = await manifestBytes(url.href, descriptor.transferSize, descriptor.retry);
  if (sha256(bytes) !== descriptor.transferSha256) throw new Error('Search manifest integrity failed');
  const manifest = validateManifest(JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)), descriptor);
  const store = new ChunkStore(manifest, url.href, {retry: descriptor.retry, progress});
  const {capi, wasm} = sqlite3, io = new capi.sqlite3_io_methods(), vfs = new capi.sqlite3_vfs();
  const defaults = new capi.sqlite3_vfs(capi.sqlite3_vfs_find(0));
  io.$iVersion = 1; vfs.$iVersion = 1;
  vfs.$szOsFile = capi.sqlite3_file.structInfo.sizeof; vfs.$mxPathname = 256;
  for (const name of ['xRandomness', 'xSleep', 'xCurrentTime', 'xGetLastError'])
    vfs['$' + name] = defaults['$' + name];
  let lastError;
  const guarded = fn => (...args) => {
    try { return fn(...args); } catch (error) { lastError = error; return capi.SQLITE_IOERR; }
  };
  sqlite3.vfs.installVfs({
    io: {struct: io, methods: {
      xClose: () => 0,
      xRead: guarded((file, destination, length, offset) =>
        store.read(wasm.heap8u().subarray(destination, destination + length), Number(offset)) === length ?
          0 : capi.SQLITE_IOERR_SHORT_READ),
      xFileSize: (file, out) => { wasm.poke64(out, BigInt(manifest.size)); return 0; },
      xLock: () => 0, xUnlock: () => 0,
      xCheckReservedLock: (file, out) => { wasm.poke32(out, 0); return 0; },
      xFileControl: () => capi.SQLITE_NOTFOUND, xSectorSize: () => manifest.page_size,
      xDeviceCharacteristics: () => capi.SQLITE_IOCAP_IMMUTABLE,
      xWrite: () => capi.SQLITE_READONLY, xTruncate: () => capi.SQLITE_READONLY, xSync: () => 0
    }},
    vfs: {struct: vfs, name: 'guavajam-static-chunks', methods: {
      xOpen: (pVfs, name, file, flags, out) => {
        if (!(flags & capi.SQLITE_OPEN_MAIN_DB) || flags & capi.SQLITE_OPEN_READWRITE) return capi.SQLITE_CANTOPEN;
        const wrapper = new capi.sqlite3_file(file); wrapper.$pMethods = io.pointer;
        if (out) wasm.poke32(out, capi.SQLITE_OPEN_READONLY);
        return 0;
      },
      xAccess: (pVfs, name, flags, out) => { wasm.poke32(out, 0); return 0; },
      xDelete: () => capi.SQLITE_READONLY,
      xFullPathname: (pVfs, name, length, out) => {
        const text = wasm.cstrToJs(name);
        if (text !== '/search.sqlite' || length <= text.length) return capi.SQLITE_CANTOPEN;
        wasm.heap8u().set(new TextEncoder().encode(text + '\0'), out); return 0;
      }
    }}
  });
  const dispose = () => { capi.sqlite3_vfs_unregister(vfs.pointer); vfs.dispose(); io.dispose(); store.close(); };
  try {
    const db = new sqlite3.oo1.DB({filename: '/search.sqlite', flags: 'r', vfs: 'guavajam-static-chunks'});
    db.exec('PRAGMA cache_size=-8192; PRAGMA mmap_size=0; PRAGMA temp_store=MEMORY; PRAGMA query_only=ON;');
    return {db, store, dispose, error: () => lastError};
  } catch (error) { dispose(); throw lastError || error; }
}
