// Native full-scope oracles over real HTTP, with explicitly simulated network profiles.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox} from 'playwright';

const [fixturePath, output] = process.argv.slice(2);
const fixture = JSON.parse(await fsp.readFile(fixturePath, 'utf8'));
const root = path.resolve(fixture.root);
let simulated = false, transferred = 0;
const server = http.createServer(async (request, response) => {
  try {
    const target = path.resolve(root, '.' + decodeURIComponent(new URL(request.url, 'http://localhost').pathname));
    assert(target.startsWith(root + path.sep));
    const stat = await fsp.stat(target);
    if (simulated) await new Promise(resolve => setTimeout(resolve, 40));
    response.writeHead(200, {'Content-Type': target.endsWith('.html') ? 'text/html' :
      target.endsWith('.wasm') ? 'application/wasm' : /\.(mjs|js)$/.test(target) ? 'text/javascript' :
      'application/octet-stream', 'Content-Length': stat.size, 'Cache-Control': 'no-store'});
    for await (const buffer of fs.createReadStream(target, {highWaterMark: 65536})) {
      if (response.destroyed) break;
      transferred += buffer.length;
      if (simulated) await new Promise(resolve => setTimeout(resolve, buffer.length / 2500));
      if (!response.write(buffer)) await new Promise(resolve => response.once('drain', resolve));
    }
    response.end();
  } catch { if (!response.headersSent) response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = 'http://127.0.0.1:' + server.address().port + '/';
const reports = [];
const profiles = [
  {name: 'desktop-loopback-chromium', engine: chromium, viewport: {width: 1280, height: 900}},
  {name: 'desktop-loopback-firefox', engine: firefox, viewport: {width: 1280, height: 900}},
  {name: 'mobile-shaped-chromium-20Mbps-40ms', engine: chromium, viewport: {width: 390, height: 844}, slow: true},
];
try {
  for (const profile of profiles) {
    simulated = !!profile.slow;
    const browser = await profile.engine.launch();
    try {
      const context = await browser.newContext({viewport: profile.viewport});
      const page = await context.newPage();
      await page.goto(base + 'test.html');
      await page.evaluate(() => {
        window.messages = [];
        window.worker = new Worker('/search-worker.js', {type: 'module'});
        window.worker.onmessage = event => window.messages.push(event.data);
        window.worker.onerror = event => window.messages.push({id: 0, ok: false, error: event.message});
      });
      async function send(data, timeout) {
        const started = performance.now(), before = transferred;
        await page.evaluate(data => window.worker.postMessage(data), data);
        try {
          await page.waitForFunction(id => window.messages.some(item => item.id === id && !item.progress), data.id, {timeout});
        } catch (error) {
          const last = await page.evaluate(() => window.messages.at(-1));
          throw new Error(JSON.stringify({profile: profile.name, term: data.term, page: data.page, last}), {cause: error});
        }
        const result = await page.evaluate(id => window.messages.find(item => item.id === id && !item.progress), data.id);
        assert.equal(result.ok, true, JSON.stringify(result));
        return {result, ms: performance.now() - started, transferred: transferred - before};
      }
      const descriptor = fixture.descriptor;
      const initialized = await send({action: 'init', id: 0, scope: 'history', database: base + descriptor.url,
        databaseSize: descriptor.size, transferSize: descriptor.transfer_size, encoding: descriptor.encoding,
        sha256: descriptor.sha256, transferSha256: descriptor.transfer_sha256,
        expectedGeneration: descriptor.generation}, fixture.budgets.cold_init_ms);
      const measurements = [];
      let id = 0;
      for (const oracle of fixture.oracles) {
        for (const number of oracle.total > 20 ? [1, 2] : [1]) {
          const measured = await send({action: 'search', id: ++id, scope: 'history',
            generation: initialized.result.generation, term: oracle.term, sort: 'relevance', page: number},
          fixture.budgets.query_ms);
          const result = measured.result, expected = oracle.first.slice((number - 1) * 20, number * 20);
          assert.equal(result.total, oracle.total);
          assert.deepEqual(result.rows.map(row => row.id), expected.map(row => row.id));
          for (let index = 0; index < expected.length; index++)
            assert(Math.abs(result.rows[index].score - expected[index].score) < 1e-9);
          assert(result.rows.every(row => row.excerpt.includes('\uE000')));
          if (descriptor.encoding === 'sqlite-chunks') {
            assert(result.transport.networkPeak <= 1);
            assert(result.transport.decodedPeak <= 32 * 1024**2);
            assert(result.transport.compressedPeak <= 8 * 1024**2);
          }
          measurements.push({term: oracle.term, page: number, total: result.total, ms: measured.ms,
            transferred: measured.transferred, transport: result.transport});
        }
      }
      reports.push({profile: profile.name, passed: true, init_ms: initialized.ms,
        init_transferred: initialized.transferred, measurements});
      await context.close();
    } finally { await browser.close(); }
  }
} finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
await fsp.writeFile(output, JSON.stringify({passed: true, reports}, null, 2));
