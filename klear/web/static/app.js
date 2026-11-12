const $ = id => document.getElementById(id);
let district = null, asked = null, busy = false, queued = null;
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const uncertaintyLabels = {ok: '90% margin of error shown above', controlled: 'Fixed to official totals; no sampling margin of error', range: 'Estimated range shown above', open_ended: 'Open-ended published value', not_computable: 'Margin of error unavailable; treat as uncertain', not_applicable: 'No margin of error applies', unavailable: 'Uncertainty information unavailable'};
// Starter questions, with the short label each shows once it moves into the chat.
const examples = [['Give me a socioeconomic profile of this district.', 'Profile'], ['How many kūpuna live in this district?', 'Kūpuna'], ['What does the data say about housing needs in this district?', 'Housing'], ['How does poverty here compare with the state?', 'Poverty vs. state'], ['What support programs could residents of this district explore?', 'Programs']];
const refusalLabels = {decline_partisan: 'Partisan request declined', decline_individual: 'Personal request declined', not_available: 'Data not available', out_of_scope: 'Outside KLEAR’s scope'};
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
async function api(url, options) {
  let r, data = null;
  try { r = await fetch(url, options); } catch { throw new Error('KLEAR could not be reached. Check your connection and try again.'); }
  try { data = await r.json(); } catch { /* not JSON */ }
  if (r.ok && data) return data;
  if (data && typeof data.error === 'string') throw new Error(data.error);
  throw new Error(r.ok ? 'KLEAR sent a reply it could not read. Please try again.' : `KLEAR ran into a server problem (error ${r.status}). Please try again.`);
}

// Bring the chat card into view, or its question box when the card is taller than the screen.
function showChat() {
  const card = $('chat-card');
  (card.offsetHeight <= window.innerHeight ? card : $('question-form')).scrollIntoView({block: 'center'});
}

// Grow the question box with its text, up to a limit, then scroll inside it.
const QUESTION_MAX = 192;
function fitQuestion() {
  const q = $('question');
  q.style.height = 'auto';
  const full = q.scrollHeight + q.offsetHeight - q.clientHeight;
  q.style.height = `${Math.min(full, QUESTION_MAX)}px`;
  q.style.overflowY = full > QUESTION_MAX ? 'auto' : 'hidden';
}
$('question').addEventListener('input', fitQuestion);
window.addEventListener('resize', fitQuestion);
document.fonts.ready.then(fitQuestion);

function logScrolls() { return getComputedStyle($('messages')).overflowY === 'auto'; }
// Whether a node shows on screen, and inside the log's own scroll area when the log scrolls.
function onScreen(n) {
  const r = n.getBoundingClientRect(), log = $('messages');
  const box = logScrolls() && log.contains(n) ? log.getBoundingClientRect() : {top: 0, bottom: window.innerHeight};
  return r.bottom > Math.max(0, box.top) && r.top < Math.min(window.innerHeight, box.bottom);
}

// Keep the field the reader is typing in where it is while the page grows above it.
function holdField() {
  const f = document.activeElement;
  if (!f || !f.matches('input, select, textarea') || !onScreen(f)) return null;
  const before = f.getBoundingClientRect().top;
  const undo = () => { const shift = f.getBoundingClientRect().top - before; if (shift) window.scrollBy({top: shift, behavior: 'instant'}); };
  undo.field = true;
  return undo;
}

// When the log grows the page, return a function that undoes the shift: for what sits below the chat when
// the change is above the screen, else for a field the reader is typing in.
function holdPage(mark) {
  if (logScrolls()) return null;
  if (mark.getBoundingClientRect().bottom > 0) return holdField();
  const section = $('chat'), before = section.getBoundingClientRect().bottom;
  return () => { const shift = section.getBoundingClientRect().bottom - before; if (shift) window.scrollBy({top: shift, behavior: 'instant'}); };
}

// Scroll the conversation so a message sits at the top, inside the log when it scrolls on its own.
function reveal(message) {
  if (logScrolls()) $('messages').scrollTo({top: message.offsetTop - 16, behavior: reduceMotion.matches ? 'auto' : 'smooth'});
  else message.scrollIntoView({block: 'start'});
}

// Each district has its own address, like /district/house-22, so a link, Back, Forward and refresh all keep it.
const BASE_TITLE = document.title;
const SEATS = {house: 51, senate: 25};
function districtPath(geo) { const [chamber, id] = geo.split(':'); return `/district/${chamber}-${Number(id)}`; }
function districtName(geo) { const [chamber, id] = geo.split(':'); return `${chamber === 'house' ? 'House' : 'Senate'} District ${Number(id)}`; }
// Change the address bar, adding a history entry unless replacing. A page the browser will not let change its
// address, such as one opened from a file, keeps working without it.
function setAddress(path, replace) {
  try {
    if (replace) history.replaceState(null, '', path); else history.pushState(null, '', path);
  } catch { /* history unavailable */ }
}
// The district the address names, or null for any other page or a district that does not exist.
function routeGeo() {
  const m = location.pathname.match(/^\/district\/(house|senate)-(\d{1,2})$/);
  return m && +m[2] >= 1 && +m[2] <= SEATS[m[1]] ? `${m[1]}:${m[2].padStart(3, '0')}` : null;
}

function setLookup(open) {
  $('lookup').hidden = !open;
  $('change').setAttribute('aria-expanded', String(open));
  $('change').textContent = open ? 'Cancel' : district ? 'Change district' : 'Choose a district';
  if (!open) resetLookup();
}

function needDistrict() {
  setLookup(true);
  $('lookup-status').textContent = 'Pick a district first. Your question will wait in the box below.';
  const bar = $('district-bar');
  bar.classList.remove('nudge'); void bar.offsetWidth; bar.classList.add('nudge');
  $('place').focus();
}

// While an answer loads, the ask and suggestion buttons say they are unavailable but keep focus: disabling a focused
// button would drop focus to the page, and keyboard and screen reader users would lose their place.
function setBusy(on) {
  busy = on;
  for (const b of [$('send'), ...document.querySelectorAll('#examples button, #log .followup, #log .retry')]) {
    if (on) b.setAttribute('aria-disabled', 'true'); else b.removeAttribute('aria-disabled');
  }
}

function switchLine(name) {
  const line = node('p', '', 'switch');
  line.append(node('span', `Now asking about ${name}`));
  return line;
}

// Set the district the chat asks about, or queue the switch while an answer loads. A switch adds the district's
// address to the browser history, unless it came from there (push false): Back, Forward or an opened link.
function choose(geo, name, quiet, push = true) {
  setLookup(false);
  $('place').value = '';
  if (busy) {
    queued = geo === district ? null : {geo, name, push};
    $('chat-status').textContent = queued ? `Switching to ${name} after this answer.` : '';
    // The profile needs no model, so it shows the chosen district while the answer loads.
    loadProfile(geo, name);
    if (quiet) return;
    showChat();
    $('question').focus({preventScroll: true});
    return;
  }
  const switched = geo !== district;
  if (switched) {
    const log = $('log'), last = log.lastElementChild;
    if (last && last.classList.contains('switch')) last.remove();
    if (asked && geo !== asked) {
      log.append(switchLine(name));
      if (!quiet) $('messages').scrollTop = $('messages').scrollHeight;
    }
    district = geo;
    $('chat').dataset.chamber = geo.split(':')[0];
    $('district-title').textContent = name;
    $('welcome-text').textContent = `Ask about ${name}.`;
    $('change').textContent = 'Change district';
    document.title = `${name} – KLEAR`;
    if (push) setAddress(districtPath(geo));
    document.dispatchEvent(new CustomEvent('klear:district', {detail: {geo, name}}));
    loadProfile(geo, name);
  }
  const ready = $('question').value.trim() ? 'Your question is ready. Press Ask KLEAR.' : '';
  $('chat-status').textContent = quiet && switched ? `Now asking about ${name}. ${ready}`.trim() : ready;
  if (quiet) return;
  showChat();
  $('question').focus({preventScroll: true});
}

