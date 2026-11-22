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
  const supportingFilter = form.querySelector('[name="supporting"]');
  const disclosure = document.querySelector('#guavajam-index-search');
  const disclosureSummary = disclosure.querySelector('.guavajam-search-summary');
  const spinner = document.querySelector('#guavajam-search-spinner');
  const querySpinner = document.querySelector('#guavajam-search-query-spinner');
  const progressStatus = document.querySelector('#guavajam-search-progress-status');
  let highlights = new Map();
  const listing = document.querySelector('#guavajam-search-listing');
  const directory = form.dataset.directory || '';
  // A non-JS page never exposes an unusable search form. JavaScript reveals
  // only the closed disclosure; opening it requests the optional database.
  disclosure.hidden = false;
  const reload = document.createElement('button');
  reload.id = 'guavajam-search-reload';
  reload.type = 'button';
  reload.textContent = 'Reload page';
  reload.hidden = true;
  reload.addEventListener('click', () => location.reload());
  disclosureSummary.after(' ', reload);
  const back = document.querySelector('#guavajam-search-prev');
  const next = document.querySelector('#guavajam-search-next');
  const pageLabel = document.querySelector('#guavajam-search-page');
  const pagination = pageLabel.closest('nav');
  const worksHeading = document.querySelector('#guavajam-works-heading');
  // This stable heading receives focus when a result or paging control goes
  // away, without adding another stop to normal sequential navigation.
  if (worksHeading) worksHeading.tabIndex = -1;
  const staticStatus = summary.textContent;
  let worker, ready = false, loading = false, initialized = false, page = 1, sequence = 0, attempts = 0, timer;
  const initial = new URL(document.URL).searchParams;
  if (work) {
    if (!directory && ['documents', 'editions', 'passages'].includes(initial.get('kind'))) kind.value = initial.get('kind');
    if (!directory && kind.value === 'editions') edition.value = 'all';
    if (initial.has('q')) input.value = initial.get('q').slice(0, 256);
  }

  function settled() {
    spinner.hidden = querySpinner.hidden = true;
    progressStatus.textContent = '';
    form.removeAttribute('aria-busy');
  }

  function unavailable() {
    const focused = document.activeElement;
    const lostFocus = document.hasFocus() && (results.contains(focused) || pagination.contains(focused) ||
      fallback.contains(focused) && focused.matches('button.guavajam-facet'));
    clearTimeout(timer);
    ready = false;
    loading = false;
    settled();
    worker?.terminate();
    demoteStaticFacets();
    if (listing) listing.hidden = false;
    summary.textContent = directory || !work ?
      'Search is unavailable. The static pages remain accessible; close and reopen Indexed Search to retry, or reload.' :
      'Search is unavailable. Retry or reload this page.';
    reload.hidden = false;
    fallback.hidden = false;
    results.hidden = true;
    results.removeAttribute('aria-busy');
    back.disabled = next.disabled = true;
    pageLabel.textContent = '';
    pagination.hidden = true;
    if (worksHeading) worksHeading.textContent = worksHeading.dataset.staticHeading || 'Works';
    if (lostFocus) disclosureSummary.focus();
    // Closing and reopening the disclosure retries the same worker, with no
    // competing Enable/Retry search buttons or separate query implementation.
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
    button.append(type, document.createTextNode(' '));
    appendText(button, label);
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
    a.append(document.querySelector('#guavajam-icon-history').content.cloneNode(true));
    return a;
  }

  function showStaticCitation(fragment) {
    let identifier;
    try { identifier = decodeURIComponent(fragment.slice(1)); } catch { return; }
    const entry = document.getElementById(identifier);
    if (!entry || !fallback.contains(entry)) return;
    sequence++;
    clearTimeout(timer);
    settled();
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

  if (directory === 'citations') window.addEventListener('hashchange', () => {
    // Back/forward can revisit a passage after another search hid its section.
    if (fallback.hidden) showStaticCitation(location.hash)?.scrollIntoView();
  });

  function appendMarked(target, marked) {
    let buffer = '', matching = false;
    const flush = () => {
      if (!buffer) return;
      if (matching) { const mark = document.createElement('mark'); mark.textContent = buffer; target.append(mark); }
      else target.append(document.createTextNode(buffer));
      buffer = '';
    };
    for (const char of marked) {
      if (char === '\uE000') { flush(); matching = true; }
      else if (char === '\uE001') { flush(); matching = false; }
      else buffer += char;
    }
    flush();
  }

  function appendText(target, value) {
    const text = String(value), marked = highlights.get(text);
    // Literal marker characters in source text must remain literal too.
    if (marked && marked.replace(/[\uE000\uE001]/g, '') === text) appendMarked(target, marked);
    else target.append(document.createTextNode(text));
  }

  function inlineParts(target, parts) {
    for (const [kind, value] of parts) {
      if (kind === 'cite' || kind === 'code') {
        const node = document.createElement(kind); appendText(node, value); target.append(node);
      } else appendText(target, value);
    }
  }

  function resultCitation(data) {
    const cited = document.querySelector('#guavajam-citation-template').content.firstElementChild.cloneNode(true);
    if (data.description.length) {
      const description = document.createElement('div');
      description.className = 'guavajam-citation-description';
      inlineParts(description, data.description); cited.prepend(description);
    }
    const identity = cited.querySelector('.guavajam-citation-source'); identity.replaceChildren();
    inlineParts(identity, data.source);
    const download = cited.querySelector('a[download]'); download.href = workURL(data.href);
    download.setAttribute('aria-label', data.label);
    return cited;
  }

  function matchedExcerpt(row) {
    // FTS5 snippet() surrounds matched source tokens with private-use markers.
    // Never insert database text as HTML; create native marked text nodes.
    // Long source tokens can push the first native FTS marker beyond 320
    // characters. Center the bounded window on that marker, never on a new
    // JavaScript word search. Keep Unicode code points intact.
    const text = String(row.excerpt), first = text.indexOf('\uE000');
    if (first < 0) return null;
    let start = Math.max(0, first - 80);
    // Slice a bounded number of UTF-16 units without splitting a surrogate pair.
    if (start && /[\uDC00-\uDFFF]/.test(text[start])) start--;
    let end = Math.min(text.length, start + 320);
    if (end < text.length && /[\uDC00-\uDFFF]/.test(text[end])) end--;
    const excerpt = text.slice(start, end);
    const container = document.createElement('div'); container.className = 'guavajam-search-excerpt';
    const label = document.createElement('p'); label.className = 'guavajam-excerpt-label';
    const labels = {title: 'Title match', authors: 'Author match', abstract: 'Description match',
      labels: 'Tag or subject match', git: 'Publication metadata match'};
    label.textContent = row.excerpt_context === 'passage' ? 'Passage match' :
      row.excerpt_context === 'document' ? 'Document text match' : labels[row.excerpt_field];
    const source = document.createElement('p'); source.className = 'guavajam-match-source';
    source.textContent = [Number.isSafeInteger(row.match_position) ? `v${row.match_position + 1}` : '',
      row.match_document || ''].filter(Boolean).join(' · ');
    const quote = document.createElement(row.excerpt_context === 'metadata' ? 'p' : 'blockquote');
    if (start) quote.append(document.createTextNode('…'));
    quote.className = 'guavajam-passage-excerpt';
    appendMarked(quote, excerpt);
    if (end < text.length) quote.append(document.createTextNode('…'));
    container.append(label);
    if (source.textContent) container.append(source);
    container.append(quote);
    return container;
  }

  function renderRows({rows, total, page: current, pageSize, kind: resultKind, highlights: matched = []}) {
    highlights = new Map(matched);
    const focused = document.activeElement;
    const lostFocus = document.hasFocus() &&
      (results.contains(focused) || fallback.contains(focused) || pagination.contains(focused));
    results.replaceChildren();
    for (const row of rows) {
      const li = document.createElement('li');
      const article = document.createElement('article');
      const heading = document.createElement('h3');
      const link = document.createElement('a');
      link.href = work ? workURL(row.url) :
        collectionURL(row.path + (row.latest_revision ? '/editions/' + row.latest_revision + '/read/index.html' : '/index.html'));
      appendText(link, row.title);
      heading.append(link);
      if (!work) heading.append(recordLink(collectionURL(row.path + '/index.html'), row.title + ' work page'));

      article.append(heading);
      if (!work) {
        const byline = document.createElement('p');
        byline.className = 'guavajam-byline guavajam-scrollline';
        byline.tabIndex = 0;
        byline.setAttribute('aria-label', 'Work authors and date');
        if (row.authors) appendText(byline, row.authors);
        if (row.latest_published) {
          if (byline.childNodes.length) byline.append(document.createTextNode(' · '));
          const date = document.createElement('time');
          date.dateTime = row.latest_published;
          appendText(date, row.latest_published);
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
          appendText(abstract, row.abstract);
          article.append(abstract);
        }
      } else {
        const presentation = resultKind === 'passages' ? JSON.parse(row.citation_json) : null;
        if (resultKind === 'editions') {
          const meta = document.createElement('p');
          meta.className = 'guavajam-meta guavajam-scrollline'; meta.tabIndex = 0;
          meta.setAttribute('aria-label', 'Version date and source');
          const date = document.createElement('time'); date.dateTime = row.published; appendText(date, row.published);
          const revision = document.createElement('code'); revision.title = row.revision; appendText(revision, row.revision.slice(0, 12));
          const metadata = document.createElement('a');
          metadata.href = workURL('editions/' + row.revision + '/metadata.html#provenance'); metadata.textContent = 'Metadata';
          meta.append(date, document.createTextNode(' · '), revision, document.createTextNode(' · '), metadata);
          article.append(meta);
        }
        if (resultKind === 'documents' && row.abstract) {
          const abstract = document.createElement('p'); abstract.className = 'guavajam-abstract';
          appendText(abstract, row.abstract); article.append(abstract);
        }
        if (resultKind === 'passages' && directory === 'citations') article.append(resultCitation(presentation));
        if (resultKind === 'editions' && row.message) {
          const [subject, ...more] = row.message.split('\n');
          const message = document.createElement('p');
          message.className = 'guavajam-message';
          appendText(message, subject);
          article.append(message);
          if (more.join('\n').trim()) {
            const details = document.createElement('details');
            details.className = 'guavajam-message-detail';
            details.open = highlights.has(more.join('\n'));
            const toggle = document.createElement('summary');
            toggle.textContent = 'Full commit message';
            const rest = document.createElement('p');
            rest.className = 'guavajam-message';
            appendText(rest, more.join('\n'));
            details.append(toggle, rest);
            article.append(details);
          }
        }
      }
      if (row.excerpt && resultKind !== 'documents') {
        const excerpt = matchedExcerpt(row);
        if (excerpt) article.append(excerpt);
      }
      if (work && resultKind === 'passages' && directory !== 'citations') {
        const action = document.createElement('a');
        action.href = workURL(row.citation_url); action.className = 'guavajam-passage-cite';
        action.setAttribute('aria-label', 'Cite committed source passage ' + row.anchor);
        action.append(document.querySelector('#guavajam-icon-quote').content.cloneNode(true));
        const actions = document.createElement('p'); actions.append(action); article.append(actions);
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
    // Hand focus from replaced content or its pager to the new results. A
    // reply must never pull readers away from the query, filters, another tab,
    // or any destination they have reached while the worker was busy.
    if (lostFocus) worksHeading?.focus();
    back.disabled = current <= 1;
    next.disabled = current >= pages;
    back.removeAttribute('aria-disabled');
    next.removeAttribute('aria-disabled');
    results.removeAttribute('aria-busy');
    settled();
  }

  function pending() {
    querySpinner.hidden = false;
    progressStatus.textContent = 'Searching…';
    form.setAttribute('aria-busy', 'true');
    results.setAttribute('aria-busy', 'true');
    // Native disabled would blur the button that initiated a page change.
    // Keep it focused during the request; event handlers enforce this state.
    back.setAttribute('aria-disabled', 'true');
    next.setAttribute('aria-disabled', 'true');
  }

  function request() {
    if (!ready) return;
    pending();
    worker.postMessage({action: 'search', id: ++sequence, term: input.value,
      keyword: keyword?.value, subject: subject?.value, kind: kind?.value,
      edition: edition?.value, document: documentFilter?.value,
      supporting: supportingFilter?.value, sort: sort.value, page});
  }

  function changed() {
    clearTimeout(timer); page = 1;
    if (ready) request();
  }
  input.addEventListener('input', () => {
    // Invalidate in-flight replies immediately, including the debounce window.
    sequence++;
    page = 1;
    if (ready) pending();
    clearTimeout(timer);
    timer = setTimeout(changed, 200);
  });
  for (const select of [keyword, subject, sort, edition, documentFilter, supportingFilter].filter(Boolean)) select.addEventListener('change', changed);
  kind?.addEventListener('change', () => {
    if (kind.value === 'editions' && edition.value === 'latest') edition.value = 'all';
    changed();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    changed();
    if (!ready) initialize();
  });
  back.addEventListener('click', () => {
    if (!ready || results.hasAttribute('aria-busy') || back.disabled) return;
    clearTimeout(timer); if (page > 1) { page--; request(); }
  });
  next.addEventListener('click', () => {
    if (!ready || results.hasAttribute('aria-busy') || next.disabled) return;
    clearTimeout(timer); page++; request();
  });

  function initialize() {
    if (ready || loading) return;
    loading = true;
    reload.hidden = true;
    spinner.hidden = false;
    form.setAttribute('aria-busy', 'true');
    if (listing) listing.hidden = false;
    // The summary remains keyboard-focusable while loading; the spinner is an
    // in-place cue without replacing or disabling the native disclosure.
    progressStatus.textContent = 'Loading local search…';
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
          promoteStaticFacets();
          request();
        } else {
          renderRows(data);
        }
      };
      worker.onerror = unavailable;
      worker.postMessage({action: 'init', id: 0, scope: work ? 'work' : 'collection',
        facets: edition?.tagName === 'SELECT' || documentFilter?.tagName === 'SELECT',
        database: new URL(form.dataset.database || './index.sqlite', document.baseURI).href,
        databaseSize: form.dataset.databaseSize === undefined ? undefined : Number(form.dataset.databaseSize),
        transferSize: form.dataset.databaseTransferSize === undefined ? undefined : Number(form.dataset.databaseTransferSize),
        encoding: form.dataset.databaseEncoding, retry: attempts++ > 0});
    } catch {
      unavailable();
    }
  }
  disclosure.addEventListener('toggle', event => {
    if (event.target !== disclosure) return;
    if (disclosure.open && !ready && !loading) initialize();
  });
  // Browsers can restore an opened disclosure through session history.
  if (disclosure.open) initialize();
}
