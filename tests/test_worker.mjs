import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('../storage/worker.js', import.meta.url), 'utf8');
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;
const env = {ASSET_HOST: 'assets.publisher.test', B2_PUBLIC_BASE: 'https://f000.backblazeb2.com/file/test-bucket'};
const path = '/sha256/' + 'a'.repeat(64) + '.svg';

test('only approved image paths can reach the fixed bucket; bytes remain unchanged', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return new Response('<svg/>', {headers: {'Content-Type': 'image/svg+xml', 'Content-Length': '6'}});
  };
  try {
    for (const url of ['https://evil.test' + path, 'https://assets.publisher.test/file/other-bucket/x.svg',
                       'https://assets.publisher.test' + path + '?x=1',
                       'https://assets.publisher.test' + path.replace('.svg', '.gif'),
                       'https://assets.publisher.test' + path.replace('/sha256/', '/%73ha256/'),
                       'https://assets.publisher.test' + path.replace('/sha256/', '/sha256%2f/')]) {
      assert.equal((await worker.fetch(new Request(url), env)).status, 404);
    }
    assert.equal((await worker.fetch(new Request('https://assets.publisher.test' + path, {method: 'POST'}), env)).status, 405);
    assert.equal(calls.length, 0);
    const response = await worker.fetch(new Request('https://assets.publisher.test' + path), env);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Cache-Control'), 'public, max-age=31536000, immutable, no-transform');
    assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
    assert.equal(calls[0].url, env.B2_PUBLIC_BASE + path);
    assert.equal(calls[0].options.redirect, 'manual');
    assert.equal(await response.text(), '<svg/>');
    // The proxy does not hash objects; Actions performs cryptographic checks.
    assert.equal(calls.length, 1);
  } finally { globalThis.fetch = original; }
});

test('redirects, missing objects and upstream errors fail closed without caching', async () => {
  const original = globalThis.fetch;
  try {
    for (const [upstream, expected] of [
      [new Response(null, {status: 302, headers: {Location: 'http://127.0.0.1/'}}), 502],
      [new Response(null, {status: 404}), 404],
      [new Response(null, {status: 500}), 502],
    ]) {
      globalThis.fetch = async () => upstream;
      const response = await worker.fetch(new Request('https://assets.publisher.test' + path), env);
      assert.equal(response.status, expected);
      assert.equal(response.headers.get('Cache-Control'), 'no-store');
    }
    globalThis.fetch = async () => { throw new Error('network'); };
    assert.equal((await worker.fetch(new Request('https://assets.publisher.test' + path), env)).status, 502);
  } finally { globalThis.fetch = original; }
});

test('opaque content is always a bounded attachment regardless of upstream MIME', async () => {
  const original = globalThis.fetch;
  const bin = path.replace('.svg','.bin');
  try {
    for (const type of ['text/html','application/pdf','application/x-unfamiliar','image/svg+xml']) {
      globalThis.fetch = async () => new Response('<script>active()</script>', {headers:{'Content-Type':type}});
      const response = await worker.fetch(new Request('https://assets.publisher.test'+bin),env);
      assert.equal(response.headers.get('Content-Type'),'application/octet-stream');
      assert.equal(response.headers.get('Content-Disposition'),'attachment; filename="'+'a'.repeat(64)+'.bin"');
      assert.equal(response.headers.get('X-Content-Type-Options'),'nosniff');
      assert.match(response.headers.get('Content-Security-Policy'),/sandbox/);
      assert.equal(await response.text(),'<script>active()</script>');
    }
    globalThis.fetch = async () => new Response('x',{headers:{'Content-Length':String(10*1024*1024+1)}});
    assert.equal((await worker.fetch(new Request('https://assets.publisher.test'+bin),env)).status,502);
    globalThis.fetch = async () => new Response(new Uint8Array(10*1024*1024+1));
    const tooLarge = await worker.fetch(new Request('https://assets.publisher.test'+bin),env);
    await assert.rejects(tooLarge.arrayBuffer());
    globalThis.fetch = async () => new Response('active',{headers:{'Content-Type':'text/html'}});
    assert.equal((await worker.fetch(new Request('https://assets.publisher.test'+path),env)).status,502);
    const head = await worker.fetch(new Request('https://assets.publisher.test'+bin,{method:'HEAD'}),env);
    assert.equal(head.status,200);assert.equal(await head.text(),'');
  } finally { globalThis.fetch = original; }
});