// Back to the page with no district: the starter questions return in place of the profile. The conversation stays.
function clearDistrict() {
  queued = null;
  district = null;
  profileSeq++;
  profileGeo = null;
  setLookup(false);
  delete $('chat').dataset.chamber;
  $('district-title').textContent = 'No district chosen yet';
  $('welcome-text').textContent = 'Ask about this district.';
  $('chat-status').textContent = '';
  $('profile').hidden = true;
  $('profile').replaceChildren();
  $('profile-live').textContent = '';
  setSheet(null);
  dropDistrictActions();
  if ($('starter').hidden) {
    for (const b of $('examples').children) for (const part of b.children) part.hidden = !part.hidden;
    $('starter').append($('examples'));
    $('starter').hidden = false;
    $('suggest').hidden = true;
  }
  document.title = BASE_TITLE;
}

window.addEventListener('popstate', () => {
  const geo = routeGeo();
  // Back or Forward within the same district, such as to a section link, changes nothing.
  if (geo === (queued ? queued.geo : district)) return;
  if (geo) choose(geo, districtName(geo), true, false);
  else clearDistrict();
});

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
    const place = m.kind === 'zcta' ? `ZIP code ${m.geo.split(':')[1]}` : m.name;
    // Name the island, or the county when the store has no islands. An island, county or address names itself.
    const where = ['island', 'county', 'address'].includes(m.kind) ? '' : m.island || (county && county.name);
    const rank = g => (g.geo.startsWith('senate:') ? 1000 : 0) - Math.round((g.share ?? 1) * 100);
    rows.sort((a, b) => rank(a) - rank(b) || a.geo.localeCompare(b.geo));
    const title = m.kind === 'district' ? '' : [place, where].filter(Boolean).join(', ');
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
  box.append(mapNudge('Still not sure which is yours?'));
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
// A picked suggestion searches at once for exactly what it names. Returns a function that clears the form's results
// and drops any search still loading.
function lookupForm(form, input, status, box, pick) {
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
        box.append(mapNudge('Not sure which district is yours?'));
        return;
      }
      if (groups.every(g => g.kind === 'address')) status.textContent = 'Choose the House or Senate district to ask about.';
      else status.textContent = groups.some(g => g.title) ? 'Choose a district. The percent beside each one is the share of that area’s residents who live in it.' : 'Choose a district:';
      showMatches(groups, box, pick);
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

$('change').onclick = () => {
  const open = $('lookup').hidden;
  // Reopening the search drops a switch queued while an answer loads.
  if (open && queued) { queued = null; $('chat-status').textContent = ''; }
  setLookup(open);
  if (open) $('place').focus();
};
const resetLookup = lookupForm($('lookup'), $('place'), $('lookup-status'), $('matches'), choose);
// The search at the top of the page sets the chat's district and moves to the chat.
const resetFind = lookupForm($('find'), $('find-place'), $('find-status'), $('find-matches'), (geo, name) => { resetFind(); choose(geo, name); });
// Each starter shows its full question. Once it moves into the chat it shows only its short label, and the rest of the
// question stays in its spoken name after that label, so voice control can say what it sees.
for (const [question, label] of examples) {
  const b = node('button', '');
  b.type = 'button';
  const short = says('span', label, 'example-label'), rest = hidden(': '), full = says('span', question, 'example-full');
  short.hidden = true;
  rest.hidden = true;
  rest.append(says('span', question));
  b.append(short, rest, full);
  b.onclick = () => { if (busy) return; $('question').value = question; fitQuestion(); if (district) $('question-form').requestSubmit(); else needDistrict(); };
  $('examples').append(b);
}

// Once a district is chosen, its profile takes the starter questions' place and they move into the chat as short buttons.
// The profile is long, so it is not a live region: a short status line says when it has loaded.
let profileSeq = 0, profileGeo = null;
async function loadProfile(geo, name) {
  if (geo === profileGeo) return;
  profileGeo = geo;
  const seq = ++profileSeq, panel = $('profile'), live = $('profile-live');
  live.textContent = '';
  if (!$('starter').hidden) {
    for (const b of $('examples').children) for (const part of b.children) part.hidden = !part.hidden;
    $('suggest').append($('examples'));
    $('suggest').hidden = false;
    $('starter').hidden = true;
  }
  panel.hidden = false;
  panel.replaceChildren(...profileTitle(name), node('p', 'Loading the district profile…', 'profile-status'));
  setSheet(null);
  try {
    const data = await api('/api/profile?district=' + encodeURIComponent(geo));
    if (seq !== profileSeq) return;
    panel.replaceChildren(...renderProfile(data));
    setSheet(data);
    districtExamples(data);
    live.textContent = `Profile for ${data.district} loaded.`;
  } catch (err) {
    if (seq !== profileSeq) return;
    profileGeo = null;
    const message = `The district profile could not load. ${err.message}`;
    panel.replaceChildren(...profileTitle(name), node('p', message, 'profile-status'));
    live.textContent = message;
  }
}

// The panel is labelled by its heading, so the heading is there while the profile loads too.
function profileTitle(name) {
  const title = node('h3', name);
  title.id = 'profile-title';
  return [node('p', 'District profile', 'small-label'), title];
}

// What a short label stands for, one tap away: a plain definition, then the official label and who the figure
// counts, and a link to the glossary. Nothing when there is no more to say than the label itself.
function meaning(label, m) {
  const official = m && m.official && m.official !== label ? m.official : '';
  if (!m || !(m.plain || m.universe || official)) return null;
  const box = node('details', '', 'meaning'), summary = node('summary', 'What this means');
  summary.append(hidden(`: ${label}`));
  box.append(summary);
  if (m.plain) box.append(says('p', m.plain));
  const terms = node('dl', '');
  const term = (name, text) => { const t = node('div', ''); t.append(node('dt', name), says('dd', text)); terms.append(t); };
  if (official) term('Official label', official);
  if (m.universe) term('Who is counted', m.universe);
  if (terms.children.length) box.append(terms);
  // A new tab, so a chat in progress stays where it is.
  const more = node('p', '', 'meaning-more'), a = node('a', 'Glossary of terms');
  a.href = '/glossary.html'; a.target = '_blank';
  a.append(hidden(' (opens in a new tab)'));
  more.append(a);
  box.append(more);
  return box;
}

