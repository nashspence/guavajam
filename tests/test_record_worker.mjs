import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../records/worker.js', import.meta.url), 'utf8');
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;
const env = {RECORD_HOST: 'press.publisher.test',
             RECORD_TARGET_BASE: 'https://github.com/owner/press/releases/tag',
             RECORD_TAG_PATTERN: 'works/(sample-work)/(v1\\.0\\.0)'};
const address = 'https://press.publisher.test/works/sample-work/v1.0.0';

test('edition addresses redirect without fetching and can move to a recovered archive', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('record routing must not fetch edition bytes'); };
  try {
    for (const method of ['GET', 'HEAD']) {
      const response = await worker.fetch(new Request(address, {method}), env);
      assert.equal(response.status, 302);
      assert.equal(response.headers.get('Location'), env.RECORD_TARGET_BASE + '/works/sample-work/v1.0.0');
      assert.equal(response.headers.get('Cache-Control'), 'no-store');
      assert.equal(await response.text(), '');
    }
    const recovered = await worker.fetch(new Request(address), {...env, RECORD_TARGET_BASE: 'https://recovered.publisher.test'});
    assert.equal(recovered.headers.get('Location'), 'https://recovered.publisher.test/works/sample-work/v1.0.0');
    const catalog = await worker.fetch(new Request('https://press.publisher.test/'), env);
    assert.equal(catalog.headers.get('Location'), 'https://github.com/owner/press/releases');
  } finally { globalThis.fetch = previous; }
});

test('requests cannot choose another host, path, query destination, or write method', async () => {
  for (const url of ['https://other.test/works/sample-work/v1.0.0',
                     address + '?target=https://evil.test', address + '/',
                     address.replace('/works/', '/%77orks/'), address.replace('/works/', '//works/'),
                     address.replace('sample-work', 'elsewhere'), address.replace('v1.0.0', 'v01.0.0')]) {
    const response = await worker.fetch(new Request(url), env);
    assert.equal(response.status, 404, url);
    assert.equal(response.headers.get('Location'), null);
  }
  const response = await worker.fetch(new Request(address, {method: 'POST'}), env);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('Allow'), 'GET, HEAD');
});

test('invalid archive configuration cannot redirect readers', async () => {
  for (const RECORD_TARGET_BASE of ['http://archive.test', 'https://user:password@archive.test',
                                    'https://archive.test:8443', 'https://archive.test?q=x',
                                    'https://archive.test#x', 'https://archive.test/']) {
    const response = await worker.fetch(new Request(address), {...env, RECORD_TARGET_BASE});
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('Location'), null);
  }
  assert.equal((await worker.fetch(new Request(address), {...env, RECORD_TAG_PATTERN: undefined})).status, 503);
});
