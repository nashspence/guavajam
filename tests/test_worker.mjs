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

test('typed MP4/MP3 stream full objects and exactly the requested byte ranges', async () => {
  const old = globalThis.fetch;
  const mp4 = 'https://assets.publisher.test/sha256/' + 'b'.repeat(64) + '.mp4';
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    if (options.headers.Range === 'bytes=500-599')
      return new Response('x'.repeat(100), {status: 206, headers: {
        'Content-Type':'video/mp4', 'Content-Range':'bytes 500-599/60000000',
        'Content-Length':'100','Accept-Ranges':'bytes'}});
    return new Response('x'.repeat(32), {status: 200, headers: {
      'Content-Type':'video/mp4','Content-Length':'60000000','Accept-Ranges':'bytes'}});
  };
  try {
    const part=await worker.fetch(new Request(mp4,{headers:{Range:'bytes=500-599'}}),env);
    assert.equal(part.status,206);
    assert.equal(part.headers.get('Content-Range'),'bytes 500-599/60000000');
    assert.equal(part.headers.get('Content-Length'),'100');
    assert.equal(part.headers.get('Accept-Ranges'),'bytes');
    assert.equal(part.headers.get('Access-Control-Allow-Origin'),'*');
    assert.equal(await part.text(),'x'.repeat(100));
    assert.equal(calls[0].options.headers.Range,'bytes=500-599');
    assert.equal('cf' in calls[0].options,false,'range requests must not trigger whole-object cache fill');
    const response=await worker.fetch(new Request(mp4,{method:'HEAD'}),env);
    assert.equal(response.status,200);
    assert.equal(response.headers.get('Content-Length'),'60000000');
    assert.equal(response.headers.get('Accept-Ranges'),'bytes');
    assert.equal(await response.text(),'');
  } finally {globalThis.fetch=old;}
});

test('media upstream range failures and invalid partial metadata fail closed', async () => {
  const old=globalThis.fetch;
  const url='https://assets.publisher.test/sha256/'+'b'.repeat(64)+'.mp4';
  try {
    for(const [upstream,code] of [
      [new Response('whole', {status:200,headers:{'Content-Type':'video/mp4'}}),502],
      [new Response('x', {status:206,headers:{'Content-Type':'video/mp4','Content-Range':'bytes 1-1/100','Content-Length':'1'}}),502],
      [new Response('xxx', {status:206,headers:{'Content-Type':'video/mp4','Content-Range':'bytes 10-12/100','Content-Length':'2'}}),502],
      [new Response(null, {status:416,headers:{'Content-Range':'bytes */100'}}),416],
    ]){
      globalThis.fetch=async()=>upstream;
      const received=await worker.fetch(new Request(url,{headers:{Range:'bytes=10-12'}}),env);
      assert.equal(received.status,code);
      if(code===416)assert.equal(received.headers.get('Content-Range'),'bytes */100');
    }
    globalThis.fetch=async()=>{throw new Error('unexpected fetch')};
    assert.equal((await worker.fetch(new Request(url,{headers:{Range:'bytes=10-12,20-30'}}),env)).status,416);
    assert.equal((await worker.fetch(new Request(url.replace('.mp4','.bin'),{headers:{Range:'bytes=1-2'}}),env)).status,416);
  } finally {globalThis.fetch=old;}
});
