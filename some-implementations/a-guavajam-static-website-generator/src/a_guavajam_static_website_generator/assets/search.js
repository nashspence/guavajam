/* Progressive enhancement: every work remains reachable when JavaScript/WASM fails. */
const form = document.querySelector('#guavajam-search');
if (form) (async () => {
  const {readMetadataURL, writeMetadataURL, serializeMetadata} = await import(
    new URL('search-state.js', new URL(form.dataset.worker, document.baseURI)));
  const work = form.dataset.scope !== 'collection';
  let scope = form.dataset.scope, generation, workerEpoch = 0, routingController;
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
  const toggle = document.querySelector('#guavajam-search-toggle');
  const spinner = document.querySelector('#guavajam-search-spinner');
  const progressStatus = document.querySelector('#guavajam-search-progress-status');
  let highlights = new Map();
  const listing = document.querySelector('#guavajam-search-listing');
  const directory = form.dataset.directory || '';
  // A non-JS page never exposes an unusable search form. JavaScript reveals
  // only the heading icon; a deliberate search requests its selected corpus.
  toggle.hidden = false;
  const reload = document.createElement('button');
  reload.id = 'guavajam-search-reload';
  reload.type = 'button';
  reload.textContent = 'Reload page';
  reload.hidden = true;
  reload.addEventListener('click', () => location.reload());
  disclosure.prepend(reload);
  const back = document.querySelector('#guavajam-search-prev');
  const next = document.querySelector('#guavajam-search-next');
  const pageLabel = document.querySelector('#guavajam-search-page');
  const pagination = pageLabel.closest('nav');
  const worksHeading = document.querySelector('#guavajam-works-heading');
  // The result list follows the revealed controls. Focus it after paging so
  // Tab continues into results rather than returning to the query form.
  results.tabIndex = -1;
  if (worksHeading) results.setAttribute('aria-labelledby', worksHeading.id);
  const staticStatus = summary.textContent;
  const failure = document.createElement('p');
  failure.id = 'guavajam-search-message'; failure.className = 'guavajam-search-help';
  failure.setAttribute('role', 'status'); failure.hidden = true; disclosure.append(failure);
  let activeQuery, queuedQuery;
  let worker, ready = false, loading = false, initialized = false, page = 1, sequence = 0, attempts = 0, timer;
  let executed = false;
  const initial = new URL(document.URL).searchParams;

  function settled() {
    spinner.hidden = true;
    progressStatus.textContent = '';
    failure.hidden = true;
    form.removeAttribute('aria-busy');
    if (document.activeElement === cancelButton) input.focus();
    cancelButton.hidden = true;
  }

  function unavailable() {
    const focused = document.activeElement;
    const lostFocus = document.hasFocus() && (results.contains(focused) || pagination.contains(focused) ||
      fallback.contains(focused) && focused.matches('button.guavajam-facet-type'));
    clearTimeout(timer);
    ready = false;
    loading = false;
    settled();
    worker?.terminate(); worker = undefined; workerEpoch++; routingController?.abort();
    activeQuery = queuedQuery = undefined;
    demoteStaticFacets();
    if (listing) listing.hidden = false;
    summary.textContent = staticStatus;
    failure.hidden = false;
    failure.textContent = 'Search is incomplete. The static pages remain accessible. Retry to run it again.';
    retryButton.hidden = false;
    reload.hidden = false;
    fallback.hidden = false;
    results.hidden = true;
    results.removeAttribute('aria-busy');
    back.disabled = next.disabled = true;
    pageLabel.textContent = '';
    pagination.hidden = true;
    if (lostFocus) toggle.focus();
  }

  function option(select, item) {
    const node = document.createElement('option');
    node.value = item.key;
    node.textContent = `${item.name} (${item.total})`;
    node.dataset.generated = 'true';
    select.append(node);
  }

  function facetButton(kind, key, label, field = '') {
    const facet = document.createElement('span');
    facet.className = 'guavajam-facet guavajam-' + (kind === 'keyword' ? 'tag' : kind === 'subject' ? 'subject' : 'metadata');
    facet.dataset.facet = kind; facet.dataset.value = key;
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'guavajam-facet-type';
    button.dataset.facet = kind; button.dataset.value = key;
    if (kind === 'metadata') facet.dataset.field = button.dataset.field = field;
    button.setAttribute('aria-pressed', 'false');
    button.textContent = kind === 'metadata' ? field : kind === 'keyword' ? 'Tag' : 'Subject';
    button.setAttribute('aria-label', 'Filter by ' + button.textContent + ': ' + label);
    const value = document.createElement('span'); value.className = 'guavajam-facet-value'; appendText(value, label);
    facet.append(button, document.createTextNode(' '), value);
    return facet;
  }

  function promoteStaticFacets() {
    for (const span of fallback.querySelectorAll('span.guavajam-facet[data-facet][data-value]')) {
      if (span.querySelector('button')) continue;
      const type = span.dataset.facet;
      if (!['keyword', 'subject', 'metadata'].includes(type)) continue;
      const label = span.querySelector('.guavajam-facet-value').textContent;
      span.replaceWith(facetButton(type, span.dataset.value, label, span.dataset.field));
    }
    updateFacetSelection();
  }

  function demoteStaticFacets() {
    for (const button of fallback.querySelectorAll('button.guavajam-facet-type[data-facet]')) {
      const span = document.createElement('span'); span.className = 'guavajam-facet-type';
      span.textContent = button.textContent; button.replaceWith(span);
    }
  }

  function facetSelection(event) {
    const button = event.target.closest('button.guavajam-facet-type[data-facet][data-value]');
    if (!button) return;
    if (button.dataset.facet === 'metadata') {
      const next = facetState.metadata.map(item => ({key: item.key, values: [...item.values]}));
      let filter = next.find(item => item.key === button.dataset.field);
      if (!filter) { filter = {key: button.dataset.field, values: []}; next.push(filter); }
      const value = button.dataset.value;
      filter.values = filter.values.includes(value) ? filter.values.filter(item => item !== value) : [...filter.values, value];
      const selected = next.filter(item => item.values.length);
      try { serializeMetadata(selected); }
      catch (error) { failure.textContent = error.message; failure.hidden = false; return; }
      facetState.metadata = selected;
      form.dispatchEvent(new Event('guavajam-metadata-change'));
      form.querySelector('details.guavajam-search-filters').open = true;
      updateFacetSelection(); execute(); return;
    }
    const select = button.dataset.facet === 'keyword' ? keyword : subject;
    if (!select) return;
    if (![...select.options].some(option => option.value === button.dataset.value))
      option(select, {key: button.dataset.value, name: button.closest('.guavajam-facet').querySelector('.guavajam-facet-value').textContent, total: 1});
    select.value = select.value === button.dataset.value ? '' : button.dataset.value;
    form.querySelector('details.guavajam-search-filters').open = true;
    updateFacetSelection(); execute();
  }
  fallback.addEventListener('click', facetSelection);
  results.addEventListener('click', facetSelection);

  function updateFacetSelection() {
    for (const button of document.querySelectorAll('button.guavajam-facet-type')) {
      const selected = button.dataset.facet === 'metadata' ?
        facetState.metadata.some(item => item.key === button.dataset.field && item.values.includes(button.dataset.value)) :
        (button.dataset.facet === 'keyword' ? keyword : subject)?.value === button.dataset.value;
      button.setAttribute('aria-pressed', String(selected));
    }
  }

  function documentFacets(fields) {
    const line = document.createElement('p');
    line.className = 'guavajam-facets guavajam-scrollline'; line.tabIndex = 0;
    line.setAttribute('aria-label', 'Document metadata');
    for (const [field, value] of fields) {
      const values = Array.isArray(value) ? value : [value];
      for (const literal of values) line.append(facetButton('metadata', literal, literal || '(empty string)', field));
      if (!values.length) {
        const chip = document.createElement('span'); chip.className = 'guavajam-facet';
        const name = document.createElement('span'); name.className = 'guavajam-facet-type'; name.textContent = field;
        const text = document.createElement('span'); text.className = 'guavajam-facet-value'; text.textContent = '[]';
        chip.append(name, document.createTextNode(' '), text); line.append(chip);
      }
    }
    return line;
  }

  function workURL(value) {
    return new URL(value, new URL(form.dataset.root || './', document.baseURI)).href;
  }

  // A collection's paged HTML lives under page/N.html, while SQLite URLs
  // remain relative to the collection root. Preserve root-page link shapes.
  function collectionURL(value) {
    return (form.dataset.root || '') + value;
  }

  function recordLink(href, label, iconName = 'history') {
    const a = document.createElement('a');
    a.className = 'guavajam-record-link';
    a.href = href;
    a.setAttribute('aria-label', label);
    a.title = label;
    a.append(document.querySelector('#guavajam-icon-' + iconName).content.cloneNode(true));
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

  function captionLink(href, text, highlight = true) {
    const wrapper = document.createElement('span');
    wrapper.className = 'guavajam-icon-label';
    const link = document.createElement('a');
    link.href = href; link.className = 'guavajam-icon-control';
    link.setAttribute('aria-label', text); link.title = text;
    link.append(document.querySelector('#guavajam-icon-link').content.cloneNode(true));
    const caption = document.createElement('span'); caption.className = 'guavajam-link-label';
    caption.setAttribute('aria-hidden', 'true');
    if (highlight) appendText(caption, text);
    else caption.textContent = text;
    wrapper.append(link, caption);
    return wrapper;
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
      labels: 'Tag or subject match', git: 'Publication metadata match', name: 'Version name match', message: 'Commit message match'};
    const reason = row.excerpt_context === 'passage' ? 'Passage match' :
      row.excerpt_context === 'document' ? 'Document text match' : labels[row.excerpt_field];
    if (row.hit_url) label.append(captionLink(work ? workURL(row.hit_url) : collectionURL(row.hit_url), reason, false));
    else {
      const caption = document.createElement('span'); caption.className = 'guavajam-link-label';
      caption.textContent = reason; label.append(caption);
    }
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
      const href = work ? workURL(row.url) :
        collectionURL(row.path + (row.latest_revision ? '/editions/' + row.latest_revision + '/read/index.html' : '/index.html'));
      heading.className = 'guavajam-scrollline'; heading.tabIndex = 0;
      heading.append(captionLink(href, row.title));

      article.append(heading);
      if (!work) {
        const byline = document.createElement('p');
        byline.className = 'guavajam-byline guavajam-scrollline';
        byline.tabIndex = 0;
        byline.setAttribute('aria-label', 'Work date and authors');
        byline.append(recordLink(collectionURL(row.path + '/index.html'), row.title + ' work page'),
          document.createTextNode(' '));
        if (row.latest_published) {
          const date = document.createElement('time');
          date.dateTime = row.latest_published;
          appendText(date, row.latest_published);
          byline.append(date);
        }
        if (row.authors) {
          if (row.latest_published) byline.append(document.createTextNode(' · '));
          appendText(byline, row.authors);
        }
        article.append(byline);
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
          const metadata = recordLink(workURL('editions/' + row.revision + '/metadata.html#provenance'),
            'Metadata for ' + row.title, 'info');
          meta.append(date, document.createTextNode(' · '), revision, document.createTextNode(' '), metadata);
          article.append(meta);
        }
        if (resultKind === 'documents') {
          const fields = JSON.parse(row.fields_json);
          if (fields.length) article.append(documentFacets(fields));
        }
        if (resultKind === 'documents' && row.abstract) {
          const abstract = document.createElement('p'); abstract.className = 'guavajam-abstract';
          appendText(abstract, row.abstract); article.append(abstract);
        }
        if (resultKind === 'passages') article.append(resultCitation(presentation));
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
      li.append(article);
      results.append(li);
    }
    page = current;
    if (listing) listing.hidden = false;
    fallback.hidden = true;
    results.hidden = false;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    summary.textContent = scope === 'history' ? total + (total === 1 ? ' passage' : ' passages') : total + ' total';
    pageLabel.textContent = `Page ${current} of ${pages}`;
    pagination.hidden = false;
    updateFacetSelection();
    // Hand focus from replaced content or its pager to the new results. A
    // reply must never pull readers away from the query, filters, another tab,
    // or any destination they have reached while the worker was busy.
    if (lostFocus) results.focus();
    back.disabled = current <= 1;
    next.disabled = current >= pages;
    back.removeAttribute('aria-disabled');
    next.removeAttribute('aria-disabled');
    results.removeAttribute('aria-busy');
    settled();
  }

  function pending() {
    spinner.hidden = false;
    progressStatus.textContent = 'Searching…';
    form.setAttribute('aria-busy', 'true');
    results.setAttribute('aria-busy', 'true');
    // Native disabled would blur the button that initiated a page change.
    // Keep it focused during the request; event handlers enforce this state.
    back.setAttribute('aria-disabled', 'true');
    next.setAttribute('aria-disabled', 'true');
  }


  const scopes = [...form.querySelectorAll('input[name="search-scope"]')];
  const queryLabel = form.querySelector('label[for="guavajam-query"]');
  const sortMarkup = sort.innerHTML;
  const recovery = document.createElement('div');
  recovery.className = 'guavajam-search-recovery';
  const cancelButton = document.querySelector('#guavajam-search-cancel');
  const retryButton = document.createElement('button');
  retryButton.type = 'button'; retryButton.textContent = 'Retry search'; retryButton.hidden = true;
  recovery.append(retryButton); form.append(recovery);
  const dateFrom = form.querySelector('[name="from"]'), dateTo = form.querySelector('[name="to"]');
  let navigationMethod = 'push';
  const facetState = {scope, metadata: []};
  let facetController, facetModule;
  const filtersPanel = form.querySelector('details.guavajam-search-filters');
  const facetMessage = document.createElement('p');
  facetMessage.className = 'guavajam-facet-message'; facetMessage.setAttribute('role', 'status');
  facetMessage.hidden = true; filtersPanel.append(facetMessage);

  async function historyDescriptor(signal) {
    const response = await fetch(workURL('SEARCH.json'), {signal, cache: 'no-cache', mode: 'same-origin', redirect: 'error'});
    if (!response.ok || !response.body) throw new Error('Cannot load search routing');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let text = '', size = 0;
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 16384) throw new Error('Search routing exceeds its 16-KiB budget');
        text += decoder.decode(value, {stream: true});
      }
      text += decoder.decode();
    } catch (error) { await reader.cancel(error).catch(() => {}); throw error; }
    finally { reader.releaseLock(); }
    const routing = JSON.parse(text);
    if (routing.schema !== 'guavajam.search-routing.v1') throw new Error('Unsupported search routing');
    return routing.history;
  }

  async function loadFacets() {
    facetController?.abort(); facetController = new AbortController();
    const controller = facetController, selected = scope;
    for (const item of form.querySelectorAll('.guavajam-facet-options,.guavajam-metadata-filters')) item.remove();
    facetState.scope = scope; facetState.generation = undefined;
    facetMessage.hidden = true;
    if (!filtersPanel.open || scope === 'versions') return;
    try {
      let descriptor = scope === 'history' ? (await historyDescriptor(controller.signal)).facets :
        form.dataset.facets ? JSON.parse(form.dataset.facets) : null;
      if (!descriptor) return;
      if (scope === 'history') descriptor = {...descriptor, url: workURL(descriptor.url)};
      if (controller.signal.aborted || scope !== selected) return;
      facetModule ||= import(new URL('search-facets.js', new URL(form.dataset.worker, document.baseURI)));
      const {attachFacets} = await facetModule;
      await attachFacets(form, descriptor, selected, facetState, changed, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted && scope === selected) {
        facetMessage.textContent = 'Filter options are unavailable. Reopen filters to retry.';
        facetMessage.hidden = false;
      }
    }
  }
  filtersPanel.addEventListener('toggle', () => {
    if (filtersPanel.open) loadFacets();
    else facetController?.abort();
  });


  function showStatic() {
    fallback.hidden = false; results.hidden = true;
    results.removeAttribute('aria-busy'); pagination.hidden = true;
    summary.textContent = staticStatus;
    back.disabled = next.disabled = true;
  }

  function stop({message = '', release = true} = {}) {
    clearTimeout(timer); sequence++; queuedQuery = undefined;
    if (release) {
      worker?.terminate(); worker = undefined; workerEpoch++; activeQuery = undefined;
      routingController?.abort(); ready = loading = false; generation = undefined;
    }
    settled(); showStatic();
    if (message) { failure.textContent = message; failure.hidden = false; }
  }

  function setScope(value) {
    if (!scopes.length) return;
    const selected = value === 'history' ? 'history' : 'versions';
    if (selected !== scope) {
      stop(); scope = selected; executed = false; page = 1;
      facetState.metadata = []; facetState.scope = scope; loadFacets();
      if (documentFilter) documentFilter.value = '';
      if (supportingFilter) supportingFilter.value = 'all';
      sort.innerHTML = sortMarkup;
      if (scope === 'history') {
        sort.replaceChildren();
        for (const [value, label] of [['relevance', 'Relevance'], ['publication', 'Publication order (newest)'],
          ['oldest', 'Publication order (oldest)'], ['title', 'Title (A–Z)']]) {
          const option = document.createElement('option'); option.value = value; option.textContent = label; sort.append(option);
        }
      }
    }
    for (const radio of scopes) radio.checked = radio.value === selected;
    queryLabel.textContent = selected === 'history' ? 'Search passages in complete history' : 'Search versions and commit messages';
    input.placeholder = selected === 'history' ? 'Search complete history passages' : '';
    kind.value = selected === 'history' ? 'passages' : 'editions';
    if (supportingFilter) supportingFilter.closest('div').hidden = selected !== 'history';
  }

  function state() {
    return {term: input.value, keyword: keyword?.value || '', subject: subject?.value || '',
      document: documentFilter?.value || '', supporting: supportingFilter?.value || 'all',
      from: dateFrom?.value || '', to: dateTo?.value || '', metadata: facetState.metadata, sort: sort.value, page};
  }

  function executableHistory() {
    return /[\p{L}\p{N}]/u.test(input.value) || documentFilter?.value ||
      supportingFilter?.value === 'manuscript' || facetState.metadata.some(item => item.values.length);
  }

  function writeURL(method, modeOnly = false) {
    const url = new URL(document.URL);
    for (const name of ['q', 'scope', 'keyword', 'subject', 'document', 'supporting', 'sort', 'page', 'from', 'to', 'metadata'])
      url.searchParams.delete(name);
    if (scopes.length && scope === 'history') url.searchParams.set('scope', 'history');
    if (!modeOnly) {
      const current = state(); url.searchParams.set('q', current.term);
      for (const name of ['keyword', 'subject', 'document', 'from', 'to'])
        if (current[name]) url.searchParams.set(name, current[name]);
      writeMetadataURL(url, current.metadata);
      if (current.supporting !== 'all') url.searchParams.set('supporting', current.supporting);
      url.searchParams.set('sort', current.sort);
      if (page > 1) url.searchParams.set('page', String(page));
    }
    if (modeOnly) writeMetadataURL(url, []);
    if (url.href !== location.href) history[method === 'push' ? 'pushState' : 'replaceState'](null, '', url);
  }

  function request(method = 'replace') {
    if (!ready) return;
    pending(); cancelButton.hidden = false; retryButton.hidden = true;
    writeURL(method);
    const query = {action: 'search', id: ++sequence, scope, generation, ...state()};
    if (activeQuery !== undefined) queuedQuery = query;
    else { activeQuery = query.id; worker.postMessage(query); }
  }

  async function initialize() {
    if (ready || loading) return;
    loading = true;
    failure.hidden = reload.hidden = true; retryButton.hidden = true; cancelButton.hidden = false;
    spinner.hidden = false; form.setAttribute('aria-busy', 'true');
    progressStatus.textContent = 'Loading local search…';
    const epoch = ++workerEpoch, selected = scope;
    try {
      let descriptor = {
        database: new URL(form.dataset.database, document.baseURI).href,
        databaseSize: Number(form.dataset.databaseSize), transferSize: Number(form.dataset.databaseTransferSize),
        encoding: form.dataset.databaseEncoding, sha256: form.dataset.databaseSha256,
        transferSha256: form.dataset.databaseTransferSha256, expectedGeneration: form.dataset.generation
      };
      if (scope === 'history') {
        routingController = new AbortController();
        const data = await historyDescriptor(routingController.signal);
        descriptor = {database: workURL(data.url), databaseSize: data.size, transferSize: data.transfer_size,
          encoding: data.encoding, sha256: data.sha256, transferSha256: data.transfer_sha256, expectedGeneration: data.generation};
      }
      if (workerEpoch !== epoch || scope !== selected) return;
      worker = new Worker(new URL(form.dataset.worker, document.baseURI), {type: 'module'});
      const active = worker;
      worker.onmessage = ({data}) => {
        if (worker !== active || workerEpoch !== epoch || scope !== selected || data.scope !== scope) return;
        if (data.progress) {
          if (loading || data.id === sequence && data.generation === generation)
            progressStatus.textContent = 'Searching local index…';
          return;
        }
        if (data.id !== 0) {
          if (data.id !== activeQuery || data.generation !== generation) return;
          activeQuery = undefined;
          if (queuedQuery) {
            const query = queuedQuery; queuedQuery = undefined;
            if (query.id === sequence) { activeQuery = query.id; worker.postMessage(query); return; }
          }
          if (data.id !== sequence) return;
        }
        if (!data.ok) { unavailable(); return; }
        if (data.ready) {
          generation = data.generation; ready = initialized = true; loading = false;
          request(navigationMethod);
        } else renderRows(data);
      };
      worker.onerror = () => { if (worker === active && workerEpoch === epoch) unavailable(); };
      worker.postMessage({action: 'init', id: 0, scope, ...descriptor,
        revision: ['documents', 'passages'].includes(scope) ? edition.value : undefined,
        retry: attempts++ > 0});
    } catch (error) {
      if (workerEpoch === epoch && scope === selected && error.name !== 'AbortError') unavailable();
    }
  }

  function execute({method = 'push', keepPage = false} = {}) {
    clearTimeout(timer);
    if (scope === 'history' && !executableHistory()) {
      stop({release: loading});
      failure.textContent = 'Enter search text or choose a filter, then search complete history.';
      failure.hidden = false; return;
    }
    if (!keepPage) page = 1;
    executed = true; navigationMethod = method;
    if (ready) request(method); else initialize();
  }

  function changed() {
    clearTimeout(timer); page = 1; sequence++; updateFacetSelection();
    if (loading) { if (scope === 'history') { stop(); executed = false; } return; }
    if (scope === 'history') { settled(); showStatic(); return; }
    if (executed && ready) request();
  }

  input.addEventListener('input', () => {
    sequence++; clearTimeout(timer); page = 1;
    if (loading) { if (scope === 'history') { stop(); executed = false; } return; }
    if (scope === 'history') { settled(); showStatic(); return; }
    if (ready && executed) { pending(); timer = setTimeout(changed, 200); }
  });
  for (const control of [keyword, subject, sort, documentFilter, supportingFilter, dateFrom, dateTo].filter(Boolean))
    control.addEventListener('change', changed);
  for (const radio of scopes) radio.addEventListener('change', () => {
    setScope(radio.value); writeURL('push', true);
  });
  form.addEventListener('submit', event => { event.preventDefault(); execute(); });
  cancelButton.addEventListener('click', () => {
    stop({message: 'Search canceled. Static pages remain available.'}); executed = false;
    retryButton.hidden = false;
  });
  retryButton.addEventListener('click', () => execute());
  back.addEventListener('click', () => {
    if (!ready || results.hasAttribute('aria-busy') || back.disabled) return;
    clearTimeout(timer); if (page > 1) { page--; request('push'); }
  });
  next.addEventListener('click', () => {
    if (!ready || results.hasAttribute('aria-busy') || next.disabled) return;
    clearTimeout(timer); page++; request('push');
  });
  toggle.addEventListener('click', () => {
    disclosure.hidden = !disclosure.hidden;
    toggle.setAttribute('aria-expanded', String(!disclosure.hidden));
    if (disclosure.hidden) demoteStaticFacets(); else promoteStaticFacets();
    if (disclosure.hidden && scope === 'history') { stop(); executed = false; }
  });

  function restore() {
    const parameters = new URL(document.URL).searchParams;
    stop(); executed = false;
    if (scopes.length) setScope(parameters.get('scope'));
    facetState.metadata = scope === 'versions' || scope === 'collection' ? [] : readMetadataURL(new URL(document.URL));
    if (filtersPanel.open) loadFacets();
    input.value = (parameters.get('q') || '').slice(0, 256);
    for (const [control, name] of [[keyword, 'keyword'], [subject, 'subject'], [documentFilter, 'document'],
      [supportingFilter, 'supporting'], [dateFrom, 'from'], [dateTo, 'to'], [sort, 'sort']]) {
      if (!control) continue;
      let value = parameters.get(name);
      if (value === null) {
        if (control === keyword || control === subject || control === documentFilter || control === dateFrom || control === dateTo) value = '';
        else if (control === supportingFilter) value = 'all';
        else value = control.options[0].value;
      }
      if (control === keyword || control === subject) {
        if (value && ![...control.options].some(item => item.value === value))
          option(control, {key: value, name: value, total: 1});
      }
      if (control.tagName !== 'SELECT' || [...control.options].some(item => item.value === value)) control.value = value;
      else control.selectedIndex = 0;
    }
    const requested = Number(parameters.get('page'));
    page = Number.isSafeInteger(requested) && requested > 0 ? requested : 1;
    if (parameters.has('scope') || parameters.has('q')) {
      disclosure.hidden = false; toggle.setAttribute('aria-expanded', 'true');
      promoteStaticFacets();
    }
    if (parameters.has('q') && (scope !== 'history' || executableHistory()))
      execute({method: 'replace', keepPage: true});
  }
  window.addEventListener('popstate', restore);
  if (scopes.length) setScope('versions');
  restore();
})();
