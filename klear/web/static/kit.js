// Shared by the pages that find districts and show their figures (app.js on the home and district pages, compare.js
// on the compare page): page helpers, the district search with its suggestions, and a figure with its margin.

const $ = id => document.getElementById(id);
function node(tag, text, className) { const n = document.createElement(tag); n.textContent = text; if (className) n.className = className; return n; }
// Hawaiian words for groups of people, marked so screen readers can pronounce them. Place names are left as they are.
const HAWAIIAN = /(?<![\p{L}\p{M}])(kūpuna|keiki|ʻohana)(?![\p{L}\p{M}])/giu;
function says(tag, text, className) {
  const n = node(tag, '', className);
  let at = 0;
  for (const m of text.matchAll(HAWAIIAN)) {
    const word = node('span', m[0]);
    word.lang = 'haw';
    n.append(text.slice(at, m.index), word);
    at = m.index + m[0].length;
  }
  n.append(text.slice(at));
  return n;
}
const hidden = text => node('span', text, 'visually-hidden');
// A chip that flags an unreliable estimate. Its spoken name gives the reason.
function cautionChip(why) {
  const chip = node('span', 'Use with caution', 'chip caution');
  if (why) chip.append(hidden(`. ${why}`));
  return chip;
}
// A caution chip with its reason written out beside it.
function cautionLine(why) {
  const p = node('p', '', 'profile-caution');
  p.append(cautionChip(''), ` ${why}`);
  return p;
}
// How an apportioned or modeled figure was estimated, linked to its source's section on the methods page.
function methodLine(m) {
  const line = node('p', 'How estimated: ', 'method');
  line.append(link(m.text, m.url));
  return line;
}
// Notes for districts and counties with a known caveat, such as the Maui wildfires, each with its sources.
function placeNotes(notes, className) {
  const box = node('div', '', `place-notes ${className}`);
  for (const c of notes) {
    const note = node('div', '', 'place-note'), text = node('p', '', 'place-note-text');
    text.append(says('b', `${c.title}.`), ' ', says('span', c.text));
    const list = node('ul', '', 'place-note-sources');
    for (const s of c.sources) { const li = document.createElement('li'); li.append(link(s.name, s.url)); list.append(li); }
    list.setAttribute('aria-label', c.sources.length > 1 ? `Sources for ${c.title}` : `Source for ${c.title}`);
    note.append(text, list);
    box.append(note);
  }
  return box;
}
async function api(url, options) {
  let r, data = null;
  try { r = await fetch(url, options); } catch { throw new Error('KLEAR could not be reached. Check your connection and try again.'); }
  try { data = await r.json(); } catch { /* not JSON */ }
  if (r.ok && data) return data;
  if (data && typeof data.error === 'string') throw new Error(data.error);
  throw new Error(r.ok ? 'KLEAR sent a reply it could not read. Please try again.' : `KLEAR ran into a server problem (error ${r.status}). Please try again.`);
}

function districtPath(geo) { const [chamber, id] = geo.split(':'); return `/district/${chamber}-${Number(id)}`; }
function districtName(geo) { const [chamber, id] = geo.split(':'); return `${chamber === 'house' ? 'House' : 'Senate'} District ${Number(id)}`; }
const share = s => s < 0.01 ? '<1%' : `${Math.round(s * 100)}%`;
// A place with more districts than this in one chamber shows a summary first, with its full list folded.
const MANY = 8;
// What kind of area a result is, when its name alone does not say.
const kindNotes = {
  neighborhood: 'Honolulu neighborhood board area',
  division: 'Census county division',
  island: 'Whole island',
  county: 'Whole county',
  address: 'Address as matched by the U.S. Census Bureau',
};

