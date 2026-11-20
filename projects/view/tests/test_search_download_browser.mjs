// Real HTTP/cache and SQLite WASM qualification. Timings are evidence, not gates.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {gzipSync} from 'node:zlib';
import {chromium, firefox, webkit} from 'playwright';

const [fixturePath, output] = process.argv.slice(2);
assert(fixturePath && output, 'Use run_search_download_browser.py');
const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
const root = path.resolve(fixture.root);
await fs.mkdir(output, {recursive: true});
const requests = [];
const cachedFiles = new Map();
let sample = '';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.wasm': 'application/wasm', '.sqlite': 'application/vnd.sqlite3', '.gz': 'application/gzip'};
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    const match = /^\/bench\/([a-z-]+)\/(.*)$/.exec(url.pathname);
    assert(match, 'Unknown fixture route');
    const [, profile, relative] = match;
    const special = /^(no-store|database-ce)\/(.*)$/.exec(relative);
    const clean = special ? special[2] : relative;
    const filename = path.resolve(root, decodeURIComponent(clean || 'index.html'));
    assert(filename.startsWith(root + path.sep));
    if (!cachedFiles.has(filename)) {
      const raw = await fs.readFile(filename);
      cachedFiles.set(filename, {raw, compressed: /\.(?:js|mjs|wasm)$/.test(filename) ? gzipSync(raw, {level: 6}) : null,
        etag: '"' + crypto.createHash('sha256').update(raw).digest('hex') + '"'});
    }
    const file = cachedFiles.get(filename);
    const database = /\.sqlite(?:\.gz)?$/.test(filename);
    const encoding = special?.[1] === 'database-ce' ||
      (profile === 'controlled-runtime-gzip' && /\.(?:js|mjs|wasm)$/.test(filename));
    // A .gz database already contains exactly one gzip layer. HTTP decoding
    // makes its Response.body raw SQLite; native module/WASM compression does
    // not alter their import or instantiate code.
    const body = encoding && !database ? file.compressed : file.raw;
    const cacheControl = special?.[1] === 'no-store' ? 'no-store' :
      database ? 'public, max-age=0' : 'no-cache';
    const record = {sample, profile, relative, database, bytes: 0, status: 200};
    requests.push(record);
    const controlled = profile.startsWith('controlled');
    if (controlled) await delay(40);
    if (req.headers['if-none-match'] === file.etag && special?.[1] !== 'no-store') {
      record.status = 304;
      res.writeHead(304, {'ETag': file.etag, 'Cache-Control': cacheControl});
      res.end(); return;
    }
    res.writeHead(200, {'Content-Type': types[path.extname(filename)] || 'application/octet-stream',
      'Content-Length': body.length, 'X-Content-Type-Options': 'nosniff',
      'Cache-Control': cacheControl, 'ETag': file.etag, ...(encoding ? {'Content-Encoding': 'gzip'} : {})});
    if (!controlled) {
      record.bytes = body.length;
      res.end(body); return;
    }
    let closed = false;
    res.on('close', () => { closed = true; });
    for (let position = 0; position < body.length && !closed; position += 16384) {
      const chunk = body.subarray(position, position + 16384);
      // Nominal 10 Mbit/s per response, plus 40 ms before response headers.
      await delay(chunk.length / 1250000 * 1000);
      if (closed) break;
      record.bytes += chunk.length;
      res.write(chunk);
    }
    res.end();
  } catch (error) { res.writeHead(404); res.end(String(error.message)); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const engines = {chromium, firefox, webkit};
const selected = (process.env.GUAVAJAM_BROWSER_ENGINES || 'chromium,firefox,webkit').split(',');
assert(selected.length && selected.every(name => Object.hasOwn(engines, name)));
const samples = [];
const summarize = values => {
  const sorted = [...values].sort((a, b) => a - b);
  return {median: sorted[Math.floor(sorted.length / 2)], p95: sorted[Math.ceil(sorted.length * .95) - 1]};
};

async function freshContext(type, browser) {
  if (fixture.requireCacheReuse === false) {
    const context = await browser.newContext();
    return {context, close: () => context.close()};
  }
  // WebKit ephemeral contexts do not retain these HTTP responses. A new
  // normal profile starts cold while enabling native cache qualification.
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'guavajam-download-cache-'));
  try {
    const context = await type.launchPersistentContext(directory);
    return {context, close: async () => {
      try { await context.close(); }
      finally { await fs.rm(directory, {recursive: true, force: true}); }
    }};
  } catch (error) {
    await fs.rm(directory, {recursive: true, force: true});
    throw error;
  }
}

