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
  const kind = form.querySelector('[name="kind"]');
  const edition = form.querySelector('[name="edition"]');
  const documentFilter = form.querySelector('[name="document"]');
  const enable = document.querySelector('#guavajam-search-enable');
  const spinner = document.querySelector('#guavajam-search-spinner');
  const listing = document.querySelector('#guavajam-search-listing');
  const directory = form.dataset.directory || '';
  if (directory) document.querySelector('#guavajam-directory-enhancement').hidden = false;
  const reload = document.createElement('button');
  reload.id = 'guavajam-search-reload';
  reload.type = 'button';
  reload.textContent = 'Reload page';
  reload.hidden = true;
  reload.addEventListener('click', () => location.reload());
  (enable || summary).after(' ', reload);
  const back = document.querySelector('#guavajam-search-prev');
  const next = document.querySelector('#guavajam-search-next');
  const pageLabel = document.querySelector('#guavajam-search-page');
  const pagination = pageLabel.closest('nav');
  const worksHeading = document.querySelector('#guavajam-works-heading');
  const staticStatus = summary.textContent;
  let worker, ready = false, loading = false, initialized = false, page = 1, sequence = 0, attempts = 0, timer;
  const initial = new URL(document.URL).searchParams;
  if (work) {
    if (!directory && ['documents', 'editions', 'passages'].includes(initial.get('kind'))) kind.value = initial.get('kind');
    if (!directory && kind.value === 'editions') edition.value = 'all';
    if (initial.has('q')) input.value = initial.get('q').slice(0, 256);
  }

  function unavailable() {
    clearTimeout(timer);
    ready = false;
    loading = false;
    if (spinner) spinner.hidden = true;
    worker?.terminate();
    demoteStaticFacets();
    if (listing) listing.hidden = false;
    summary.textContent = directory || !work ?
      'Search is unavailable. The static pages remain accessible; retry search or reload.' :
      'Search is unavailable. Retry or reload this page.';
    reload.hidden = false;
    fallback.hidden = false;
    results.hidden = true;
    results.removeAttribute('aria-busy');
    back.disabled = next.disabled = true;
    pageLabel.textContent = '';
    pagination.hidden = true;
    if (worksHeading) worksHeading.textContent = worksHeading.dataset.staticHeading || 'Works';
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

  function facetButton(kind, key, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'guavajam-facet guavajam-' + (kind === 'keyword' ? 'tag' : 'subject');
    button.dataset.facet = kind;
    button.setAttribute('aria-pressed', 'false');
    button.dataset.value = key;
    const type = document.createElement('span');
    type.className = 'guavajam-facet-type';
    type.textContent = kind === 'keyword' ? 'Tag' : 'Subject';
    button.append(type, document.createTextNode(' ' + label));
    button.setAttribute('aria-label', `Filter by ${kind === 'keyword' ? 'tag' : 'subject'} ${label}`);
    return button;
  }

  function promoteStaticFacets() {
    if (work) return;
    for (const span of fallback.querySelectorAll('span.guavajam-facet[data-facet][data-value]')) {
      const type = span.dataset.facet;
      if (type !== 'keyword' && type !== 'subject') continue;
      const label = span.textContent.trim().replace(/^(?:Tag|Subject)\s*/, '');
      span.replaceWith(facetButton(type, span.dataset.value, label));
    }
  }

  function demoteStaticFacets() {
    if (work) return;
    for (const button of fallback.querySelectorAll('button.guavajam-facet[data-facet]')) {
      const span = document.createElement('span');
      span.className = button.className;
      span.dataset.facet = button.dataset.facet;
      span.dataset.value = button.dataset.value;
      span.append(...button.childNodes);
      button.replaceWith(span);
    }
  }

  function facetSelection(event) {
    const button = event.target.closest('button.guavajam-facet[data-facet][data-value]');
    if (!ready || !button) return;
    const select = button.dataset.facet === 'keyword' ? keyword : subject;
    if (!select || ![...select.options].some(option => option.value === button.dataset.value)) return;
    select.value = select.value === button.dataset.value ? '' : button.dataset.value;
    form.querySelector('details.guavajam-search-filters').open = true;
    changed();
  }
  fallback.addEventListener('click', facetSelection);
  results.addEventListener('click', facetSelection);

  function workURL(value) {
    return new URL(value, new URL(form.dataset.root || '../', document.baseURI)).href;
  }

  // A collection's paged HTML lives under page/N.html, while SQLite URLs
  // remain relative to the collection root. Preserve root-page link shapes.
  function collectionURL(value) {
    return (form.dataset.root || '') + value;
  }

  function recordLink(href, label) {
    const a = document.createElement('a');
    a.className = 'guavajam-record-link';
    a.href = href;
    a.setAttribute('aria-label', label);
    a.title = label;
    // Fixed local icon markup. Untrusted search text is always textContent.
    a.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 3.5h9l3 3V20.5H6z"/><path d="M15 3.5v4h4M9 12h6M9 15h6"/></svg>';
    return a;
  }

  function showStaticCitation(fragment) {
    let identifier;
    try { identifier = decodeURIComponent(fragment.slice(1)); } catch { return; }
    const entry = document.getElementById(identifier);
    if (!entry || !fallback.contains(entry)) return;
    sequence++;
    clearTimeout(timer);
    fallback.hidden = false;
    results.hidden = true;
    results.removeAttribute('aria-busy');
    pagination.hidden = true;
    back.disabled = next.disabled = true;
    summary.textContent = staticStatus;
    if (worksHeading) worksHeading.textContent = worksHeading.dataset.staticHeading || 'Passages';
    entry.focus({preventScroll: true});
    return entry;
  }

  function openStaticCitation(event) {
    // A hit on this very static page is only a fragment navigation. Reveal its
    // native entry before the browser follows the href; no reload is implied.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = new URL(event.currentTarget.href);
    const current = new URL(document.URL);
    if (target.origin !== current.origin || target.pathname !== current.pathname || target.search !== current.search) return;
    showStaticCitation(target.hash);
  }

  if (directory === 'citations') window.addEventListener('hashchange', () => {
    // Back/forward can revisit a passage after another search hid its section.
    if (fallback.hidden) showStaticCitation(location.hash)?.scrollIntoView();
  });

  function renderRows({rows, total, page: current, pageSize, kind: resultKind}) {
    results.replaceChildren();
    for (const row of rows) {
      const li = document.createElement('li');
      const article = document.createElement('article');
      const heading = document.createElement('h3');
      const link = document.createElement('a');
      link.href = work ? workURL(directory === 'citations' ? row.citation_url : row.url) :
        collectionURL(row.path + (row.latest_revision ? '/editions/' + row.latest_revision + '/read/index.html' : '/index.html'));
      link.textContent = row.title;
      if (directory === 'citations') link.addEventListener('click', openStaticCitation);
      heading.append(link);
      if (!work) heading.append(recordLink(collectionURL(row.path + '/index.html'), row.title + ' work page'));

      article.append(heading);
      if (!work) {
        const byline = document.createElement('p');
        byline.className = 'guavajam-byline guavajam-scrollline';
        byline.tabIndex = 0;
        byline.setAttribute('aria-label', 'Work authors and date');
        if (row.authors) byline.append(document.createTextNode(row.authors));
        if (row.latest_published) {
          if (byline.childNodes.length) byline.append(document.createTextNode(' · '));
          const date = document.createElement('time');
          date.dateTime = row.latest_published;
          date.textContent = row.latest_published;
          byline.append(date);
        }
        if (byline.childNodes.length) article.append(byline);
        const tags = JSON.parse(row.keywords_json || '[]');
        const subjects = JSON.parse(row.subjects_json || '[]');
        if (tags.length || subjects.length) {
          const facets = document.createElement('p');
          facets.className = 'guavajam-facets guavajam-scrollline';
          facets.setAttribute('aria-label', 'Subjects and tags');
          facets.tabIndex = 0;
          for (const tag of tags) facets.append(facetButton('keyword', tag, tag));
          for (const item of subjects) facets.append(facetButton('subject', item.key, item.label));
          article.append(facets);
        }
        if (row.abstract) {
          const abstract = document.createElement('p');
          abstract.className = 'guavajam-abstract';
          abstract.textContent = row.abstract;
          article.append(abstract);
        }
      } else {
        const meta = document.createElement('p');
        meta.className = 'guavajam-search-meta';
        const context = [row.published];
        if (resultKind !== 'editions') context.unshift(row.revision.slice(0, 12));
        if (resultKind === 'passages') {
          context.push(row.document_title);
          if (row.scope.startsWith('quotation:')) context.push('Quoted passage');
        }
        if (row.document_path) context.push(row.document_path + (row.anchor ? '#' + row.anchor : ''));
        if (resultKind === 'editions') context.push(row.revision.slice(0, 12));
        meta.textContent = context.join(' · ');
        if (resultKind === 'editions') {
          const verify = document.createElement('a');
          verify.href = workURL('editions/' + row.revision + '/verify.html');
          verify.textContent = 'Verify';
          meta.append(document.createTextNode(' · '), verify);
        }
        article.append(meta);
        if (resultKind === 'passages') {
          const action = document.createElement('a');
          action.href = workURL(directory === 'citations' ? row.url : row.citation_url);
          action.textContent = directory === 'citations' ? 'Read this passage' : 'Cite';
          if (directory !== 'citations') action.setAttribute('aria-label', 'Cite committed source passage ' + row.anchor);
          article.append(action);
        }
        if (resultKind === 'editions' && row.message) {
          const [subject, ...more] = row.message.split('\n');
          const message = document.createElement('p');
          message.className = 'guavajam-message';
          message.textContent = subject;
          article.append(message);
          if (more.join('\n').trim()) {
            const details = document.createElement('details');
            details.className = 'guavajam-message-detail';
            const toggle = document.createElement('summary');
            toggle.textContent = 'Full commit message';
            const rest = document.createElement('p');
            rest.className = 'guavajam-message';
            rest.textContent = more.join('\n');
            details.append(toggle, rest);
            article.append(details);
          }
        }
      }
      if (row.hit_url) {
        const match = document.createElement('a');
        match.href = work ? workURL(row.hit_url) : collectionURL(row.hit_url);
        match.textContent = 'View matching edition text';
        article.append(match);
      }
      li.append(article);
      results.append(li);
    }
    page = current;
    if (listing) listing.hidden = false;
    fallback.hidden = true;
    results.hidden = false;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const label = work ? ({editions: 'edition(s)', documents: 'document(s)', passages: 'section(s) / passage(s)'}[resultKind]) : 'work(s)';
    summary.textContent = `${total} ${label} found`;
    pageLabel.textContent = `Page ${current} of ${pages}`;
    pagination.hidden = false;
    if (worksHeading) worksHeading.textContent = 'Search results';
    for (const button of results.querySelectorAll('button.guavajam-facet')) {
      const selected = button.dataset.facet === 'keyword' ? keyword : subject;
      button.setAttribute('aria-pressed', String(selected?.value === button.dataset.value));
    }
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

  function changed() {
    clearTimeout(timer); page = 1;
    if (ready) request();
  }
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
    reload.hidden = true;
    if (spinner) spinner.hidden = false;
    if (listing) listing.hidden = false;
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
          for (const select of [keyword, subject, edition, documentFilter]
            .filter(node => node?.tagName === 'SELECT')) {
            values.set(select, select.value);
            for (const node of select.querySelectorAll('option[data-generated]')) node.remove();
          }
          if (work) {
            if (edition?.tagName === 'SELECT') for (const item of data.editions) {
              option(edition, {...item, name: `${item.name.split('\n')[0]} · ${item.published} · ${item.key.slice(0, 12)}`});
            }
            if (documentFilter?.tagName === 'SELECT') for (const item of data.documents)
              option(documentFilter, {...item, name: `${item.name} · ${item.key}`});
            if (!initialized) {
              for (const [select, name] of [[edition, 'edition'], [documentFilter, 'document']]) {
                if (select?.tagName === 'SELECT' && initial.has(name)) values.set(select, initial.get(name));
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
          if (spinner) spinner.hidden = true;
          promoteStaticFacets();
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
        database: new URL(form.dataset.database || './index.sqlite', document.baseURI).href,
        databaseSize: form.dataset.databaseSize === undefined ? undefined : Number(form.dataset.databaseSize),
        transferSize: form.dataset.databaseTransferSize === undefined ? undefined : Number(form.dataset.databaseTransferSize),
        encoding: form.dataset.databaseEncoding, retry: attempts++ > 0});
    } catch {
      unavailable();
    }
  }
  if (enable) enable.addEventListener('click', initialize);
  else initialize();
}
