/* Bounded static downloads: native decompression writes directly into WASM memory. */
import {Sha256} from './search-hash.js';

const MAX_DATABASE_BYTES = 256 * 1024 * 1024;
const SQLITE_HEADER = new TextEncoder().encode('SQLite format 3\0');

function length(value, name, optional = false) {
  if (value === undefined && optional) return undefined;
  if (!Number.isSafeInteger(value) || value <= 0 || value > MAX_DATABASE_BYTES) {
    throw new Error(`Invalid ${name} size`);
  }
  return value;
}

function sqliteSize(header) {
  if (header.length < 100 || !SQLITE_HEADER.every((value, index) => header[index] === value)) {
    throw new Error('The search artifact is not a SQLite database');
  }
  const view = new DataView(header.buffer, header.byteOffset, header.byteLength);
  const pageSize = view.getUint16(16) === 1 ? 65536 : view.getUint16(16);
  if (pageSize < 512 || pageSize > 65536 || (pageSize & (pageSize - 1))) {
    throw new Error('Invalid SQLite page size');
  }
  return length(pageSize * view.getUint32(28), 'database');
}

async function peek(stream) {
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (size < 100) {
      const {done, value} = await reader.read();
      if (done) break;
      chunks.push(value);
      size += value.byteLength;
    }
  } catch (error) {
    await reader.cancel(error).catch(() => {});
    reader.releaseLock();
    throw error;
  }
  const header = new Uint8Array(Math.min(size, 100));
  let offset = 0;
  for (const chunk of chunks) {
    const part = chunk.subarray(0, header.length - offset);
    header.set(part, offset);
    offset += part.byteLength;
    if (offset === header.length) break;
  }
  return {header, stream: new ReadableStream({
    async pull(controller) {
      try {
        if (chunks.length) {
          controller.enqueue(chunks.shift());
          return;
        }
        const {done, value} = await reader.read();
        if (done) { reader.releaseLock(); controller.close(); }
        else controller.enqueue(value);
      } catch (error) {
        reader.releaseLock();
        controller.error(error);
      }
    },
    async cancel(reason) {
      chunks.length = 0;
      await reader.cancel(reason).catch(() => {});
      reader.releaseLock();
    }
  })};
}

