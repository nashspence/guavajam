/* Progressive enhancement: every work remains reachable when JavaScript/WASM fails. */
const form = document.querySelector('#guavajam-search');
if (form) {
  const work = form.dataset.scope === 'work';
  const results = document.querySelector('#guavajam-search-results');
  const fallback = document.querySelector('#guavajam-static-section') || document.querySelector('#guavajam-static-list');
  const summary = document.querySelector('#guavajam-search-status');
  const input = form.querySelector('input[name="q"]');
  const keyword = form.querySelector('select[name="keyword"]');
  const subject = form.querySelector('select[name="subject"]');
  const sort = form.querySelector('select[name="sort"]');
  const kind = form.querySelector('select[name="kind"]');
  const edition = form.querySelector('select[name="edition"]');
  const documentFilter = form.querySelector('select[name="document"]');
  const enable = document.querySelector('#guavajam-search-enable');
  const back = document.querySelector('#guavajam-search-prev');
  const next = document.querySelector('#guavajam-search-next');
  const pageLabel = document.querySelector('#guavajam-search-page');
  let worker, ready = false, loading = false, initialized = false, page = 1, sequence = 0, timer;
  const initial = new URL(document.URL).searchParams;
  if (work) {
    if (['documents', 'editions', 'passages'].includes(initial.get('kind'))) kind.value = initial.get('kind');
    if (kind.value === 'editions') edition.value = 'all';
    if (initial.has('q')) input.value = initial.get('q').slice(0, 256);
  }

  function unavailable() {
    clearTimeout(timer);
    ready = false;
    loading = false;
    worker?.terminate();
    summary.textContent = 'Search is unavailable; the full listing remains below.';
    fallback.hidden = false;
    results.hidden = true;
    results.removeAttribute('aria-busy');
    back.disabled = next.disabled = true;
    pageLabel.textContent = '';
    if (enable) {
      enable.hidden = false;
      enable.disabled = false;
      enable.removeAttribute('aria-disabled');
      enable.textContent = 'Retry search';
    }
  }

  function option(select, item) {
    const node = document.createElement('option');
    node.value = item.key;
    node.textContent = `${item.name} (${item.total})`;
    node.dataset.generated = 'true';
    select.append(node);
  }

  function workURL(value) {
    return new URL(value, new URL(form.dataset.root || '../', document.baseURI)).href;
  }

  function renderRows({rows, total, page: current, pageSize, kind: resultKind}) {
    results.replaceChildren();
    for (const row of rows) {
      const li = document.createElement('li');
      const article = document.createElement('article');
      const heading = document.createElement('h3');
      const link = document.createElement('a');
      link.href = work ? workURL(row.url) : row.path + '/index.html';
      link.textContent = work && resultKind === 'editions' ? (row.title.split('\n')[0] || row.revision.slice(0, 12)) : row.title;
      heading.append(link);
      article.append(heading);
      for (const value of [row.authors, row.abstract]) {
        if (value) {
          const paragraph = document.createElement('p');
          paragraph.textContent = value;
          article.append(paragraph);
        }
      }
      const meta = document.createElement('p');
      meta.className = 'guavajam-search-meta';
      if (work) {
        const context = [row.revision.slice(0, 12), `Date claim: ${row.published}`];
        if (resultKind === 'passages') {
          context.push(row.document_title);
          if (row.scope.startsWith('quotation:')) context.push('Quoted passage');
        }
        if (row.document_path) context.push(row.document_path + (row.anchor ? '#' + row.anchor : ''));
        meta.textContent = context.join(' · ');
      } else {
        meta.textContent = `${row.latest_published || 'Unpublished'} · ${row.edition_count} edition(s) · `;
        const source = document.createElement('a');
        source.href = row.repository;
        source.textContent = 'Source repository';
        meta.append(source);
      }
      article.append(meta);
      if (row.hit_url) {
        const match = document.createElement('a');
        match.href = work ? workURL(row.hit_url) : row.hit_url;
        match.textContent = 'View matching edition text';
        article.append(match);
      }
      li.append(article);
      results.append(li);
    }
    page = current;
    fallback.hidden = true;
    results.hidden = false;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const label = work ? ({editions: 'edition(s)', documents: 'document(s)', passages: 'section(s) / passage(s)'}[resultKind]) : 'work(s)';
    summary.textContent = `${total} ${label} found`;
    pageLabel.textContent = `Page ${current} of ${pages}`;
    back.disabled = current <= 1;
    next.disabled = current >= pages;
    results.removeAttribute('aria-busy');
  }

  function request() {
    if (!ready) return;
    results.setAttribute('aria-busy', 'true');
    back.disabled = next.disabled = true;
    worker.postMessage({action: 'search', id: ++sequence, term: input.value,
      keyword: keyword?.value, subject: subject?.value, kind: kind?.value,
      edition: edition?.value, document: documentFilter?.value, sort: sort.value, page});
  }

  function changed() { clearTimeout(timer); page = 1; request(); }
  input.addEventListener('input', () => {
    // Invalidate in-flight replies immediately, including the debounce window.
    sequence++;
    page = 1;
    back.disabled = next.disabled = true;
    clearTimeout(timer);
    timer = setTimeout(changed, 200);
  });
  for (const select of [keyword, subject, sort, edition, documentFilter].filter(Boolean)) select.addEventListener('change', changed);
  kind?.addEventListener('change', () => {
    if (kind.value === 'editions' && edition.value === 'latest') edition.value = 'all';
    changed();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    changed();
    if (!ready) initialize();
  });
  back.addEventListener('click', () => { clearTimeout(timer); if (page > 1) { page--; request(); } });
  next.addEventListener('click', () => { clearTimeout(timer); page++; request(); });

  function initialize() {
    if (ready || loading) return;
    loading = true;
    // Keep keyboard focus while loading. The loading guard rejects duplicate
    // requests; native disabled would blur the very button we need to restore.
    if (enable) enable.setAttribute('aria-disabled', 'true');
    summary.textContent = 'Loading local search…';
    try {
      worker = new Worker(new URL(form.dataset.worker || './search-worker.js', document.baseURI), {type: 'module'});
      worker.onmessage = ({data}) => {
        if (data.id !== 0 && data.id !== sequence) return;
        if (!data.ok) { unavailable(); return; }
        if (data.ready) {
          const values = new Map();
          for (const select of [keyword, subject, edition, documentFilter].filter(Boolean)) {
            values.set(select, select.value);
            for (const node of select.querySelectorAll('option[data-generated]')) node.remove();
          }
          if (work) {
            for (const item of data.editions) {
              option(edition, {...item, name: `${item.name.split('\n')[0]} · ${item.published} · ${item.key.slice(0, 12)}`});
            }
            for (const item of data.documents) option(documentFilter, {...item, name: `${item.name} · ${item.key}`});
            if (!initialized) {
              for (const [select, name] of [[edition, 'edition'], [documentFilter, 'document']]) {
                if (initial.has(name)) values.set(select, initial.get(name));
              }
            }
          } else {
            data.keywords.forEach(item => option(keyword, item));
            data.subjects.forEach(item => option(subject, item));
          }
          for (const [select, value] of values) {
            if ([...select.options].some(option => option.value === value)) select.value = value;
          }
          ready = initialized = true;
          loading = false;
          if (enable) {
            const heldFocus = document.activeElement === enable;
            enable.removeAttribute('aria-disabled');
            enable.hidden = true;
            if (heldFocus) input.focus();
          }
          request();
        } else {
          renderRows(data);
        }
      };
      worker.onerror = unavailable;
      worker.postMessage({action: 'init', id: 0, scope: work ? 'work' : 'collection',
        database: new URL(form.dataset.database || './index.sqlite', document.baseURI).href});
    } catch {
      unavailable();
    }
  }
  if (enable) enable.addEventListener('click', initialize);
  else initialize();
}
