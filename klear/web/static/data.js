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

  // Whether this site's AI assistant is on right now, so the privacy section says where questions go today.
  async function loadMode() {
    const line = $('privacy-mode');
    if (!line) return;
    let c = null;
    try { c = ((await (await fetch('/api/ready')).json()) || {}).capabilities; } catch { return; }
    if (!c || typeof c !== 'object') return;
    line.textContent = !c.assistant
      ? 'Right now this site answers with its basic lookup only: no question is sent to an AI provider.'
      : c.assistant_capped
        ? 'Right now the AI assistant has reached today’s spending limit, so the basic lookup answers and no question is sent to an AI provider until the day ends (HST).'
        : 'Right now the AI assistant is on, so questions without a prepared answer go to Anthropic.';
    line.hidden = false;
  }
  loadMode();

  // The section list beside the page marks the section being read: the last one whose top has passed a line a third of
  // the way down the window, worked out from where the sections are now, so a section that grows as its results load
  // (the checks) keeps its mark. At the foot of the page, the last section.
  const links = [...document.querySelectorAll('.data-nav a')];
  const sections = links.map(a => document.querySelector(a.getAttribute('href')));
  let frame = 0;
  function markCurrent() {
    frame = 0;
    const line = innerHeight / 3, page = document.scrollingElement;
    let at = 0;
    sections.forEach((s, i) => { if (s && s.getBoundingClientRect().top <= line) at = i; });
    if (page.scrollTop > 0 && page.scrollTop + page.clientHeight >= page.scrollHeight - 1) at = sections.length - 1;
    links.forEach((a, i) => { if (i === at) a.setAttribute('aria-current', 'location'); else a.removeAttribute('aria-current'); });
  }
  const schedule = () => { if (!frame) frame = requestAnimationFrame(markCurrent); };
  if (links.length) {
    addEventListener('scroll', schedule, {passive: true});
    addEventListener('resize', schedule);
    addEventListener('hashchange', schedule);
    const grow = new ResizeObserver(schedule);
    for (const s of sections) if (s) grow.observe(s);
    schedule();
  }
})();
