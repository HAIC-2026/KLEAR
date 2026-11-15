// The sources page: the facts line, the source table and the DBEDT cross-check, read from the data store. Without a
// store the page keeps its fallback line, which then says why the list is missing.
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
}
loadSources();
