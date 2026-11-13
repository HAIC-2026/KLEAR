// Where outreach could help most: one program's group across House or Senate districts, in tiers whose ranges
// overlap, from /api/outreach. The program and chamber live in the address, so a link opens the same view.
const $ = id => document.getElementById(id);
function node(tag, text, className) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; }
const hidden = text => node('span', text, 'visually-hidden');
// Hawaiian words for groups of people, marked so screen readers can pronounce them.
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

// A link to another site opens in a new tab and says so. A path on this site opens in the same tab.
function link(text, href) {
  if (!href) return node('span', text);
  const url = new URL(href, location.href);
  if (!['http:', 'https:'].includes(url.protocol)) return node('span', text);
  const a = says('a', text);
  a.href = url.href;
  if (url.origin !== location.origin) {
    a.className = 'ext';
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.append(hidden(' (opens in a new tab)'));
  }
  return a;
}

function figure(value, spread, className) {
  const p = node('span', '', className);
  p.append(node('span', value, 'num'));
  if (spread) p.append(' ', node('span', spread, 'moe'));
  return p;
}

// What the group's label stands for, one tap away, as on the district profile.
function meaning(label, m) {
  if (!m || !(m.plain || m.universe)) return null;
  const box = node('details', '', 'meaning'), summary = node('summary', 'What this means');
  summary.append(hidden(`: ${label}`));
  box.append(summary);
  if (m.plain) box.append(says('p', m.plain));
  if (m.universe) { const p = node('p', ''); p.append(node('b', 'Who is counted: '), m.universe); box.append(p); }
  return box;
}

// One program: its name linked to the page to apply or learn more, the agency, and how to call.
function programCard(p) {
  const card = node('div', '', 'program');
  const name = node('p', '', 'program-name');
  name.append(link(p.name, p.url));
  card.append(name, node('p', p.agency, 'program-agency'));
  if (p.description) card.append(says('p', p.description, 'program-about'));
  const contact = node('p', '', 'program-contact'), tel = node('a', p.phone);
  tel.href = 'tel:+' + (p.phone.startsWith('1-') ? '' : '1') + p.phone.replace(/\D/g, '');
  contact.append('Call ', tel, p.phone_label ? `, ${p.phone_label}.` : '.', ` Details last verified ${p.verified}.`);
  card.append(contact);
  return card;
}

function summary(d) {
  const out = [says('p', d.lead, 'outreach-lead')];
  if (d.year) out.push(node('p', `${d.year}. ± is the 90% margin of error.`, 'outreach-year'));
  const facts = node('ul', '', 'outreach-facts');
  if (d.state) {
    const li = node('li', `${d.state.name} in all: `);
    li.append(figure(d.state.value, d.state.spread), '.');
    facts.append(li);
  }
  if (d.unreliable) facts.append(node('li', d.unreliable, 'outreach-caution'));
  if (facts.children.length) out.push(facts);
  const about = meaning(d.label, d.meaning);
  if (about) out.push(about);
  const caveat = node('div', '', 'outreach-caveat');
  if (d.caveat) caveat.append(says('p', d.caveat));
  caveat.append(node('p', d.note));
  out.push(caveat);
  if (d.programs.length) {
    const box = node('div', '', 'outreach-programs');
    box.append(node('h3', d.programs.length > 1 ? 'Programs for this group' : 'Program for this group'), ...d.programs.map(programCard));
    out.push(box);
  }
  return out;
}

// A tier's districts in district order, each linked to its profile, with its figure and the program-use figure.
function tierTable(t, d) {
  const scroll = node('div', '', 'table-scroll'), table = node('table', '', 'tier-table');
  table.append(says('caption', `${t.title}: ${d.label}`, 'visually-hidden'));
  const head = table.createTHead().insertRow();
  for (const text of ['District', d.label, ...(d.use ? [d.use.label] : [])]) { const th = says('th', text); th.scope = 'col'; head.append(th); }
  const body = table.createTBody();
  for (const r of t.districts) {
    const tr = body.insertRow(), name = node('th', '');
    name.scope = 'row';
    name.append(link(r.name, r.path));
    // On a phone the header row is hidden, so each figure names its column.
    const cell = node('td', '');
    cell.dataset.label = d.label;
    cell.append(figure(r.value, r.spread));
    if (r.caution) {
      const flag = node('span', '', 'outreach-flag');
      flag.append(node('span', 'Use with caution', 'chip caution'), ` ${r.caution}`);
      cell.append(flag);
    }
    tr.append(name, cell);
    if (d.use) {
      const use = node('td', '', 'use');
      use.dataset.label = d.use.label;
      if (r.use) use.append(figure(r.use.value, r.use.spread)); else use.textContent = 'No figure';
      tr.append(use);
    }
  }
  scroll.append(table);
  return scroll;
}