function profileRow(r) {
  const row = node('div', '', 'profile-row');
  const figure = node('p', '', 'profile-figure');
  figure.append(node('span', r.value, 'num'));
  if (r.spread) figure.append(' ', node('span', r.spread, 'moe'));
  const label = says('p', r.label, 'profile-label');
  if (r.note) { const sup = node('sup', ''); sup.append(hidden(' source '), String(r.note)); label.append(sup); }
  row.append(figure, label);
  if (r.caution) row.append(node('p', r.caution, 'profile-caution'));
  if (r.against.length) {
    const list = node('ul', '', 'profile-against');
    for (const a of r.against) { const li = document.createElement('li'); li.append(node('b', a.lead), ` ${a.rest}`); list.append(li); }
    row.append(list);
  }
  if (r.beside) row.append(besideLine(r.beside));
  if (r.caveat) row.append(node('p', r.caveat, 'profile-caveat'));
  const about = meaning(r.label, r.meaning);
  if (about) row.append(about);
  return row;
}

// A second figure shown next to a row for comparison, or a sentence on how to read the row.
function besideLine(b) {
  const p = node('p', '', 'profile-beside');
  if (!b.value) { p.append(b.text); return p; }
  p.append('For comparison, ', node('b', b.value));
  if (b.spread) p.append(' ', node('span', b.spread.startsWith('range') ? `(${b.spread})` : b.spread, 'moe'));
  p.append(` ${b.text}`);
  if (b.note) { const sup = node('sup', String(b.note)); sup.setAttribute('aria-label', `source ${b.note}`); p.append(sup); }
  return p;
}

// The section's citations, numbered when its figures come from more than one source, then any source
// that only the county and state figures come from.
function profileSources(sources, compared = []) {
  const box = node('div', '', 'profile-sources');
  const cite = (items, tag) => {
    const list = node(tag, '');
    // the year goes on its own line, so a wrapped link never leaves its comma at the start of a line
    for (const s of items) { const li = document.createElement('li'); li.append(link(s.name, s.url), node('span', s.year, 'profile-source-year')); list.append(li); }
    return list;
  };
  box.append(node('p', sources.length > 1 ? 'Sources' : 'Source', 'profile-sources-title'), cite(sources, sources.length > 1 ? 'ol' : 'ul'));
  if (compared.length) box.append(node('p', 'County and state figures', 'profile-sources-title'), cite(compared, 'ul'));
  return box;
}

// One program: its name linked to the page to apply or learn more, the agency, why it is listed here, and how to call.
function programRow(p, full) {
  const row = node('div', '', 'program');
  const name = node('p', '', 'program-name');
  name.append(link(p.name, p.url));
  row.append(name, node('p', p.agency, 'program-agency'));
  if (full && p.description) row.append(node('p', p.description, 'program-about'));
  if (p.reasons && p.reasons.length) {
    const list = node('ul', '', 'program-reasons');
    for (const r of p.reasons) list.append(node('li', r));
    row.append(list);
  }
  const contact = node('p', '', 'program-contact'), tel = node('a', p.phone);
  tel.href = 'tel:+' + (p.phone.startsWith('1-') ? '' : '1') + p.phone.replace(/\D/g, '');
  contact.append('Call ', tel, p.phone_label ? `, ${p.phone_label}.` : '.', ` Details last verified ${p.verified}.`);
  row.append(contact);
  return row;
}

// Matched programs first, then the rest of the catalog under its own heading.
function programList(matched, others, othersTitle) {
  const out = matched.map(p => programRow(p, true));
  if (others.length) out.push(node('p', othersTitle, 'program-subhead'), ...others.map(p => programRow(p, false)));
  return out;
}

// The district's matched programs, or null when the profile has none in the expected shape.
function programsOf(p) {
  const pr = p.programs;
  return pr && Array.isArray(pr.programs) && Array.isArray(pr.others) ? pr : null;
}
// Programs on the one-page summary, so it stays one page.
const SHEET_PROGRAMS = 6;

function profilePrograms(pr) {
  const d = node('details', '', 'profile-section profile-programs');
  d.append(node('summary', pr.title), node('p', pr.intro, 'program-intro'), ...programList(pr.programs, pr.others, pr.others_title), node('p', pr.note, 'program-note'));
  if ((pr.sources || []).length) d.append(profileSources(pr.sources));
  return d;
}

// The largest places in a district and the other chamber's districts it overlaps, with their shares of its residents.
function placesList(p) {
  const more = p.places.length - 4;
  return p.places.slice(0, 4).map(x => `${x.name} (${x.share})`).join(', ') + (more > 0 ? `, and ${more} more places` : '');
}
const overlapsList = p => p.overlaps.map(o => `${o.name} (${o.share})`).join(', ');

// Line icons for the share controls, on a 24-unit grid in the text color. Screen readers skip them.
const ICONS = {
  print: 'M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2M7 14h10v7H7z',
  link: 'M9 7H7a5 5 0 0 0 0 10h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  check: 'M5 12.5l4.5 4.5L19 7.5',
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

// Copy a link to the district. Without the clipboard API, as on a page served over plain HTTP, copy through a
// selection, and failing that show the address to copy by hand.
async function copyLink(url, status) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(url);
    copied = true;
  } catch {
    // Fixed and transparent, so selecting it neither scrolls the page nor shows.
    const back = document.activeElement, box = node('textarea', url, 'copy-buffer');
    box.readOnly = true;
    document.body.append(box);
    box.focus({preventScroll: true});
    box.setSelectionRange(0, url.length);
    try { copied = document.execCommand('copy'); } catch { /* not supported */ }
    box.remove();
    if (back && back.focus) back.focus({preventScroll: true});
  }
  const text = copied ? 'Link copied.' : `Copy this link: ${url}`;
  status.replaceChildren(...(copied ? [icon('check'), text] : [text]));
  // Cleared first, so a second copy is announced again.
  $('profile-live').textContent = '';
  requestAnimationFrame(() => { $('profile-live').textContent = text; });
}

// Print, link and download controls, in their own panel near the top of the profile. Printing shows the one-page
// summary in place of the page.
function profileActions(p) {
  const box = node('div', '', 'profile-share'), title = node('p', 'Share this profile', 'profile-share-title');
  title.id = 'share-title';
  box.setAttribute('role', 'group');
  box.setAttribute('aria-labelledby', title.id);
  const control = (tag, text, name, primary) => {
    const c = node(tag, '', primary ? 'action primary' : 'action');
    c.append(icon(name), node('span', text));
    if (tag === 'button') c.type = 'button';
    return c;
  };
  const print = control('button', 'Print or save as PDF', 'print', true), copy = control('button', 'Copy link', 'link');
  const csv = control('a', 'Download data (CSV)', 'download');
  print.onclick = () => window.print();
  csv.href = '/api/profile.csv?district=' + encodeURIComponent(p.geo);
  csv.download = '';
  const actions = node('div', '', 'profile-actions'), status = node('p', '', 'profile-share-status');
  copy.onclick = () => copyLink(location.origin + districtPath(p.geo), status);
  actions.append(print, copy, csv);
  box.append(title, node('p', 'A one-page summary with sources, a link to this district, or every figure as a spreadsheet.', 'profile-share-note'), actions, status);
  return box;
}

