/* Progressive enhancement: every work remains reachable when JavaScript/WASM fails. */
const form = document.querySelector('#guavajam-search');
if (form) {
  const results = document.querySelector('#guavajam-search-results');
  const fallback = document.querySelector('#guavajam-static-section') || document.querySelector('#guavajam-static-list');
  const summary = document.querySelector('#guavajam-search-status');
  const input = form.querySelector('input[name="q"]');
  const keyword = form.querySelector('select[name="keyword"]');
  const subject = form.querySelector('select[name="subject"]');
  const sort = form.querySelector('select[name="sort"]');
  const back = document.querySelector('#guavajam-search-prev');
  const next = document.querySelector('#guavajam-search-next');
  const pageLabel = document.querySelector('#guavajam-search-page');
  let worker, ready = false, page = 1, sequence = 0, timer;

  function unavailable() {
    clearTimeout(timer);
    ready = false;
    worker?.terminate();
    summary.textContent = 'Search is unavailable; the full listing remains below.';
    fallback.hidden = false;
    results.hidden = true;
    back.disabled = next.disabled = true;
    pageLabel.textContent = '';
  }

  function option(select, item) {
    const node = document.createElement('option');
    node.value = item.key;
    node.textContent = `${item.name} (${item.total})`;
    select.append(node);
  }

  function renderRows({rows, total, page: current, pageSize}) {
    results.replaceChildren();
    for (const row of rows) {
      const li = document.createElement('li');
      const article = document.createElement('article');
      const heading = document.createElement('h3');
      const link = document.createElement('a');
      link.href = row.path + '/index.html';
      link.textContent = row.title;
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
      meta.textContent = `${row.latest_published || 'Unpublished'} · ${row.edition_count} edition(s) · `;
      const source = document.createElement('a');
      source.href = row.repository;
      source.textContent = 'Source repository';
      meta.append(source);
      article.append(meta);
      if (row.hit_url) {
        const match = document.createElement('a');
        match.href = row.hit_url;
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
    summary.textContent = `${total} work(s) found`;
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
      keyword: keyword.value, subject: subject.value, sort: sort.value, page});
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
  for (const select of [keyword, subject, sort]) select.addEventListener('change', changed);
  form.addEventListener('submit', event => { event.preventDefault(); changed(); });
  back.addEventListener('click', () => { clearTimeout(timer); if (page > 1) { page--; request(); } });
  next.addEventListener('click', () => { clearTimeout(timer); page++; request(); });

  try {
    worker = new Worker(new URL('./search-worker.js', document.baseURI), {type: 'module'});
    worker.onmessage = ({data}) => {
      if (data.id !== 0 && data.id !== sequence) return;
      if (!data.ok) { unavailable(); return; }
      if (data.ready) {
        data.keywords.forEach(item => option(keyword, item));
        data.subjects.forEach(item => option(subject, item));
        ready = true;
        request();
      } else {
        renderRows(data);
      }
    };
    worker.onerror = unavailable;
    worker.postMessage({action: 'init', id: 0});
  } catch {
    unavailable();
  }
}
