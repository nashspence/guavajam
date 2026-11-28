import assert from 'node:assert/strict';
import {createHash, randomBytes} from 'node:crypto';
import test from 'node:test';
import {Sha256, sha256} from '../src/a_guavajam_static_website_generator/assets/search-hash.js';

test('incremental integrity matches the native SHA-256 oracle at padding and chunk boundaries', () => {
  for (const length of [0,1,31,55,56,57,63,64,65,127,128,129,1024,65536,1048576]) {
    const bytes = randomBytes(length);
    const expected = createHash('sha256').update(bytes).digest('hex');
    assert.equal(sha256(bytes), expected, 'one update: ' + length);
    const hasher = new Sha256();
    for (let offset = 0; offset < bytes.length; offset += 37) hasher.update(bytes.subarray(offset, offset + 37));
    assert.equal(hasher.hex(), expected, 'streamed: ' + length);
    assert.throws(() => hasher.hex());
    assert.throws(() => hasher.update(new Uint8Array()));
  }
  assert.equal(sha256(new TextEncoder().encode('abc')),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});