function renderProfile(p) {
  const out = profileTitle(p.district);
  if (p.where) out.push(node('p', p.where, 'profile-where'));
  if (p.places.length) out.push(node('p', `Includes ${placesList(p)}`, 'profile-meta'));
  if (p.overlaps.length) out.push(node('p', `Overlaps ${overlapsList(p)}`, 'profile-meta'));
  out.push(profileActions(p));
  const stats = node('dl', '', 'profile-stats');
  // Each term comes before its value, as a description list needs; the CSS shows the value on top.
  for (const h of p.headline) { const s = node('div', ''); s.append(says('dt', h.label), node('dd', h.value)); stats.append(s); }
  out.push(stats);
  if (programsOf(p)) out.push(profilePrograms(p.programs));
  for (const sec of p.sections) {
    if (!sec.rows.length) continue;
    const d = node('details', '', 'profile-section');
    d.append(node('summary', sec.title));
    if (sec.caveat) d.append(node('p', sec.caveat, 'profile-caveat lead'));
    d.append(...sec.rows.map(profileRow), profileSources(sec.sources, sec.compared));
    out.push(d);
  }
  return out;
}

// The one-page summary that prints in place of the page: twelve key figures beside the county and the state, programs
// to explore, and every source with its vintage. Paper cannot follow links, so every address is written out.
const PRINT_GROUPS = [
  ['People', ['pop_total', 'pct_65_plus', 'pop_under_18']],
  ['Income and poverty', ['median_household_income', 'pct_people_poverty', 'pct_children_poverty', 'pct_65_plus_poverty']],
  ['Housing', ['pct_renter_occupied', 'median_gross_rent', 'pct_rent_burden_35_plus']],
  ['Health and assistance', ['pct_uninsured', 'pct_hh_snap']],
];
const longDate = d => d.toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'});

// A summary that cannot be drawn leaves the page to print as it is, and never stops the profile from showing.
function setSheet(p) {
  const sheet = $('print-sheet');
  let parts = [];
  try {
    if (p) parts = sheetParts(p);
  } catch (err) {
    console.warn('KLEAR could not lay out the printed summary.', err);
  }
  sheet.replaceChildren(...parts);
  sheet.dataset.chamber = parts.length ? p.geo.split(':')[0] : '';
  document.body.classList.toggle('has-sheet', parts.length > 0);
}
// The print date is the day it is printed, even when the page has been open since an earlier day.
window.addEventListener('beforeprint', () => {
  const printed = $('print-sheet').querySelector('.sheet-printed');
  if (printed) printed.textContent = `Printed ${longDate(new Date())}`;
});

function sheetParts(p) {
  // Sources are numbered in the order the figures cite them; each row's Source cell lists every source it uses.
  const sources = [];
  const cite = s => {
    if (!s || !s.name) return 0;
    const i = sources.findIndex(x => x.name === s.name && x.year === s.year);
    return i < 0 ? sources.push(s) : i + 1;
  };
  const url = text => node('span', text, 'sheet-url');
  const section = (title, className, ...content) => {
    const s = node('section', '', `sheet-section ${className}`);
    s.append(node('h2', title), ...content);
    return s;
  };
  const byId = new Map(p.sections.flatMap(s => s.rows.map(r => [r.id, r])));
  const groups = PRINT_GROUPS.map(([title, ids]) => [title, ids.map(id => byId.get(id)).filter(Boolean)]).filter(([, rows]) => rows.length);
  const against = (r, kind) => (r.against || []).find(a => String(a.geo || '').startsWith(`${kind}:`));
  const placeOf = kind => (groups.flatMap(([, rows]) => rows).map(r => against(r, kind)).find(a => a && a.place) || {}).place;
  // A column pair (estimate, then margin) for the district, then its county and the state when the profile has them.
  const places = [['home', p.district], ['county', placeOf('county')], ['state', placeOf('state')]].filter(([, name]) => name);

  const table = node('table', '', 'sheet-figures'), headRow = table.createTHead().insertRow(), measure = node('th', 'Measure');
  measure.scope = 'col';
  headRow.append(measure);
  for (const [kind, name] of places) {
    const th = node('th', name, `place ${kind}`);
    th.colSpan = 2;
    th.scope = 'colgroup';
    headRow.append(th);
  }
  const sourceHead = node('th', 'Source', 'src');
  sourceHead.scope = 'col';
  headRow.append(sourceHead);
  let unreliable = false, ranged = false;
  for (const [title, rows] of groups) {
    const body = table.createTBody(), group = node('th', title, 'sheet-group');
    group.colSpan = 2 + places.length * 2;
    group.scope = 'colgroup';
    body.insertRow().append(group);
    for (const r of rows) {
      const tr = body.insertRow(), label = says('th', r.label), cited = [];
      label.scope = 'row';
      tr.append(label);
      for (const [kind] of places) {
        const f = kind === 'home' ? r : against(r, kind);
        if (!f) {
          const none = node('td', 'Not available', `sheet-none ${kind}`);
          none.colSpan = 2;
          tr.append(none);
          continue;
        }
        const value = node('td', f.value, `num ${kind}`);
        if (kind === 'home' && r.caution) { value.append('†'); unreliable = true; }
        ranged ||= String(f.spread || '').startsWith('range');
        cited.push(cite(f.source));
        tr.append(value, node('td', f.spread || '', `moe ${kind}`));
      }
      tr.append(node('td', [...new Set(cited.filter(Boolean))].sort((a, b) => a - b).join(', '), 'src'));
    }
  }
  const margins = ['± is the 90% margin of error. Where two figures’ margins overlap, the difference may not be real.'];
  if (ranged) margins.push('A range gives the low and high bounds of an estimate.');
  if (unreliable) margins.push('† The margin of error is large; use with care.');

  // Who the district is on the left; where to find it online, and how current it is, on the right.
  const head = node('header', '', 'sheet-head'), who = node('div', ''), live = node('div', '', 'sheet-live');
  who.append(node('p', 'KLEAR district summary', 'sheet-kicker'), node('h1', p.district));
  if (p.where) who.append(node('p', p.where, 'sheet-where'));
  live.append(node('p', 'Full profile, sources and questions online', ''), url(location.origin + districtPath(p.geo)));
  const dates = node('p', p.built ? `Data built ${p.built} · ` : '', 'sheet-dates');
  dates.append(node('span', `Printed ${longDate(new Date())}`, 'sheet-printed'));
  live.append(dates);
  head.append(who, live);

  const parts = [head];
  const about = node('div', '', 'sheet-about'), line = (label, text) => { const l = node('p', ''); l.append(node('b', label), ` ${text}`); return l; };
  if (p.places.length) about.append(line('Includes', placesList(p)));
  if (p.overlaps.length) about.append(line('Overlaps', overlapsList(p)));
  if (about.lastChild) {
    const n = cite(p.shares_source);
    about.lastChild.append(`. Shares of 2020 Census residents${n ? ` (source ${n})` : ''}.`);
    parts.push(about);
  }
  parts.push(section('Key figures', 'sheet-key', table, node('p', margins.join(' '), 'sheet-note')));

  // The programs whose need figures here sit above the state's, or the statewide list when none does.
  const matched = programsOf(p), shown = matched ? (matched.programs.length ? matched.programs : matched.others) : [];
  const programs = shown.filter(g => g && g.name).slice(0, SHEET_PROGRAMS);
  if (programs.length) {
    const list = node('ul', '', 'sheet-list');
    for (const g of programs) {
      const li = document.createElement('li');
      li.append(node('b', g.name));
      if (g.description) li.append(': ', says('span', g.description));
      if (g.agency) li.append(` (${g.agency})`);
      if (g.url) li.append(' ', url(g.url));
      list.append(li);
    }
    const why = matched.programs.length ? 'Listed because this district’s need figures sit above the state’s, highest first.' : 'None of this district’s need figures sit clearly above the state’s, so these programs serve residents statewide.';
    const more = shown.length > programs.length ? ' The live profile lists every program.' : '';
    parts.push(section('Programs to explore', 'sheet-programs', list, node('p', `${why}${more} ${matched.note || ''}`.trim(), 'sheet-note')));
  }
  const list = node('ol', '', 'sheet-list');
  for (const s of sources) {
    const li = node('li', [s.name, s.year].filter(Boolean).join(', '));
    if (s.url) li.append(' ', url(s.url));
    list.append(li);
  }
  if (sources.length) parts.push(section('Sources', 'sheet-sources', list));
  // A paragraph, not a footer element, so the site footer's dark band never reaches the page.
  parts.push(node('p', 'Public, aggregated data from KLEAR, made for HAIC 2026. Not an official State of Hawaiʻi product.', 'sheet-foot'));
  return parts;
}