// One group of district buttons per matched place, area, ZIP code or address, largest first.
function matchGroups(matches) {
  const groups = [];
  for (const m of matches) {
    const rows = [m, ...(m.overlaps || [])].filter(g => /^(house|senate):/.test(g.geo || ''));
    if (!rows.length) continue;
    const county = (m.overlaps || []).find(g => /^county:/.test(g.geo || ''));
    // A district found by its legislator's name says who holds it.
    const place = m.kind === 'zcta' ? `ZIP code ${m.geo.split(':')[1]}` : m.member || m.name;
    // Name the island, or the county when the store has no islands. An island, county or address names itself.
    const where = ['island', 'county', 'address', 'district'].includes(m.kind) ? '' : m.island || (county && county.name);
    const rank = g => (g.geo.startsWith('senate:') ? 1000 : 0) - Math.round((g.share ?? 1) * 100);
    rows.sort((a, b) => rank(a) - rank(b) || a.geo.localeCompare(b.geo));
    const title = m.kind === 'district' && !m.member ? '' : [place, where].filter(Boolean).join(', ');
    groups.push({place, kind: m.kind, title, note: kindNotes[m.kind] || '', pop: m.pop2020 || 0, rows});
  }
  return groups.sort((a, b) => b.pop - a.pop);
}

const chamberNames = {house: 'House districts', senate: 'Senate districts'};

// A line that points a reader who is unsure of their district to the map.
function mapNudge(lead) {
  const line = node('p', `${lead} `, 'map-nudge'), link = node('a', 'Find it on the map');
  link.href = '#district-map';
  line.append(link, '.');
  return line;
}

// A link that moves to the map with a place's districts highlighted and its shares listed.
function mapLink(g, text) {
  const link = node('a', text), pairs = c => g.rows.filter(r => r.geo.startsWith(`${c}:`)).map(r => [r.geo, r.share ?? 1]);
  link.href = '#district-map';
  link.onclick = () => document.dispatchEvent(new CustomEvent('klear:area', {detail: {label: g.place, house: pairs('house'), senate: pairs('senate')}}));
  return link;
}

// Each place's districts in a House column and a Senate column, largest share first. A place with many districts
// leads with how many it spans and where to narrow it down, and keeps the full list in a disclosure.
function showMatches(groups, box, pick) {
  for (const g of groups) {
    const wrap = node('div', '', 'match-group'), cols = node('div', '', 'chamber-cols');
    if (g.title) wrap.append(node('p', g.title, 'match-title'));
    if (g.note) wrap.append(node('p', g.note, 'match-note'));
    const counts = {};
    for (const chamber of ['house', 'senate']) {
      const rows = g.rows.filter(r => r.geo.startsWith(`${chamber}:`));
      counts[chamber] = rows.length;
      if (!rows.length) continue;
      const col = node('div', '', 'chamber-col'), chips = node('div', '', 'chips');
      col.setAttribute('role', 'group');
      col.setAttribute('aria-label', g.title ? `${chamberNames[chamber]} for ${g.place}` : chamberNames[chamber]);
      col.append(node('p', chamberNames[chamber], `chamber-title ${chamber}`));
      for (const r of rows) {
        const b = node('button', '', `match ${chamber}`);
        b.type = 'button';
        const label = r.name.replace(' District', '');
        // The spoken name is the visible text with the rest of the sentence hidden after it.
        b.append(node('span', label));
        if (r.share != null) b.append(' ', node('span', share(r.share), 'share'), hidden(` of ${g.place} residents`));
        b.onclick = () => pick(r.geo, r.name);
        chips.append(b);
      }
      col.append(chips);
      cols.append(col);
    }
    if (counts.house > MANY || counts.senate > MANY) {
      const spans = [['house', 'House'], ['senate', 'Senate']].filter(([c]) => counts[c]).map(([c, word]) => `${counts[c]} ${word}`);
      const summary = node('p', `${g.place} spans ${spans.join(' and ')} districts. Search for a neighborhood, town or ZIP code in it, or `, 'match-summary');
      summary.append(mapLink(g, 'see its districts on the map'), '.');
      const all = node('details', '', 'match-all'), label = node('summary', `All ${g.rows.length} districts`);
      all.append(label, cols);
      wrap.append(summary, all);
    } else {
      wrap.append(cols);
    }
    box.append(wrap);
  }
}

