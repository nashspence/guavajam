/* Small static option trees; no SQLite worker or index is needed to use filters. */
import {sha256} from './search-hash.js';
import {serializeMetadata} from './search-state.js';

const NODE_LIMIT = 1024 * 1024, DEPTH_LIMIT = 16;
const order = (a, b) => {
  const left = Array.from(a, char => char.codePointAt(0)), right = Array.from(b, char => char.codePointAt(0));
  for (let n = 0; n < Math.min(left.length, right.length); n++) if (left[n] !== right[n]) return left[n] - right[n];
  return left.length - right.length;
};

async function node(descriptor, base, signal, identity) {
  const url = new URL(descriptor?.url, base);
  if (url.origin !== location.origin || url.username || url.password || url.search || url.hash ||
      !/^[a-f0-9]{64}$/.test(descriptor?.sha256 || '') ||
      !Number.isSafeInteger(descriptor.size) || descriptor.size < 1 || descriptor.size > NODE_LIMIT)
    throw new Error('Invalid filter option descriptor');
  const response = await fetch(url, {signal, cache: 'force-cache', mode: 'same-origin', redirect: 'error'});
  if (!response.ok || !response.body) throw new Error('Cannot load filter options');
  const reader = response.body.getReader(), bytes = new Uint8Array(descriptor.size);
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      if (size + value.length > bytes.length) throw new Error('Filter option response exceeds its declared size');
      bytes.set(value, size); size += value.length;
    }
    if (size !== bytes.length || sha256(bytes) !== descriptor.sha256) throw new Error('Filter option integrity differs');
  } catch (error) { await reader.cancel(error).catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  const value = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
  if (value.schema !== 'guavajam.search-facets.v1' || value.scope !== identity.scope ||
      identity.generation && value.generation !== identity.generation ||
      identity.revision !== undefined && value.revision !== identity.revision)
    throw new Error('Filter option generation or scope differs');
  if (value.items || value.children) {
    const count = value.items ? value.items.length : value.children.reduce((sum, child) => {
      if (!Number.isSafeInteger(child.count) || child.count < 0) throw new Error('Invalid option count');
      return sum + child.count;
    }, 0);
    if (!Number.isSafeInteger(descriptor.count) || descriptor.count !== count)
      throw new Error('Filter option count differs');
  }
  return {value, url};
}