function sentLine(question, chamber) {
  const sent = node('p', '', 'user');
  sent.append(hidden('You asked: '), question);
  sent.dataset.chamber = chamber;
  return sent;
}

// This tab's questions and answers, kept in session storage so a refresh does not lose them. Storage can be blocked
// or full; the chat works the same without it. Errors are not kept: some browsers fail a request as the page reloads,
// which is not an error worth showing again. The newest question without an answer is kept, to wait in the box.
const SAVED_CHAT = 'klear-chat';
const exchanges = [];
function saveChat() {
  const kept = exchanges.filter((x, i) => x.answer || i === exchanges.length - 1);
  // When storage is full, keep the newest exchanges that fit.
  for (let from = 0; from <= kept.length; from++) {
    try { sessionStorage.setItem(SAVED_CHAT, JSON.stringify(kept.slice(from))); return; } catch { /* full or blocked */ }
  }
}

function restoreChat() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(SAVED_CHAT)); } catch { /* blocked or unreadable */ }
  if (!Array.isArray(saved)) return;
  let last = null, sent = null;
  for (const x of saved) {
    if (!x || typeof x.geo !== 'string' || typeof x.name !== 'string' || typeof x.question !== 'string') continue;
    // A question whose answer was still loading, or failed, waits in the box to ask again.
    if (!x.answer) {
      if (!$('question').value.trim()) { $('question').value = x.question; fitQuestion(); }
      continue;
    }
    const chamber = x.geo.split(':')[0];
    let reply;
    try { reply = renderAnswer(x.answer, x.name); } catch { continue; }
    reply.dataset.chamber = chamber;
    showMode(reply, x.answer.mode);
    askedQuestions.add(sameQuestion(x.question, x.name));
    if (last && x.geo !== last) $('log').append(switchLine(x.name));
    sent = sentLine(x.question, chamber);
    $('log').append(sent, reply);
    exchanges.push(x);
    last = x.geo;
  }
  saveChat();
  if (!last) return;
  asked = last;
  $('chat-card').classList.add('started');
  if (logScrolls()) $('messages').scrollTop = sent.offsetTop - 16;
}

// Restore the conversation and open the district the address names. This waits for the page's other scripts, so the
// map hears which district opened too.
document.addEventListener('DOMContentLoaded', () => {
  restoreChat();
  const geo = routeGeo();
  if (geo) {
    // An address like /district/house-07 is shown the usual way, /district/house-7.
    if (location.pathname !== districtPath(geo)) setAddress(districtPath(geo) + location.search + location.hash, true);
    choose(geo, districtName(geo), true, false);
    // A link opened fresh moves to the district; a reload, Back or Forward keeps the reader's place.
    const nav = performance.getEntriesByType ? performance.getEntriesByType('navigation')[0] : null;
    if ((!nav || nav.type === 'navigate') && !location.hash) $('chat').scrollIntoView({behavior: 'instant'});
  } else if (location.pathname.startsWith('/district/')) {
    setAddress('/', true);
    $('find-status').textContent = 'That link names a district that does not exist. Hawaiʻi has 51 House and 25 Senate districts.';
  }
});

$('question-form').onsubmit = async e => {
  e.preventDefault();
  if (busy) return;
  const question = $('question').value.trim();
  if (!question) return;
  if (!district) { needDistrict(); return; }
  // The card stays compact until the first question, then grows to hold the conversation.
  const card = $('chat-card');
  if (!card.classList.contains('started')) { card.classList.add('started'); showChat(); }
  setBusy(true);
  asked = district;
  const name = $('district-title').textContent, geo = district;
  askedQuestions.add(sameQuestion(question, name));
  // The conversation is not a live region, since answers are long. A short status line says what is happening,
  // and the new answer's heading takes focus.
  // Each exchange keeps the color of the chamber it was about, even after the chat switches district.
  const chamber = district.split(':')[0];
  const sent = sentLine(question, chamber), wait = waiting(name), pending = wait.box;
  pending.dataset.chamber = chamber;
  $('log').append(sent, pending);
  const saved = {geo: district, name, question};
  exchanges.push(saved);
  saveChat();
  reveal(sent);
  $('question').value = '';
  fitQuestion();
  $('chat-status').textContent = '';
  $('chat-live').textContent = `Checking district data and sources for ${name}…`;
  let follow = true, hold = null, reply = null, done, reader = null;
  try {
    const answer = await askKlear(question, saved.geo, wait);
    reader = readerPlace();
    hold = holdPage(pending);
    follow = onScreen(pending);
    reply = renderAnswer(answer, name);
    reply.dataset.chamber = chamber;
    showMode(reply, answer.mode);
    // Only the newest answer offers next questions, and an earlier failure's Retry goes once this one is answered.
    for (const n of $('log').querySelectorAll('.followups, .retry')) n.remove();
    const next = followUpGroup(followUps(question, answer, name, counties.get(geo)));
    if (next) reply.append(next);
    pending.replaceWith(reply);
    saved.answer = answer;
    done = `Answer ready for ${name}.`;
  } catch (err) {
    reader = reader || readerPlace();
    hold = holdPage(pending);
    follow = onScreen(pending);
    for (const n of $('log').querySelectorAll('.retry')) n.remove();
    // A reply the page could not lay out reads like any other unreadable reply.
    const shown = 'retry' in err ? err : problem('KLEAR sent a reply it could not read. Press Retry to ask again.');
    pending.replaceWith(errorNote(shown, question));
    done = shown.message;
    if (!$('question').value.trim()) { $('question').value = question; fitQuestion(); $('chat-status').textContent = 'Your question is ready to retry.'; }
  } finally {
    wait.stop();
    setBusy(false);
    saveChat();
    const {elsewhere, inChat} = reader || readerPlace();
    $('chat-live').textContent = done;
    if (queued) { const next = queued; queued = null; choose(next.geo, next.name, true, next.push); }
    if (hold) hold();
    if (follow && (logScrolls() || (!elsewhere && !(hold && hold.field)))) reveal(sent);
    // A reader still in the chat moves to the new answer; after an error, back to the question to retry.
    if (inChat && reply) reply.querySelector('h3').focus({preventScroll: true});
    else if (inChat) $('question').focus({preventScroll: true});
  }
};
// Where the reader is when an answer arrives, read before the answer changes the page: working in the district search
// or another field (leave the page and focus alone), or still in the chat (focus in the chat card or its suggestions,
// or on nothing while the question box shows).
function readerPlace() {
  const focus = document.activeElement;
  const elsewhere = !$('lookup').hidden || Boolean(focus && focus !== $('question') && focus.matches('input, select, textarea'));
  const idle = !focus || focus === document.body;
  const inChat = !elsewhere && ($('chat-card').contains(focus) || $('examples').contains(focus) || (idle && onScreen($('question-form'))));
  return {elsewhere, inChat};
}