function tiers(d) {
  const out = [];
  if (d.use) {
    const note = node('p', '', 'use-note');
    note.append(node('b', `${d.use.label}. `), d.use.note);
    out.push(note);
  }
  d.tiers.forEach((t, i) => {
    const count = `${t.districts.length} ${t.districts.length === 1 ? 'district' : 'districts'}`;
    if (i === 0) {
      const top = node('section', '', 'tier top');
      top.setAttribute('aria-labelledby', 'tier-top');
      const h = node('h3', `${t.title}: ${count}`);
      h.id = 'tier-top';
      top.append(h, node('p', t.note, 'tier-note'), tierTable(t, d));
      out.push(top);
      if (d.tiers.length > 1) out.push(node('h3', 'Other groups', 'tier-others'));
      return;
    }
    const box = node('details', '', 'tier');
    box.append(node('summary', `${t.title}: ${count}`), node('p', t.note, 'tier-note'), tierTable(t, d));
    out.push(box);
  });
  // Districts the groups leave out: a figure with no margin of error, or no figure at all.
  const left = [...d.unranked.map(r => [r, `${r.value}, ${r.reason}`]), ...d.missing.map(m => [m, `no figure, ${m.reason}`])];
  if (left.length) {
    const box = node('details', '', 'tier'), list = node('ul', '', 'tier-left');
    for (const [r, why] of left) { const li = node('li', ''); li.append(link(r.name, r.path), `: ${why}`); list.append(li); }
    box.append(node('summary', `Not in a group: ${left.length} ${left.length === 1 ? 'district' : 'districts'}`), list);
    out.push(box);
  }
  return out;
}

function sources(d) {
  const box = node('div', '', 'outreach-sources'), list = node('ul', '');
  box.append(node('p', d.sources.length > 1 ? 'Sources' : 'Source', 'outreach-sources-title'));
  for (const s of d.sources) {
    const li = node('li', '');
    li.append(link(s.name, s.url), node('span', s.year, 'source-year'));
    // Where figures KLEAR moved to districts are explained, on a line of its own.
    if (s.links && s.links.length) {
      const more = node('span', '', 'source-links');
      s.links.forEach((l, i) => more.append(i ? ', ' : '', link(l.text, l.url)));
      li.append(more);
    }
    list.append(li);
  }
  box.append(list);
  return box;
}

function render(d) {
  $('main').dataset.chamber = d.chamber;
  $('result-title').replaceChildren(says('span', d.heading));
  $('summary').replaceChildren(...summary(d));
  $('tiers').replaceChildren(...tiers(d));
  $('sources').replaceChildren(sources(d));
  $('result').hidden = false;
}

const picked = name => { const r = document.querySelector(`#choose input[name=${name}]:checked`); return r ? r.value : ''; };
let seq = 0;
async function load() {
  const pair = picked('pair'), chamber = picked('chamber'), mine = ++seq;
  try { history.replaceState(null, '', `/outreach.html?pair=${pair}&chamber=${chamber}`); } catch { /* history unavailable */ }
  $('status').textContent = 'Loading the districts…';
  $('live').textContent = '';
  let r, data = null;
  try {
    r = await fetch(`/api/outreach?pair=${encodeURIComponent(pair)}&chamber=${encodeURIComponent(chamber)}`);
    data = await r.json();
  } catch { /* shown below */ }
  if (mine !== seq) return;
  if (!r || !r.ok || !data || !Array.isArray(data.tiers)) {
    $('result').hidden = true;
    $('status').textContent = (data && data.error) || 'KLEAR could not load the districts. Check your connection and try again.';
    return;
  }
  render(data);
  $('status').textContent = '';
  $('live').textContent = `Showing ${data.heading}.`;
}

// Open the program and chamber the address names, when they are ones the page offers.
const params = new URLSearchParams(location.search);
for (const name of ['pair', 'chamber']) {
  const want = params.get(name), input = want && [...document.querySelectorAll(`#choose input[name=${name}]`)].find(i => i.value === want);
  if (input) input.checked = true;
}
$('choose').addEventListener('change', load);
$('choose').addEventListener('submit', e => e.preventDefault());
load();