// Suggestions under a search box as an ARIA 1.2 combobox. The list follows typing, the arrow keys move through it
// while focus stays in the box, Enter or a click picks one, and Escape closes it. Suggestions come from names in the
// store only, so nothing typed goes to another service. Returns a function that closes the list.
const SUGGEST_MIN = 2, SUGGEST_WAIT = 150;
function suggestBox(input, list, live, onPick) {
  // stale: the text changed since the list was filled, so the keys must not act on its old options
  let items = [], active = -1, seq = 0, timer = 0, stale = false;
  const close = () => {
    seq++;
    clearTimeout(timer);
    items = [];
    active = -1;
    list.hidden = true;
    list.replaceChildren();
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  };
  const mark = i => {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute('aria-selected', String(j === i)));
    input.setAttribute('aria-activedescendant', list.children[i].id);
    list.children[i].scrollIntoView({block: 'nearest'});
  };
  const pick = i => {
    const s = items[i];
    input.value = s.label;
    close();
    live.textContent = '';
    onPick(s.query);
  };
  const show = found => {
    items = found;
    active = -1;
    stale = false;
    list.replaceChildren(...found.map((s, i) => {
      const li = node('li', '');
      li.id = `${list.id}-${i}`;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', 'false');
      li.append(node('span', s.label, 'suggestion-label'), ' ', node('span', s.detail, 'suggestion-detail'));
      // Keep focus in the box, so a tap or click picks without the box losing focus and closing the list first.
      li.addEventListener('pointerdown', e => e.preventDefault());
      li.addEventListener('click', () => pick(i));
      return li;
    }));
    list.hidden = !found.length;
    input.setAttribute('aria-expanded', String(Boolean(found.length)));
    input.removeAttribute('aria-activedescendant');
    live.textContent = found.length ? `${found.length} suggestion${found.length === 1 ? '' : 's'}. Use the up and down arrows to choose.` : '';
  };
  const load = async () => {
    const text = input.value.trim(), mine = ++seq;
    if (text.length < SUGGEST_MIN) { close(); live.textContent = ''; return; }
    try {
      const r = await fetch('/api/suggest?q=' + encodeURIComponent(text));
      const data = r.ok ? await r.json() : null;
      if (mine === seq && document.activeElement === input) show((data && data.suggestions) || []);
    } catch {
      if (mine === seq) close();
    }
  };
  input.addEventListener('input', () => {
    stale = true;
    active = -1;
    [...list.children].forEach(li => li.setAttribute('aria-selected', 'false'));
    input.removeAttribute('aria-activedescendant');
    clearTimeout(timer);
    timer = setTimeout(load, SUGGEST_WAIT);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      // A closed or outdated list loads the suggestions for the text as it is now, without waiting.
      if (list.hidden || stale) { clearTimeout(timer); load(); return; }
      const n = items.length, down = e.key === 'ArrowDown';
      mark(down ? (active + 1) % n : (active <= 0 ? n - 1 : active - 1));
    } else if (e.key === 'Enter' && !list.hidden && !stale && active >= 0) {
      e.preventDefault();
      pick(active);
    } else if (e.key === 'Escape' && !list.hidden) {
      e.preventDefault();
      close();
    }
  });
  input.addEventListener('blur', close);
  return close;
}