// The last announcement is cleared once the reader starts the next question, so it is not read again.
$('question').addEventListener('input', () => { if (!busy) $('chat-live').textContent = ''; });

// Ctrl+Enter, or Command+Enter on a Mac, sends the question. Enter alone starts a new line.
$('question').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); $('question-form').requestSubmit(); }
});
if (/Mac|iPhone|iPad/.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform)) $('send-hint').textContent = 'Press ⌘+Enter to send.';

// Ask again with a question already written out: a follow-up, a district example, or a retry.
function askAgain(question) {
  if (busy) return;
  $('question').value = question;
  fitQuestion();
  $('question-form').requestSubmit();
}

// The waiting line: elapsed seconds, a Cancel button, and a time limit. The status line notes every 15 seconds that
// the answer is still coming, since the waiting line itself is not a live region.
const ASK_LIMIT = 45;
function waiting(name) {
  const box = node('div', '', 'pending'), text = node('p', 'Checking district data and sources… '), clock = node('span', '', 'elapsed');
  const cancel = node('button', 'Cancel', 'cancel'), control = new AbortController(), started = Date.now();
  const wait = {box, signal: control.signal, why: ''};
  const stop = why => { if (!wait.why) { wait.why = why; control.abort(why); } };
  cancel.type = 'button';
  cancel.onclick = () => stop('cancel');
  text.append(clock);
  box.append(text, cancel);
  let note = 15;
  const tick = setInterval(() => {
    const s = Math.floor((Date.now() - started) / 1000);
    clock.textContent = s === 1 ? '1 second' : `${s} seconds`;
    if (s >= note) { $('chat-live').textContent = `Still checking for ${name}: ${note} seconds so far. Press Cancel to stop.`; note += 15; }
  }, 1000);
  const limit = setTimeout(() => stop('timeout'), ASK_LIMIT * 1000);
  wait.stop = () => { clearInterval(tick); clearTimeout(limit); };
  return wait;
}

// An error that says what to do next, and whether pressing Retry could help.
const problem = (message, retry = true) => Object.assign(new Error(message), {retry});
function statusProblem(status) {
  if (status === 429) return problem('KLEAR is getting too many questions right now. Wait a minute, then press Retry.');
  if (status === 413) return problem('That question is too long for KLEAR to receive. Shorten it, then ask again.', false);
  if ([502, 503, 504].includes(status)) return problem(`KLEAR’s server is not responding right now (error ${status}). Wait a moment, then press Retry.`);
  if (status >= 500) return problem(`KLEAR ran into a server problem (error ${status}). Wait a moment, then press Retry.`);
  if (status >= 400) return problem(`KLEAR could not take this question (error ${status}). Reload the page, then ask again.`, false);
  return problem('KLEAR sent a reply it could not read. Press Retry to ask again.');
}

// Send a question. Replies that are not JSON, such as a proxy's error page, get a plain message instead of a parse error.
async function askKlear(question, geo, wait) {
  const stopped = () => problem(wait.why === 'timeout'
    ? `KLEAR did not answer within ${ASK_LIMIT} seconds, so the question was stopped. Press Retry to ask again.`
    : 'You cancelled this question. Press Retry to ask it again.');
  let r, data = null;
  try {
    r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question, district: geo}), signal: wait.signal});
  } catch {
    throw wait.why ? stopped() : problem('KLEAR could not be reached. Check your internet connection, then press Retry.');
  }
  try { data = await r.json(); } catch { if (wait.why) throw stopped(); }
  if (r.ok && data && typeof data === 'object') return data;
  if (data && typeof data.error === 'string') throw problem(data.error, r.status >= 500 || r.status === 429);
  throw statusProblem(r.ok ? 200 : r.status);
}

function errorNote(err, question) {
  const box = node('div', '', 'error');
  box.append(node('p', err.message));
  if (err.retry) {
    const retry = node('button', 'Retry', 'retry');
    retry.type = 'button';
    retry.onclick = () => askAgain(question);
    box.append(retry);
  }
  return box;
}

// A small badge after the answer heading that says whether the assistant or the basic lookup answered.
const modeNames = {assistant: ['Assistant', 'Written by the KLEAR assistant from the data store'], basic: ['Basic lookup', 'Figures matched by keyword in the data store, without the assistant']};
function showMode(article, mode) {
  if (!modeNames[mode]) return;
  const [label, about] = modeNames[mode], h = article.querySelector('h3'), head = node('div', '', 'answer-head'), badge = node('p', '', 'mode-badge');
  badge.title = about;
  badge.append(hidden('Answer type: '), label);
  h.replaceWith(head);
  head.append(h, badge);
}

// Topics for next questions, matched in the order the basic lookup matches them, so every question written here is
// one the basic lookup answers too. Each question names its district, so it stands on its own.
const TOPICS = [
  {key: 'programs', match: /program|benefit|assistance|support|help available/, ask: d => `What support programs could residents of ${d} explore?`, next: ['poverty', 'housing']},
  {key: 'profile', match: /\b(profile|socioeconomic|overview)/, ask: d => `Give me a socioeconomic profile of ${d}.`, next: ['kupuna', 'housing', 'programs']},
  {key: 'kupuna', match: /\b(kūpuna|kupuna|senior|older adult|65)/, compare: 'kūpuna', ask: d => `How many kūpuna live in ${d}?`, more: d => `How many kūpuna in ${d} live below the poverty level?`, next: ['health', 'programs']},
  {key: 'poverty', match: /\b(poverty|low income)/, compare: 'poverty', ask: d => `How many people in ${d} live below the poverty level?`, more: d => `How many keiki in ${d} live below the poverty level?`, next: ['income', 'programs']},
  {key: 'housing', match: /\b(housing|rent|cost burden)/, compare: 'housing costs', ask: d => `What does the data say about housing needs in ${d}?`, more: d => `How many renters in ${d} pay 35% or more of their income on rent?`, next: ['income', 'programs']},
  {key: 'income', match: /\b(income|wage|earn)/, compare: 'household income', ask: d => `What is the median household income in ${d}?`, more: d => `What is the per capita income in ${d}?`, next: ['poverty', 'housing']},
  {key: 'work', match: /\b(unemploy|jobs|work)/, compare: 'unemployment', ask: d => `How many people in ${d} are unemployed?`, more: d => `How many people in ${d} work from home?`, next: ['income', 'poverty']},
  {key: 'health', match: /\b(health|uninsured|disabil)/, compare: 'health insurance coverage', ask: d => `How many people in ${d} have no health insurance?`, more: d => `How many people in ${d} have a disability?`, next: ['kupuna', 'programs']},
  {key: 'keiki', match: /\b(child|keiki|young)/, compare: 'keiki', ask: d => `How many keiki live in ${d}?`, more: d => `How many households in ${d} have children?`, next: ['poverty', 'programs']},
  {key: 'people', match: /\b(population|people|residents)/, ask: d => `How many people live in ${d}?`, next: ['kupuna', 'keiki', 'income']},
];
const topic = key => TOPICS.find(t => t.key === key);
const topicOf = text => TOPICS.find(t => t.match.test(text.toLowerCase()));
const compareQuestion = (what, name, county) => `Compare ${what} in ${name} with ${county || 'its county'} and the state.`;
// Questions already asked, written the same way whether they said "this district" or named it.
const askedQuestions = new Set();
const sameQuestion = (q, name) => q.trim().toLowerCase().replaceAll('this district', name.toLowerCase()).replace(/\s+/g, ' ');
// Main county of each district whose profile has loaded.
const counties = new Map();

