/* One bounded literal-filter state, with short queries and larger fragments. */
export const MAX_METADATA_STATE_BYTES = 1024 * 1024;
const PREFIX = '#guavajam-metadata=';
const utf8 = new TextEncoder();

export function serializeMetadata(state) {
  if (!Array.isArray(state) || state.length > 64) throw new Error('Invalid metadata filter state');
  let bytes = 0;
  const text = value => {
    if (typeof value !== 'string' || value.length > 65536) throw new Error('Invalid metadata literal');
    const size = utf8.encode(value).length;
    if (size > 65536 || (bytes += size) > MAX_METADATA_STATE_BYTES)
      throw new Error('Selected filters exceed the 1-MiB state budget');
  };
  for (const item of state) {
    if (!item || !Array.isArray(item.values) || item.values.length > 64) throw new Error('Invalid metadata values');
    text(item.key);
    for (const value of item.values) text(value);
  }
  const raw = JSON.stringify(state);
  if (utf8.encode(raw).length > MAX_METADATA_STATE_BYTES)
    throw new Error('Selected filters exceed the 1-MiB state budget');
  return raw;
}

function parse(raw) {
  if (typeof raw !== 'string' || raw.length > MAX_METADATA_STATE_BYTES) return [];
  try {
    const state = JSON.parse(raw);
    serializeMetadata(state);
    return state;
  } catch { return []; }
}

function base64(bytes) {
  let text = '';
  for (let start = 0; start < bytes.length; start += 8192)
    text += String.fromCharCode(...bytes.subarray(start, start + 8192));
  return btoa(text).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

export function writeMetadataURL(url, state) {
  const raw = serializeMetadata(state);
  url.searchParams.delete('metadata');
  if (url.hash.startsWith(PREFIX)) url.hash = '';
  if (!state.length) return;
  if (encodeURIComponent(raw).length <= 2048) url.searchParams.set('metadata', raw);
  else url.hash = PREFIX + base64(utf8.encode(raw));
}

export function readMetadataURL(url) {
  if (!url.hash.startsWith(PREFIX)) return parse(url.searchParams.get('metadata'));
  const encoded = url.hash.slice(PREFIX.length);
  if (!encoded.length || encoded.length > Math.ceil(MAX_METADATA_STATE_BYTES * 4 / 3) ||
      !/^[A-Za-z0-9_-]+$/.test(encoded)) return [];
  try {
    const text = atob(encoded.replaceAll('-', '+').replaceAll('_', '/'));
    if (text.length > MAX_METADATA_STATE_BYTES) return [];
    const bytes = Uint8Array.from(text, char => char.charCodeAt(0));
    return parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
  } catch { return []; }
}
