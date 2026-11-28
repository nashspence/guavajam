import assert from 'node:assert/strict';
import {test} from 'node:test';
import {MAX_METADATA_STATE_BYTES,serializeMetadata,readMetadataURL,writeMetadataURL}
  from '../src/a_guavajam_static_website_generator/assets/search-state.js';

test('short literal state preserves Unicode, case, numeric-looking, list and empty distinctions',()=>{
  const state=[{key:'Applies to',values:['01','001','A','a','café','CAFÉ','','🍋']}];
  const url=new URL('https://example.org/works/?q=coast&scope=history');
  writeMetadataURL(url,state);
  assert.deepEqual(JSON.parse(url.searchParams.get('metadata')),state);
  assert.deepEqual(readMetadataURL(url),state);
  assert.equal(url.hash,'');assert.equal(url.searchParams.get('q'),'coast');
});
test('large exact literals use a bounded fragment, never the static HTTP request path',()=>{
  const state=[{key:'Literal',values:['İ'.repeat(29000)]}];
  const url=new URL('https://example.org/works/?q=&scope=history&sort=relevance');
  writeMetadataURL(url,state);
  assert(url.hash.startsWith('#guavajam-metadata='));
  assert.equal(url.searchParams.has('metadata'),false);
  assert((url.pathname+url.search).length<256);
  assert.deepEqual(readMetadataURL(new URL(url.href)),state);
  const prior=url.href;
  writeMetadataURL(url,[{key:'Other',values:['001']}]);
  assert.deepEqual(readMetadataURL(url),[{key:'Other',values:['001']}]);
  assert.deepEqual(readMetadataURL(new URL(prior)),state);
  writeMetadataURL(url,[]);
  assert.deepEqual(readMetadataURL(url),[]);
  assert.equal(url.hash,'');assert.equal(url.searchParams.get('scope'),'history');
});
test('invalid or over-budget state resets safely and cannot mutate a URL before validation',()=>{
  const url=new URL('https://example.org/?q=coast#guavajam-metadata=%%%');
  assert.deepEqual(readMetadataURL(url),[]);
  const before=url.href;
  for(const state of [[{key:'Key',values:[1]}],[{key:'Key',values:['x'.repeat(65537)]}],
    [{key:'Key',values:Array.from({length:64},()=> 'x'.repeat(65500))}],
    [{key:'Key',values:Array.from({length:65},()=> '')}]]) {
    assert.throws(()=>serializeMetadata(state));
    assert.throws(()=>writeMetadataURL(url,state));
    assert.equal(url.href,before);
  }
  url.hash='#guavajam-metadata='+'a'.repeat(Math.ceil(MAX_METADATA_STATE_BYTES*4/3)+1);
  assert.deepEqual(readMetadataURL(url),[]);
  url.hash='';url.searchParams.set('metadata','{"bad":true}');
  assert.deepEqual(readMetadataURL(url),[]);
});