// Up to three next questions: the same measure against the county and state, a closer look, then related topics.
function followUps(question, answer, name, county) {
  const out = [], add = q => { if (!askedQuestions.has(sameQuestion(q, name)) && !out.includes(q)) out.push(q); };
  const declined = /^decline/.test(answer.refusal || '');
  const words = [...(answer.lead || []), ...(answer.tiles || []).map(t => t.label), ...(answer.charts || []).map(c => c.label), ...(answer.facts || []).map(f => f.label)].join(' ');
  const t = declined ? null : topicOf(question) || topicOf(words);
  if (t && t.compare && !/county/i.test(question)) add(compareQuestion(t.compare, name, county));
  if (t && t.more) add(t.more(name));
  for (const key of [...(t ? t.next : []), 'profile', 'kupuna', 'housing', 'poverty', 'programs']) add(topic(key).ask(name));
  return out.slice(0, 3);
}

let followSeq = 0;
function followUpGroup(questions) {
  if (!questions.length) return null;
  const box = node('div', '', 'followups'), title = node('p', 'Ask next', 'followups-title');
  title.id = `followups-${++followSeq}`;
  box.setAttribute('role', 'group');
  box.setAttribute('aria-labelledby', title.id);
  box.append(title);
  for (const q of questions) {
    const b = says('button', q, 'followup');
    b.type = 'button';
    b.onclick = () => askAgain(q);
    box.append(b);
  }
  return box;
}

// Measures that make a district example when the district's rate stands well above the state's or its county's.
const STANDOUTS = [['pct_65_plus', 'Kūpuna share', 'kūpuna'], ['pct_people_poverty', 'Poverty', 'poverty'], ['pct_children_poverty', 'Keiki poverty', 'keiki poverty'], ['pct_65_plus_poverty', 'Kūpuna poverty', 'poverty among kūpuna'], ['pct_rent_burden_35_plus', 'Rent burden', 'rent burden'], ['pct_uninsured', 'Uninsured rate', 'health insurance coverage'], ['pct_disability', 'Disability rate', 'disability']];
// Places burned in the August 2023 Maui wildfires, whose survey figures mostly predate the fires.
const WILDFIRE_PLACES = ['Lahaina', 'Kula'];
// How many times the other place's rate the district's is, read from the profile's comparison words.
function timesAbove(a) {
  const m = a && /^about (?:([\d.]+) times|(\d+) percent higher)/.exec(a.lead);
  return !m ? 0 : m[1] ? Number(m[1]) : 1 + Number(m[2]) / 100;
}

// Examples that fit the chosen district, from its profile: a wildfire note where the survey years matter, and the
// measure that stands out most against the state, or else the county. They lead the suggested questions.
function districtExamples(p) {
  counties.set(p.geo, p.county);
  for (const b of $('examples').querySelectorAll('.local')) b.remove();
  const rows = new Map(p.sections.flatMap(s => s.rows).map(r => [r.id, r]));
  let best = null;
  for (const [id, label, what] of STANDOUTS) {
    const r = rows.get(id);
    if (!r || !r.against.length) continue;
    const state = timesAbove(r.against.at(-1)), county = r.against.length > 1 ? timesAbove(r.against[0]) : 0;
    const score = state >= 1.2 ? 10 + state : county >= 1.2 ? county : 0;
    if (score && (!best || score > best.score)) best = {score, label: `${label} higher than ${state >= 1.2 ? 'statewide' : 'countywide'}`, what};
  }
  const chips = [];
  if (p.places.some(x => WILDFIRE_PLACES.includes(x.name))) chips.push(['Survey years and the 2023 wildfires', `Which survey years do the housing figures for ${p.district} cover, given the 2023 Maui wildfires?`]);
  if (best) chips.push([best.label, compareQuestion(best.what, p.district, p.county)]);
  $('examples').prepend(...chips.map(([label, question]) => {
    const b = node('button', '', 'local'), rest = hidden(': ');
    b.type = 'button';
    if (busy) b.setAttribute('aria-disabled', 'true');
    rest.append(says('span', question));
    b.append(says('span', label, 'example-label'), rest);
    b.onclick = () => askAgain(question);
    return b;
  }));
}

// Next questions, retries and district examples name a district, so they go when the chat switches to another.
function dropDistrictActions() {
  for (const n of document.querySelectorAll('#log .followups, #log .retry, #examples .local')) n.remove();
}
document.addEventListener('klear:district', dropDistrictActions);

// Answer heading that names the district the question was asked about. It takes focus when the answer arrives.
function heading(answer, name) {
  const h = node('h3', refusalLabels[answer.refusal] || 'KLEAR');
  h.tabIndex = -1;
  if (name) h.append(hidden(', '), node('span', name, 'answer-for'));
  return h;
}

// A labelled figure for the basic lookup, with its source.
function factCard(f) {
  const card = node('div', '', 'fact');
  const uncertainty = uncertaintyLabels[f.uncertainty] || uncertaintyLabels.unavailable;
  card.append(says('b', f.label), node('p', f.value), node('small', `${f.year}, ${f.geography}. Uncertainty: ${uncertainty}`), link(f.source, f.url));
  return card;
}

// A figure card with what its label means at the end.
function explainedCard(f) {
  const card = factCard(f), about = meaning(f.label, f.meaning);
  if (about) card.append(about);
  return card;
}

function basicAnswer(answer, name) {
  const article = document.createElement('article');
  article.append(heading(answer, name));
  for (const p of answer.text.split('\n\n')) article.append(says('p', p));
  if (answer.facts.length) {
    const details = document.createElement('details');
    details.open = true;
    details.append(node('summary', 'Figures & sources'), ...answer.facts.map(explainedCard));
    article.append(details);
  }
  if (answer.programs && answer.programs.length) article.append(answerPrograms(answer.programs, answer.program_sources));
  return article;
}

// A link that opens in a new tab and says so, or plain text when the address is not a web page.
function link(text, href) {
  try {
    const url = new URL(href);
    if (['http:', 'https:'].includes(url.protocol)) {
      const a = node('a', text, 'ext');
      a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.append(hidden(' (opens in a new tab)'));
      return a;
    }
  } catch {}
  return node('span', text);
}