function controls(parent, select, label, root, base, signal, identity, selected = () => select.value) {
  const panel = document.createElement('div'); panel.className = 'guavajam-facet-options';
  const input = document.createElement('input'); input.type = 'search'; input.maxLength = 128;
  input.placeholder = 'Find ' + label.toLowerCase() + ' by prefix'; input.setAttribute('aria-label', input.placeholder);
  const prev = document.createElement('button'), next = document.createElement('button');
  for (const button of [prev, next]) { button.type = 'button'; button.className = 'guavajam-facet-page'; }
  for (const [button, direction, name] of [[prev, 'left', 'Earlier options'], [next, 'right', 'More options']]) {
    button.setAttribute('aria-label', name); button.title = name;
    button.append(document.querySelector('#guavajam-icon-arrow-' + direction).content.cloneNode(true));
  }
  const status = document.createElement('span'); status.setAttribute('role', 'status');
  status.className = 'guavajam-facet-count'; status.tabIndex = 0;
  panel.append(input, select, status, prev, next); parent.append(panel);
  let epoch = 0, prefix = '', trail = [], leaf, history = [], requestController, timer, total = 0, itemMap = new Map();

  async function read(descriptor, url, request) {
    const result = await node(descriptor, url, request.signal, identity);
    if (request.signal.aborted) throw new DOMException('Option request canceled', 'AbortError');
    return result;
  }

  async function descend(descriptor, url, path = [], request = requestController) {
    for (let depth = path.length; depth < DEPTH_LIMIT; depth++) {
      const loaded = await read(descriptor, url, request);
      if (Array.isArray(loaded.value.items)) {
        if (loaded.value.items.length > 32) throw new Error('Too many filter options in one page');
        return {leaf: loaded.value.items, trail: path};
      }
      const children = loaded.value.children;
      if (!Array.isArray(children) || !children.length || children.length > 32)
        throw new Error('Invalid filter option tree');
      const index = Math.max(0, children.findIndex(child => order(child.last, prefix) >= 0));
      if (children.every(child => order(child.last, prefix) < 0)) return {leaf: [], trail: []};
      path.push({children, index, url: loaded.url});
      descriptor = children[index]; url = loaded.url;
    }
    throw new Error('Filter options exceed the tree-depth budget');
  }

  // Entire matching subtrees contribute counts without downloading their
  // leaves. Only the two prefix boundaries need descent (bounded fanout/depth).
  async function matchingCount(descriptor, url, depth = 0, needle = prefix, request = requestController) {
    if (depth >= DEPTH_LIMIT) throw new Error('Filter options exceed the tree-depth budget');
    const loaded = await read(descriptor, url, request);
    if (Array.isArray(loaded.value.items)) return loaded.value.items.filter(item => item.order.startsWith(needle)).length;
    const children = loaded.value.children;
    if (!Array.isArray(children) || !children.length || children.length > 32) throw new Error('Invalid filter option tree');
    let count = 0;
    for (const child of children) {
      if (child.first.startsWith(needle) && child.last.startsWith(needle)) count += child.count;
      else if (order(child.last, needle) >= 0 && (order(child.first, needle) <= 0 || child.first.startsWith(needle)))
        count += await matchingCount(child, loaded.url, depth + 1, needle, request);
    }
    return count;
  }

  async function following(path, request = requestController) {
    path = path.map(level => ({...level}));
    while (path.length) {
      const last = path.at(-1);
      if (++last.index < last.children.length) return descend(last.children[last.index], last.url, path, request);
      path.pop();
    }
    return {leaf: [], trail: []};
  }

  function display() {
    const current = selected(), blank = select.querySelector('option[data-all]')?.cloneNode(true);
    select.replaceChildren(); if (blank) select.append(blank);
    itemMap = new Map();
    for (const item of leaf.filter(item => item.order.startsWith(prefix))) {
      if (typeof item.key !== 'string' || typeof item.name !== 'string' || !Number.isSafeInteger(item.total))
        throw new Error('Invalid filter option');
      const option = document.createElement('option'); option.value = item.key;
      option.textContent = (item.name || '(empty string)') + ' (' + item.total + ')';
      itemMap.set(item.key, item); select.append(option);
    }
    if (current !== '' && ![...select.options].some(option => option.value === current)) {
      const option = document.createElement('option'); option.value = current; option.textContent = current;
      select.prepend(option);
    }
    if ([...select.options].some(option => option.value === current)) select.value = current;
    prev.disabled = !history.length;
    next.disabled = !leaf.length || !trail.some(level => level.index + 1 < level.children.length) ||
      (prefix && order(leaf.at(-1).order.slice(0, prefix.length), prefix) > 0);
    status.textContent = String(total);
    status.setAttribute('aria-label', total + ' matching ' + label.toLowerCase());
    status.title = status.getAttribute('aria-label');
    panel.removeAttribute('aria-busy');
    select.dispatchEvent(new CustomEvent('guavajam-options', {detail: itemMap}));
  }

  async function load(direction = 0) {
    clearTimeout(timer); requestController?.abort();
    requestController = new AbortController();
    if (signal.aborted) return;
    const thisRequest = requestController;
    const abort = () => thisRequest.abort(); signal.addEventListener('abort', abort, {once: true});
    const request = ++epoch; status.textContent = '…'; status.setAttribute('aria-label', 'Loading ' + label.toLowerCase());
    panel.setAttribute('aria-busy', 'true'); prev.disabled = next.disabled = true;
    try {
      let result, matchingTotal = total;
      if (direction < 0) result = history.pop();
      else if (direction > 0) { history.push({leaf, trail}); result = await following(trail); }
      else { prefix = input.value.toLowerCase(); history = []; result = await descend(root, base);
        matchingTotal = prefix ? await matchingCount(root, base) : root.count; }
      if (request !== epoch || thisRequest.signal.aborted) return;
      total = matchingTotal; ({leaf, trail} = result); display();
    } catch (error) {
      if (request === epoch && error.name !== 'AbortError') {
        status.textContent = '—'; status.setAttribute('aria-label', 'Options unavailable. Retry with Find options.');
        status.title = status.getAttribute('aria-label'); panel.removeAttribute('aria-busy');
        next.disabled = prev.disabled = true;
      }
    } finally { signal.removeEventListener('abort', abort); }
  }
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => load(), 200); });
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); load(); } });
  prev.addEventListener('click', () => load(-1)); next.addEventListener('click', () => load(1));
  signal.addEventListener('abort', () => {
    clearTimeout(timer); requestController?.abort(); panel.before(select); panel.remove();
  }, {once: true});
  load();
  return () => itemMap;
}