export async function loadDatabase(sqlite3, {
  database, databaseSize, transferSize, encoding, sha256, transferSha256, retry = false, baseURL = import.meta.url
}) {
  const started = performance.now();
  for (const digest of [sha256, transferSha256]) if (digest !== undefined && !/^[a-f0-9]{64}$/.test(digest))
    throw new Error('Invalid search digest');
  const decodedHash = sha256 ? new Sha256() : null, responseHash = transferSha256 ? new Sha256() : null;
  const url = new URL(database, baseURL);
  if (url.origin !== new URL(baseURL).origin || url.username || url.password ||
      !['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Search database must be served from this website');
  }
  if (encoding !== undefined && encoding !== 'gzip') throw new Error('Unsupported search encoding');
  let size = length(databaseSize, 'database', encoding === undefined);
  const compressedSize = length(transferSize, 'compressed database', encoding === undefined);
  const immutable = !url.search && !url.hash &&
    /\/(?:index|versions|documents|passages|history)\.[a-f0-9]{64}\.sqlite\.gz$/.test(url.pathname);
  const cache = retry ? 'reload' : immutable ? 'force-cache' : 'no-cache';
  const response = await fetch(url, {cache, mode: 'same-origin', redirect: 'error'});
  if (!response.ok || !response.body) {
    await response.body?.cancel().catch(() => {});
    throw new Error('Cannot fetch the local search database');
  }
  const contentLength = Number(response.headers.get('content-length')) || null;
  if (contentLength > MAX_DATABASE_BYTES) {
    await response.body.cancel().catch(() => {});
    throw new Error('Search database exceeds the transfer limit');
  }
  const metrics = {headersMs: performance.now() - started, responseBytes: 0,
    decodedBytes: 0, largestChunkBytes: 0, jsFullBufferBytes: 0, copyMs: 0, cache};
  let readable = response.body, reader, db, pointer;
  try {
    const prefix = await peek(readable);
    readable = prefix.stream;
    const gzip = prefix.header[0] === 0x1f && prefix.header[1] === 0x8b;
    if (gzip && encoding !== 'gzip') throw new Error('Unexpected compressed search artifact');
    if (!gzip) {
      // A host may send .gz with Content-Encoding:gzip; Fetch has already
      // decompressed that response. A plain SQLite response uses the same header checks.
      const headerSize = sqliteSize(prefix.header);
      if (size !== undefined && size !== headerSize) throw new Error('Database size differs from its descriptor');
      size = headerSize;
    }
    const receivedSize = gzip ? compressedSize : size;
    readable = readable.pipeThrough(new TransformStream({
      transform(chunk, controller) {
        metrics.responseBytes += chunk.byteLength;
        if (gzip) responseHash?.update(chunk);
        if (metrics.responseBytes > receivedSize) throw new Error('Search response exceeds its declared size');
        controller.enqueue(chunk);
      },
      flush() {
        if (metrics.responseBytes !== receivedSize) throw new Error('Search response is truncated');
        if (gzip && responseHash && responseHash.hex() !== transferSha256)
          throw new Error('Compressed search SHA-256 differs');
      }
    }));
    if (gzip) readable = readable.pipeThrough(new DecompressionStream('gzip'));
    reader = readable.getReader();
    db = new sqlite3.oo1.DB();
    // SQLite recommends a small zeroed tail for defensive reads of malformed
    // input. READONLY prevents expansion into that tail.
    pointer = sqlite3.wasm.alloc(size + 20);
    sqlite3.wasm.heap8u().fill(0, Number(pointer) + size, Number(pointer) + size + 20);
    const streamStarted = performance.now();
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      if (metrics.decodedBytes + value.byteLength > size) {
        throw new Error('Decompressed search database exceeds its declared size');
      }
      decodedHash?.update(value);
      const copied = performance.now();
      // Reacquire the heap view each time: WASM memory may have grown.
      sqlite3.wasm.heap8u().set(value, Number(pointer) + metrics.decodedBytes);
      metrics.copyMs += performance.now() - copied;
      metrics.decodedBytes += value.byteLength;
      metrics.largestChunkBytes = Math.max(metrics.largestChunkBytes, value.byteLength);
    }
    metrics.streamMs = performance.now() - streamStarted;
    if (metrics.decodedBytes !== size) throw new Error('Decompressed search database is truncated');
    if (decodedHash && decodedHash.hex() !== sha256) throw new Error('Search database SHA-256 differs');
    if (sqliteSize(sqlite3.wasm.heap8u().subarray(Number(pointer), Number(pointer) + 100)) !== size) {
      throw new Error('SQLite header differs from the downloaded database size');
    }
    const deserializeStarted = performance.now();
    const owned = pointer;
    pointer = undefined;
    // FREEONCLOSE takes ownership even when sqlite3_deserialize returns an
    // error. Do not also free this pointer in our exception handler.
    db.checkRc(sqlite3.capi.sqlite3_deserialize(db.pointer, 'main', owned, size, size + 20,
      sqlite3.capi.SQLITE_DESERIALIZE_FREEONCLOSE | sqlite3.capi.SQLITE_DESERIALIZE_READONLY));
    db.exec('PRAGMA query_only=ON');
    metrics.deserializeMs = performance.now() - deserializeStarted;
    metrics.totalMs = performance.now() - started;
    metrics.allocatedBytes = size + 20;
    metrics.wasmHeapBytes = sqlite3.wasm.heap8u().byteLength;
    metrics.transferredBytes = gzip || encoding === undefined ? metrics.responseBytes : null;
    metrics.contentLength = contentLength;
    return {db, metrics};
  } catch (error) {
    if (reader) await reader.cancel(error).catch(() => {});
    else await readable.cancel(error).catch(() => {});
    if (pointer !== undefined) sqlite3.wasm.dealloc(pointer);
    db?.close();
    throw error;
  } finally {
    reader?.releaseLock();
  }
}