async function run(page, item, mode, profile, label, overrides = {}) {
  sample = label;
  const database = origin + `/bench/${profile}/` + (mode === 'raw-buffer' ? item.raw : item.gzip);
  const result = await page.evaluate(async options => {
    return await new Promise((resolve, reject) => {
      const start = performance.now();
      const worker = new Worker(new URL('search_download_worker.mjs', location.href), {type: 'module'});
      const timer = setTimeout(() => { worker.terminate(); reject(new Error('Download qualification timed out')); }, 180000);
      let readyMs;
      worker.onerror = event => { clearTimeout(timer); worker.terminate(); reject(new Error(event.message)); };
      worker.onmessage = ({data}) => {
        if (data.phase === 'loaded') { readyMs = performance.now() - start; return; }
        if (data.phase === 'complete') {
          clearTimeout(timer); worker.terminate();
          resolve({...data, readyMs, roundTripMs: performance.now() - start});
        }
      };
      worker.postMessage(options);
    });
  }, {mode, database, databaseSize: item.rawBytes, transferSize: item.gzipBytes,
      encoding: mode === 'raw-buffer' ? undefined : 'gzip', queries: item.queries, ...overrides});
  const delivered = requests.filter(record => record.sample === label);
  return {...result, requests: delivered, databaseNetworkBytes: delivered.filter(r => r.database).reduce((n, r) => n + r.bytes, 0),
    totalNetworkBytes: delivered.reduce((n, r) => n + r.bytes, 0)};
}

function verify(item, result, label) {
  assert(result.ok, `${label}: ${result.error}`);
  for (let index = 0; index < item.queries.length; index++) {
    const query = item.queries[index], actual = result.results[index];
    assert.equal(actual.count, query.countExpected, `${label}: ${query.name} count`);
    assert.equal(actual.rows.length, query.expected.length, `${label}: ${query.name} result length`);
    for (let row = 0; row < actual.rows.length; row++) {
      const {score, ...identity} = actual.rows[row];
      const {score: expectedScore, ...expectedIdentity} = query.expected[row];
      assert.deepEqual(identity, expectedIdentity, `${label}: ${query.name} result identity`);
      assert(Math.abs(score - expectedScore) <= 1e-12, `${label}: ${query.name} relevance score`);
    }
  }
}

