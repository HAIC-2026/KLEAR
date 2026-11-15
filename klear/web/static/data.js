// The data and methods page: the source table and facts line from the data store, and the section an old address
// named. The checks are filled in by checks.js. Its own scope, so its helpers do not meet checks.js's.
(() => {
  const $ = id => document.getElementById(id);
  function node(tag, text, className) { const n = document.createElement(tag); n.textContent = text; if (className) n.className = className; return n; }
  // A source's page, opened in a new tab. A link that is not a web address stays plain text.
  function link(text, href) {
    try {
      const url = new URL(href, location.href);
      if (['http:', 'https:'].includes(url.protocol)) {
        const a = node('a', text, url.origin === location.origin ? '' : 'ext');
        a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.append(node('span', ' (opens in a new tab)', 'visually-hidden'));
        return a;
      }
    } catch { /* not a web address */ }
    return node('span', text);
  }

  // An old address such as /methods.html opens here as /data?section=methods: move to that section, unless the link
  // also named a part of it (#cv), which the browser has already scrolled to.
  const section = new URLSearchParams(location.search).get('section');
  if (section && !location.hash && document.getElementById(section)) {
    try { history.replaceState(null, '', `/data#${section}`); } catch { /* history unavailable */ }
    document.getElementById(section).scrollIntoView();
  }

  async function loadSources() {
    let r, data = null;
    try { r = await fetch('/api/about'); } catch {
      $('sources-missing').textContent = 'KLEAR could not be reached. Check your connection and try again.';
      return;
    }
    try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok || !data) {
      // Say why there is no source list, for example that no data store has been built.
      $('sources-missing').textContent = data && typeof data.error === 'string' ? data.error : `KLEAR ran into a server problem (error ${r.status}).`;
      return;
    }
    if (data.facts && data.facts.length) {
      $('facts').textContent = data.facts.join('; ') + '.';
      $('facts').hidden = false;
    }
    if (data.sources && data.sources.length) {
      const body = $('source-table').tBodies[0];
      for (const s of data.sources) {
        const row = document.createElement('tr'), name = document.createElement('td');
        name.append(link(s.name, s.url));
        row.append(name, node('td', s.publisher), node('td', s.years), node('td', s.geography || ''));
        body.append(row);
      }
      $('source-wrap').hidden = false;
      $('sources-missing').hidden = true;
    }
    if (data.crosscheck) { $('crosscheck').textContent = data.crosscheck; $('crosscheck').hidden = false; }
    // The table grew the page above the part a link named, such as a glossary term, so move back to it.
    const named = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (named && named.compareDocumentPosition($('source-wrap')) & Node.DOCUMENT_POSITION_PRECEDING) named.scrollIntoView();
  }
  loadSources();

  // The section list beside the page marks the section being read.
  const links = [...document.querySelectorAll('.data-nav a')];
  const watch = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      for (const a of links) {
        if (a.getAttribute('href') === `#${e.target.id}`) a.setAttribute('aria-current', 'location'); else a.removeAttribute('aria-current');
      }
    }
  }, {rootMargin: '-10% 0px -60% 0px'});
  for (const a of links) { const s = document.querySelector(a.getAttribute('href')); if (s) watch.observe(s); }
  if (links.length && !location.hash) links[0].setAttribute('aria-current', 'location');
})();