// Wire a district search form: matching districts appear as buttons under it, and a single match is picked at once.
// A picked suggestion searches at once for exactly what it names. onMatches, when given, hears the places found, so
// the map can show them. Returns a function that clears the form's results and drops any search still loading.
function lookupForm(form, input, status, box, pick, onMatches) {
  let seq = 0;
  const search = async query => {
    const mine = ++seq, button = form.querySelector('button[type=submit]');
    button.disabled = true;
    box.replaceChildren();
    status.textContent = 'Finding matching districts…';
    try {
      const data = await api('/api/resolve?q=' + encodeURIComponent(query));
      if (mine !== seq) return;
      const groups = matchGroups(data.matches || []);
      const found = new Map(groups.flatMap(g => g.rows.map(r => [r.geo, r.name])));
      if (found.size === 1) { const [[geo, name]] = found; status.textContent = ''; pick(geo, name); return; }
      if (!found.size) {
        status.textContent = data.note || 'No matching districts. Try House 22, a town or neighborhood, a ZIP code, or a street address.';
        if ($('find')) box.append(mapNudge('Not sure which district is yours?'));
        return;
      }
      if (groups.every(g => g.kind === 'address')) status.textContent = 'Choose the House or Senate district.';
      else status.textContent = groups.some(g => g.title) ? 'Choose a district. Each percent is its share of the area’s residents.' : 'Choose a district:';
      showMatches(groups, box, pick);
      if (onMatches) onMatches(groups);
    } catch (err) {
      if (mine === seq) status.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  };
  const closeList = suggestBox(input, $(`${input.id}-list`), $(`${input.id}-count`), search);
  form.onsubmit = e => { e.preventDefault(); closeList(); search(input.value); };
  return () => { seq++; closeList(); box.replaceChildren(); status.textContent = ''; };
}

// A place's figure in its own cell of a profile table: the estimate, then its margin of error or range under it.
// Each cell names its place for phones, where the cells stack and the column headings are out of sight.
function figureCell(f, place, home) {
  const td = node('td', '', `profile-figure${home ? ' home' : ''}`);
  td.setAttribute('role', 'cell');
  const where = node('span', place, 'profile-place');
  where.setAttribute('aria-hidden', 'true');
  td.append(where);
  if (!f) { td.append(node('span', 'No figure', 'none')); return td; }
  if (f.missing) { td.classList.add('missing'); td.append(node('span', 'No figure', 'none'), node('span', f.missing, 'moe')); return td; }
  // The "about" of an estimated value goes on its own line, and a range takes two, low then high, so the column stays
  // as narrow as one that holds a count and its margin of error.
  const [, about, number] = /^(about )?(.*)$/.exec(f.plain || f.value);
  if (about) td.append(node('span', 'about', 'moe'));
  td.append(node('span', number, 'num'));
  const range = /^(range .+?) (to .+)$/.exec(f.spread || '');
  if (range) td.append(node('span', range[1], 'moe'), node('span', range[2], 'moe'));
  else if (f.spread) td.append(node('span', f.spread, 'moe'));
  if (f.aside) td.append(node('span', f.aside, 'profile-aside'));
  if (!home && f.caution) td.append(cautionChip(f.caution));
  return td;
}

// Line icons for the share controls, on a 24-unit grid in the text color. Screen readers skip them.
const ICONS = {
  print: 'M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2M7 14h10v7H7z',
  link: 'M9 7H7a5 5 0 0 0 0 10h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  compare: 'M5 20V10M12 20V4M19 20v-7',
  info: 'M12 11v6M12 7.5v.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
};
function icon(name) {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'), path = document.createElementNS(ns, 'path');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', 'icon');
  path.setAttribute('d', ICONS[name]);
  svg.append(path);
  return svg;
}

// A link that opens in a new tab and says so, or plain text when the address is not a web page.
// A path such as /methods.html is a page on this site, so it has no arrow for leaving the site.
function link(text, href) {
  if (!href) return node('span', text);
  try {
    const url = new URL(href, location.href);
    if (['http:', 'https:'].includes(url.protocol)) {
      const a = node('a', text, url.origin === location.origin ? '' : 'ext');
      a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.append(hidden(' (opens in a new tab)'));
      return a;
    }
  } catch {}
  return node('span', text);
}

// A district's figures in six groups, in reading order, each gathering the store's sections by title: the profile's
// disclosures and the compare page's table use the same groups. The programs to explore lead their group, and the
// reapportionment plan closes the last.
const PROFILE_GROUPS = [
  ['people', 'People', ['People']],
  ['income-work', 'Income and work', ['Income', 'Work and education']],
  ['programs', 'Programs and help', ['@programs', 'Programs in use', 'Who may qualify', 'Help sites']],
  ['housing', 'Housing', ['Housing']],
  ['health', 'Health', ['Health and access', 'Modeled needs']],
  ['reference', 'Reference', ['County-level figures', '@plan']],
];