try {
  for (const name of selected) {
    let browser = await engines[name].launch();
    const version = browser.version();
    if (fixture.requireCacheReuse !== false) { await browser.close(); browser = undefined; }
    try {
      for (const item of fixture.cases) {
        for (const profile of fixture.profiles) {
          for (const mode of ['raw-buffer', 'gzip-buffer', 'gzip-stream']) {
            for (let repetition = 0; repetition < fixture.repetitions; repetition++) {
              const session = await freshContext(engines[name], browser);
              const {context} = session;
              try {
                const page = await context.newPage();
                await page.goto(origin + `/bench/${profile}/index.html`);
                assert.equal(await page.evaluate(() => crossOriginIsolated), false);
                for (const temperature of ['cold', 'warm']) {
                  const label = [name, item.fixture, item.name, profile, mode, repetition, temperature].join('/');
                  const result = await run(page, item, mode, profile, label);
                  verify(item, result, label);
                  if (temperature === 'cold') {
                    assert.equal(result.databaseNetworkBytes, mode === 'raw-buffer' ? item.rawBytes : item.gzipBytes);
                  } else if (mode === 'raw-buffer') {
                    // The raw baseline uses no-cache. A browser may revalidate a
                    // retained response or fetch it again; retention is optional
                    // (RFC 9111 section 3), including for this bounded fixture.
                    const dbRequests = result.requests.filter(r => r.database);
                    assert.equal(dbRequests.length, 1, label + ': raw revalidation or refetch');
                    assert([0, item.rawBytes].includes(result.databaseNetworkBytes), label + ': complete raw response');
                    assert.equal(dbRequests[0].status, result.databaseNetworkBytes ? 200 : 304);
                  } else if (fixture.requireCacheReuse !== false) {
                    // The bounded immutable gzip fixture exercises force-cache.
                    // Larger stress fixtures only report browser retention.
                    assert.equal(result.databaseNetworkBytes, 0, label + ': warm body transfer');
                    assert.equal(result.requests.filter(r => r.database).length, 0, label + ': immutable cache reuse');
                  }
                  samples.push({engine: name, version, fixture: item.fixture, variant: item.name,
                    profile, mode, repetition, temperature, rawBytes: item.rawBytes, gzipBytes: item.gzipBytes,
                    readyMs: result.readyMs, databaseNetworkBytes: result.databaseNetworkBytes,
                    totalNetworkBytes: result.totalNetworkBytes, metrics: result.metrics,
                    requests: result.requests,
                    queries: result.results.map(q => ({name: q.name, timings: q.timings}))});
                }
              } finally { await session.close(); }
            }
            const group = samples.filter(s => s.engine === name && s.fixture === item.fixture && s.variant === item.name &&
              s.profile === profile && s.mode === mode && s.temperature === 'cold');
            console.log(`${name} ${item.fixture}/${item.name} ${profile} ${mode}: ` +
              JSON.stringify({readyMs: summarize(group.map(s => s.readyMs)), bytes: group[0].totalNetworkBytes,
                wasmHeapBytes: group[0].metrics.wasmHeapBytes, jsFullBufferBytes: group[0].metrics.jsFullBufferBytes}));
            await fs.writeFile(path.join(output, 'search-download-browser-samples.json'), JSON.stringify(samples, null, 2) + '\n');
          }
        }
      }
      // The selected streaming loader handles cache replacement, host policy,
      // transparent HTTP decoding and allocation bounds independently of speed.
      const item = fixture.cases[0], profile = 'local';
      const session = await freshContext(engines[name], browser);
      const {context} = session;
      try {
        const page = await context.newPage();
        await page.goto(origin + '/bench/local/index.html');
        const first = await run(page, item, 'gzip-stream', profile, name + '/cache-first');
        verify(item, first, name + '/cache-first');
        const next = await run(page, item, 'gzip-stream', profile, name + '/cache-next', {
          database: origin + '/bench/local/' + item.next, databaseSize: item.nextRawBytes, transferSize: item.nextGzipBytes});
        verify(item, next, name + '/cache-next');
        assert.equal(next.databaseNetworkBytes, item.nextGzipBytes, 'a changed content hash downloads the new generation');
        const transparent = await run(page, item, 'gzip-stream', profile, name + '/database-ce', {
          database: origin + '/bench/local/database-ce/' + item.gzip});
        verify(item, transparent, name + '/database-ce');
        for (let repetition = 0; repetition < 2; repetition++) {
          const noStore = await run(page, item, 'gzip-stream', profile, name + '/no-store/' + repetition, {
            database: origin + '/bench/local/no-store/' + item.gzip});
          verify(item, noStore, name + '/no-store/' + repetition);
          assert.equal(noStore.databaseNetworkBytes, item.gzipBytes, 'force-cache does not override no-store');
        }
        for (const databaseSize of [item.rawBytes - 1, item.rawBytes + 1, 256 * 1024 * 1024 + 1]) {
          const invalid = await run(page, item, 'gzip-stream', profile, name + '/wrong-size/' + databaseSize, {databaseSize});
          assert.equal(invalid.ok, false, 'wrong or oversized expected length must fail');
        }
      } finally { await session.close(); }
      console.log(`${name}: exact ranking, cache policy, replacement, HTTP decoding, no-store and bounds passed`);
    } finally { await browser?.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
const groups = new Map();
for (const record of samples) {
  const key = [record.engine, record.fixture, record.variant, record.profile, record.mode, record.temperature].join('/');
  const group = groups.get(key) || [];
  group.push(record); groups.set(key, group);
}
const summary = [...groups.entries()].map(([key, records]) => ({key, readyMs: summarize(records.map(r => r.readyMs)),
  networkBytes: records[0].totalNetworkBytes, databaseNetworkBytes: records[0].databaseNetworkBytes,
  wasmHeapBytes: records[0].metrics.wasmHeapBytes, jsFullBufferBytes: records[0].metrics.jsFullBufferBytes,
  queryMs: summarize(records.flatMap(r => r.queries.flatMap(q => q.timings)))}));
await fs.writeFile(path.join(output, 'search-download-browser-report.json'), JSON.stringify({
  gzipLevel: fixture.gzipLevel, repetitions: fixture.repetitions,
  requireCacheReuse: fixture.requireCacheReuse !== false,
  rawCacheBehavior: 'Raw no-cache responses may revalidate or refetch; immutable gzip reuse remains required for the bounded fixture',
  controlledProfile: '40 ms before headers and nominal 10 Mbit/s per response; runtime HTTP gzip uses precomputed level-6 representations; timing is not a CI threshold',
  isolation: fixture.requireCacheReuse === false
    ? 'Benchmarks use a new ephemeral context with an empty HTTP cache; browser process and compilation cache may be reused'
    : 'Cache qualification uses a new persistent profile per sample, enabling HTTP caching with an empty initial cache in every engine',
  runtimeCacheControl: 'no-cache; warm runtime requests revalidate, while hashed database force-cache avoids its revalidation',
  memory: 'Observed WASM heap capacity and explicit whole JS buffer allocation, not total process peak',
  summary, samples}, null, 2) + '\n');