// Bars for sighted readers, with a sentence that says what they show and the same figures as a table.
// The bars repeat the table, so screen readers skip them.
function chartFigure(c) {
  const fig = node('figure', '', 'chart');
  if (c.bars.some(b => b.text.length > 22)) fig.classList.add('wide');
  const head = node('figcaption', '', 'chart-head');
  head.append(says('span', c.label));
  if (c.tag) head.append(node('span', c.tag, c.lower ? 'chip strong' : 'chip'));
  fig.append(head);
  const about = meaning(c.label, c.meaning);
  if (about) fig.append(about);
  if (c.summary) fig.append(says('p', c.summary, 'chart-summary'));
  const bars = node('div', '', 'chart-bars');
  bars.setAttribute('aria-hidden', 'true');
  const max = Math.max(...c.bars.map(b => b.high)) || 1;
  c.bars.forEach((b, i) => {
    const row = node('div', '', 'bar-row');
    // The district asked about is first, darker, and named in bold, so the bars do not rely on color alone.
    row.append(node('span', b.geography, i ? 'bar-place' : 'bar-place home'));
    const track = node('div', '', 'bar-track');
    const bar = node('div', '', i ? 'bar other' : 'bar home');
    bar.style.width = (b.value / max * 100) + '%';
    bar.title = `${b.geography}: ${b.text}`;
    track.append(bar);
    if (b.high > b.low) {
      const whisker = node('div', '', 'whisker');
      whisker.style.left = (b.low / max * 100) + '%';
      whisker.style.width = ((b.high - b.low) / max * 100) + '%';
      track.append(whisker);
    }
    const [num, ...spread] = b.text.split(' '), value = node('span', num, 'bar-value');
    if (spread.length) value.append(' ', node('span', spread.join(' '), 'bar-spread'));
    row.append(track, value);
    bars.append(row);
  });
  fig.append(bars, chartTable(c));
  return fig;
}

// The chart's figures as a table, collapsed under the bars.
function chartTable(c) {
  const details = node('details', '', 'chart-data');
  details.append(node('summary', 'Show the data table'));
  const spreads = c.bars.map(b => b.spread || '').filter(Boolean);
  const ranges = spreads.filter(s => s.startsWith('range')).length;
  const spreadHead = !spreads.length ? '' : !ranges ? '90% margin of error' : ranges === spreads.length ? 'Range' : 'Margin of error or range';
  const table = document.createElement('table'), caption = says('caption', c.label), head = document.createElement('tr');
  for (const h of ['Place', 'Estimate', spreadHead].filter(Boolean)) { const th = node('th', h); th.scope = 'col'; head.append(th); }
  const body = document.createElement('tbody');
  for (const b of c.bars) {
    const row = document.createElement('tr'), place = node('th', b.geography);
    place.scope = 'row';
    row.append(place, node('td', b.number ?? b.text));
    if (spreadHead) row.append(node('td', b.spread || 'None published'));
    body.append(row);
  }
  table.append(caption, node('thead', ''), body);
  table.tHead.append(head);
  const scroll = node('div', '', 'table-scroll');
  scroll.append(table);
  details.append(scroll);
  return details;
}

const ruler = document.createElement('canvas').getContext('2d');
function ems(text, weight) { ruler.font = `${weight} 100px "Public Sans", system-ui, sans-serif`; return ruler.measureText(text).width / 100; }

// Big tile number after a small 'about' for estimates. --ems and --about are their widths in em, so the CSS can shrink long numbers to fit.
function tileValue(text) {
  const [, about, figure] = text.match(/^(about )?([\s\S]*)$/), value = node('div', '', 'tile-value'), num = node('span', figure, 'tile-num');
  if (about) {
    value.append(node('span', 'about ', 'tile-about'));
    value.style.setProperty('--about', ems('about ', 600).toFixed(3));
  }
  value.append(num);
  if (/\d/.test(figure)) num.style.setProperty('--ems', (ems(figure, 400) - 0.02 * figure.length).toFixed(3));
  else value.classList.add('none');
  return value;
}

function renderAnswer(answer, name) {
  if (!answer.lead) return basicAnswer(answer, name);
  const article = document.createElement('article');
  article.className = 'answer';
  article.append(heading(answer, name));
  let list = null;
  for (const line of answer.lead.flatMap(p => p.split('\n'))) {
    const item = line.match(/^\s*[*-]\s+(.*)/);
    if (!item) { list = null; if (line.trim()) article.append(says('p', line, 'lead')); continue; }
    if (!list) article.append(list = node('ul', '', 'lead-list'));
    list.append(says('li', item[1]));
  }
  if (answer.tiles.length) {
    const grid = node('div', '', 'tiles');
    for (const t of answer.tiles) {
      const tile = node('div', '', 'tile');
      tile.append(tileValue(t.value), says('div', t.label, 'tile-label'));
      const meta = [t.geography, t.spread].filter(Boolean).join(', ');
      if (meta) tile.append(node('div', meta, 'tile-meta'));
      const about = meaning(t.label, t.meaning);
      if (about) tile.append(about);
      grid.append(tile);
    }
    article.append(grid);
  }
  for (const c of answer.charts) article.append(chartFigure(c));
  for (const c of answer.caveats) article.append(says('p', c, 'caveat'));
  if (answer.sources.length) {
    const years = [...new Set(answer.sources.map(s => s.year))].join(', ');
    const shown = [...answer.tiles.map(t => t.spread), ...answer.charts.flatMap(c => c.bars.map(b => b.text))].join(' ');
    const notes = [`${years}.`];
    if (shown.includes('±')) notes.push('± is the 90% margin of error.');
    if (shown.includes('range ')) notes.push('A range shows the low and high bounds of the estimate.');
    article.append(node('p', notes.join(' '), 'source-line'));
    const details = document.createElement('details');
    details.append(node('summary', 'Sources'));
    const list = document.createElement('ul');
    for (const s of answer.sources) { const li = document.createElement('li'); li.append(link(s.name, s.url), node('span', `, ${s.year}`)); list.append(li); }
    details.append(list);
    article.append(details);
  }
  if (answer.programs && answer.programs.length) article.append(answerPrograms(answer.programs, answer.program_sources));
  if (answer.prepared) article.append(node('p', answer.prepared, 'prepared'));
  return article;
}

// Facts line and source list, read from the data store. Without a store the page keeps its fallbacks.
async function loadAbout() {
  let data;
  try { data = await api('/api/about'); } catch (err) {
    // Say why there is no source list, for example that no data store has been built.
    $('sources-missing').textContent = err.message;
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
      row.append(name, node('td', s.publisher), node('td', s.years));
      body.append(row);
    }
    $('source-wrap').hidden = false;
    $('sources-missing').hidden = true;
  }
  if (data.crosscheck) { $('crosscheck').textContent = data.crosscheck; $('crosscheck').hidden = false; }
}
loadAbout();
// Programs named in an answer; a district lookup marks which ones its figures point to.
function answerPrograms(programs, sources) {
  const details = node('details', '', 'answer-programs');
  details.open = true;
  const matched = programs.filter(p => p.match !== false), others = programs.filter(p => p.match === false);
  const ranked = programs.some(p => 'match' in p);
  details.append(node('summary', ranked ? 'Programs to explore here' : 'Program pages'), ...programList(matched, others, matched.length ? 'Also available statewide' : 'Available statewide'));
  if (sources && sources.length) details.append(profileSources(sources));
  return details;
}