export async function attachFacets(form, descriptor, scope, state, onChange, signal) {
  const loaded = await node(descriptor, document.baseURI, signal, {scope});
  if (signal.aborted || state.scope !== scope) return;
  state.generation = loaded.value.generation;
  const identity = {scope, generation: loaded.value.generation, revision: loaded.value.revision};
  const fields = form.querySelector('.guavajam-filter-fields');
  if (scope === 'collection') {
    for (const [name, label] of [['keyword', 'Tags'], ['subject', 'Subjects']]) {
      const select = form.querySelector('select[name="' + name + '"]');
      select.options[0].dataset.all = 'true';
      controls(select.parentElement, select, label, loaded.value[name], loaded.url, signal, identity);
    }
    return;
  }
  const panel = document.createElement('fieldset'); panel.className = 'guavajam-metadata-filters';
  panel.innerHTML = '<legend>Document metadata</legend><div><label for="guavajam-metadata-key">Field</label>' +
    '<select id="guavajam-metadata-key"></select></div><div class="guavajam-metadata-values">' +
    '<label for="guavajam-metadata-value">Value</label><select id="guavajam-metadata-value"></select></div>';
  const keys = panel.querySelector('#guavajam-metadata-key'), values = panel.querySelector('#guavajam-metadata-value');
  const valuePanel = values.parentElement;
  const add = document.createElement('button'); add.type = 'button'; add.textContent = 'Add filter';
  const selected = document.createElement('ul'); selected.className = 'guavajam-selected-filters';
  const selectionStatus = document.createElement('p'); selectionStatus.setAttribute('role', 'status');
  selectionStatus.hidden = true;
  panel.append(add, selectionStatus, selected); fields.append(panel);
  let options = new Map(), valueController;

  function renderSelected() {
    selected.replaceChildren();
    for (const item of state.metadata) for (const value of item.values) {
      const li = document.createElement('li'), remove = document.createElement('button');
      remove.type = 'button'; remove.textContent = 'Remove ' + item.key + ': ' + (value || '(empty string)');
      remove.addEventListener('click', () => {
        state.metadata = state.metadata.map(filter => filter === item ?
          {...item, values: item.values.filter(selected => selected !== value)} : filter).filter(item => item.values.length);
        renderSelected(); onChange();
      });
      li.append(remove); selected.append(li);
    }
  }
  function chooseKey() {
    valueController?.abort(); valueController = new AbortController();
    const current = valueController, abort = () => current.abort();
    signal.addEventListener('abort', abort, {once: true});
    current.signal.addEventListener('abort', () => signal.removeEventListener('abort', abort), {once: true});
    valuePanel.querySelector('.guavajam-facet-options')?.remove();
    values.replaceChildren(); add.disabled = true;
    const descriptor = options.get(keys.value)?.values;
    if (descriptor) controls(valuePanel, values, 'Values', descriptor, loaded.url, valueController.signal, identity);
  }
  keys.addEventListener('guavajam-options', event => {
    const current = options.get(keys.value); options = event.detail;
    if (current && !options.has(keys.value)) options.set(keys.value, current);
    chooseKey();
  });
  keys.addEventListener('change', chooseKey);
  values.addEventListener('guavajam-options', () => { add.disabled = !values.options.length; });
  add.addEventListener('click', () => {
    if (!keys.options.length || !values.options.length) return;
    const next = state.metadata.map(item => ({key: item.key, values: [...item.values]}));
    let filter = next.find(item => item.key === keys.value);
    if (!filter) { if (next.length >= 64) return; filter = {key: keys.value, values: []}; next.push(filter); }
    if (filter.values.length >= 64 || filter.values.includes(values.value)) return;
    filter.values.push(values.value);
    try { serializeMetadata(next); }
    catch (error) { selectionStatus.textContent = error.message; selectionStatus.hidden = false; return; }
    selectionStatus.hidden = true; state.metadata = next; renderSelected(); onChange();
  });
  form.addEventListener('guavajam-metadata-change', renderSelected, {signal});
  renderSelected();
  controls(keys.parentElement, keys, 'Fields', loaded.value.metadata, loaded.url, signal, identity);
}
