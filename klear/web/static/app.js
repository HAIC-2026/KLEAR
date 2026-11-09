const $ = id => document.getElementById(id);
let district = null, asked = null, busy = false, queued = null, lookupSeq = 0;
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const uncertaintyLabels = {ok: '90% margin of error shown above', controlled: 'Fixed to official totals; no sampling margin of error', range: 'Estimated range shown above', open_ended: 'Open-ended published value', not_computable: 'Margin of error unavailable; treat as uncertain', not_applicable: 'No margin of error applies', unavailable: 'Uncertainty information unavailable'};
// Starter questions, with the short label each shows once it moves into the chat.
const examples = [['Give me a socioeconomic profile of this district.', 'Profile'], ['How many kūpuna live in this district?', 'Kūpuna'], ['What does the data say about housing needs in this district?', 'Housing'], ['How does poverty here compare with the state?', 'Poverty vs. state'], ['What support programs could residents of this district explore?', 'Programs']];
const refusalLabels = {decline_partisan: 'Partisan request declined', decline_individual: 'Personal request declined', not_available: 'Data not available', out_of_scope: 'Outside KLEAR’s scope'};
function node(tag, text, className) { const n = document.createElement(tag); n.textContent = text; if (className) n.className = className; return n; }
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

function setLookup(open) {
  $('lookup').hidden = !open;
  $('change').setAttribute('aria-expanded', String(open));
  $('change').textContent = open ? 'Cancel' : 'Change district';
  if (!open) { lookupSeq++; $('matches').replaceChildren(); $('lookup-status').textContent = ''; }
}

function needDistrict() {
  setLookup(true);
  $('lookup-status').textContent = 'Pick a district first. Your question will wait in the box below.';
  const bar = $('district-bar');
  bar.classList.remove('nudge'); void bar.offsetWidth; bar.classList.add('nudge');
  $('place').focus();
}

function setBusy(on) {
  busy = on;
  $('send').disabled = on;
  $('examples').querySelectorAll('button').forEach(b => { b.disabled = on; });
}

// Set the district the chat asks about, or queue the switch while an answer loads.
function choose(geo, name, quiet) {
  setLookup(false);
  $('place').value = '';
  if (busy) {
    queued = geo === district ? null : {geo, name};
    $('chat-status').textContent = queued ? `Switching to ${name} after this answer.` : '';
    showChat();
    $('question').focus({preventScroll: true});
    return;
  }
  const switched = geo !== district;
  if (switched) {
    const log = $('messages'), last = log.lastElementChild;
    if (last && last.classList.contains('switch')) last.remove();
    if (asked && geo !== asked) {
      const line = node('p', '', 'switch');
      line.append(node('span', `Now asking about ${name}`));
      log.append(line);
      if (!quiet) log.scrollTop = log.scrollHeight;
    }
    district = geo;
    $('district-title').textContent = name;
    $('district-now').hidden = false;
    document.dispatchEvent(new CustomEvent('klear:district', {detail: {geo, name}}));
    loadProfile(geo);
  }
  const ready = $('question').value.trim() ? 'Your question is ready. Press Ask KLEAR.' : '';
  $('chat-status').textContent = quiet && switched ? `Now asking about ${name}. ${ready}`.trim() : ready;
  if (quiet) return;
  showChat();
  $('question').focus({preventScroll: true});
}

const share = s => s < 0.01 ? '<1%' : `${Math.round(s * 100)}%`;

// One group of district buttons per matched place or ZIP code, largest place first.
function matchGroups(matches) {
  const groups = [];
  for (const m of matches) {
    const rows = [m, ...(m.overlaps || [])].filter(g => /^(house|senate):/.test(g.geo || ''));
    if (!rows.length) continue;
    const county = (m.overlaps || []).find(g => /^county:/.test(g.geo || ''));
    const place = m.kind === 'zcta' ? `ZIP ${m.geo.split(':')[1]}` : m.name;
    const rank = g => (g.geo.startsWith('senate:') ? 1000 : 0) - Math.round((g.share ?? 1) * 100);
    rows.sort((a, b) => rank(a) - rank(b) || a.geo.localeCompare(b.geo));
    groups.push({place, title: m.kind === 'district' ? '' : [place, county && county.name].filter(Boolean).join(', '), pop: m.pop2020 || 0, rows});
  }
  return groups.sort((a, b) => b.pop - a.pop);
}

function showMatches(groups) {
  for (const g of groups) {
    const wrap = node('div', '', 'match-group'), chips = node('div', '', 'chips');
    if (g.title) wrap.append(node('p', g.title, 'overline'));
    for (const r of g.rows) {
      const b = node('button', '', 'match');
      b.type = 'button';
      const label = r.name.replace(' District', '');
      b.append(node('span', label));
      if (r.share != null) {
        b.append(node('span', share(r.share), 'share'));
        b.setAttribute('aria-label', `${label} ${share(r.share)} of ${g.place} residents`);
      }
      b.onclick = () => choose(r.geo, r.name);
      chips.append(b);
    }
    wrap.append(chips);
    $('matches').append(wrap);
  }
}

$('change').onclick = () => {
  const open = $('lookup').hidden;
  // Reopening the search drops a switch queued while an answer loads.
  if (open && queued) { queued = null; $('chat-status').textContent = ''; }
  setLookup(open);
  if (open) $('place').focus();
};
$('lookup').onsubmit = async e => {
  e.preventDefault();
  const seq = ++lookupSeq, button = $('lookup').querySelector('button[type=submit]');
  button.disabled = true;
  $('matches').replaceChildren();
  $('lookup-status').textContent = 'Finding matching districts…';
  try {
    const data = await api('/api/resolve?q=' + encodeURIComponent($('place').value));
    if (seq !== lookupSeq) return;
    const groups = matchGroups(data.matches || []);
    const found = new Map(groups.flatMap(g => g.rows.map(r => [r.geo, r.name])));
    if (found.size === 1) { const [[geo, name]] = found; choose(geo, name); return; }
    const county = (data.matches || []).find(m => m.kind === 'county');
    if (!found.size) {
      $('lookup-status').textContent = county ? `${county.name} is a whole county. Try a town or ZIP code in it, or find your district on the map.` : 'No matching districts. Try House 22, Senate 17, a town, or a Hawaiʻi ZIP code.';
      return;
    }
    $('lookup-status').textContent = groups.some(g => g.title) ? 'Choose a district. Percents show how each area’s residents split across districts.' : 'Choose a district:';
    showMatches(groups);
  } catch (err) {
    if (seq === lookupSeq) $('lookup-status').textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
for (const [question, label] of examples) {
  const b = node('button', question);
  b.type = 'button';
  b.dataset.label = label;
  b.onclick = () => { $('question').value = question; if (district) $('question-form').requestSubmit(); else needDistrict(); };
  $('examples').append(b);
}

// Once a district is chosen, its profile takes the starter questions' place and they move into the chat as short buttons.
let profileSeq = 0;
async function loadProfile(geo) {
  const seq = ++profileSeq, panel = $('profile');
  if (!$('starter').hidden) {
    for (const b of $('examples').children) { b.setAttribute('aria-label', b.textContent); b.textContent = b.dataset.label; }
    $('suggest').append($('examples'));
    $('suggest').hidden = false;
    $('starter').hidden = true;
  }
  panel.hidden = false;
  panel.replaceChildren(node('p', 'Loading the district profile…', 'profile-status'));
  try {
    const data = await api('/api/profile?district=' + encodeURIComponent(geo));
    if (seq === profileSeq) panel.replaceChildren(...renderProfile(data));
  } catch (err) {
    if (seq === profileSeq) panel.replaceChildren(node('p', `The district profile could not load. ${err.message}`, 'profile-status'));
  }
}

function profileRow(r) {
  const row = node('div', '', 'profile-row');
  row.title = r.title;
  const figure = node('p', '', 'profile-figure');
  figure.append(node('span', r.value, 'num'));
  if (r.spread) figure.append(' ', node('span', r.spread, 'moe'));
  const label = node('p', r.label, 'profile-label');
  if (r.note) { const sup = node('sup', String(r.note)); sup.setAttribute('aria-label', `source ${r.note}`); label.append(sup); }
  row.append(figure, label);
  if (r.caution) row.append(node('p', r.caution, 'profile-caution'));
  if (r.against.length) {
    const list = node('ul', '', 'profile-against');
    for (const a of r.against) { const li = document.createElement('li'); li.append(node('b', a.lead), ` ${a.rest}`); list.append(li); }
    row.append(list);
  }
  return row;
}

// The section's citations, numbered when its figures come from more than one source.
function profileSources(sources) {
  const box = node('div', '', 'profile-sources');
  box.append(node('p', sources.length > 1 ? 'Sources' : 'Source', 'profile-sources-title'));
  const list = node(sources.length > 1 ? 'ol' : 'ul', '');
  for (const s of sources) { const li = document.createElement('li'); li.append(link(s.name, s.url), `, ${s.year}`); list.append(li); }
  box.append(list);
  return box;
}

function renderProfile(p) {
  const title = node('h3', p.district);
  title.id = 'profile-title';
  const out = [node('p', 'DISTRICT PROFILE', 'overline'), title];
  if (p.where) out.push(node('p', p.where, 'profile-where'));
  if (p.places.length) {
    const more = p.places.length - 4;
    out.push(node('p', 'Includes ' + p.places.slice(0, 4).map(x => `${x.name} (${x.share})`).join(', ') + (more > 0 ? `, and ${more} more places` : ''), 'profile-meta'));
  }
  if (p.overlaps.length) out.push(node('p', 'Overlaps ' + p.overlaps.map(o => `${o.name} (${o.share})`).join(', '), 'profile-meta'));
  const stats = node('dl', '', 'profile-stats');
  for (const h of p.headline) { const s = node('div', ''); s.append(node('dd', h.value), node('dt', h.label)); stats.append(s); }
  out.push(stats);
  for (const sec of p.sections) {
    if (!sec.rows.length) continue;
    const d = node('details', '', 'profile-section');
    d.append(node('summary', sec.title), ...sec.rows.map(profileRow), profileSources(sec.sources));
    out.push(d);
  }
  return out;
}

$('question-form').onsubmit = async e => {
  e.preventDefault();
  if (busy) return;
  const question = $('question').value.trim();
  if (!question) return;
  if (!district) { needDistrict(); return; }
  setBusy(true);
  asked = district;
  const name = $('district-title').textContent;
  const sent = node('p', question, 'user'), pending = node('p', 'Checking district data and sources…', 'pending');
  $('messages').append(sent, pending);
  reveal(sent);
  $('question').value = '';
  $('chat-status').textContent = '';
  let follow = true, hold = null;
  try {
    const answer = await api('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question, district})});
    hold = holdPage(pending);
    follow = onScreen(pending);
    pending.replaceWith(renderAnswer(answer, name));
  } catch (err) {
    hold = holdPage(pending);
    follow = onScreen(pending);
    pending.replaceWith(node('p', err.message, 'error'));
    if (!$('question').value.trim()) { $('question').value = question; $('chat-status').textContent = 'Your question is ready to retry.'; }
  } finally {
    setBusy(false);
    // Leave the page and focus alone while the reader works in the district search or another field.
    const focus = document.activeElement;
    const elsewhere = !$('lookup').hidden || (focus && focus !== $('question') && focus.matches('input, select, textarea'));
    const idle = !focus || focus === document.body;
    const inChat = !elsewhere && ($('chat-card').contains(focus) || $('examples').contains(focus) || (idle && onScreen($('question-form'))));
    if (queued) { const next = queued; queued = null; choose(next.geo, next.name, true); }
    if (hold) hold();
    if (follow && (logScrolls() || (!elsewhere && !(hold && hold.field)))) reveal(sent);
    if (inChat) $('question').focus({preventScroll: true});
  }
};

// Answer heading that names the district the question was asked about.
function heading(answer, name) {
  const h = node('h2', refusalLabels[answer.refusal] || 'KLEAR');
  if (name) h.append(' ', node('span', name, 'answer-for'));
  return h;
}

function basicAnswer(answer, name) { const article = document.createElement('article'); article.append(heading(answer, name)); for (const p of answer.text.split('\n\n')) article.append(node('p', p)); if (answer.facts.length) { const details = document.createElement('details'); details.open = true; details.append(node('summary', 'Figures & sources')); for (const f of answer.facts) { const card = node('div', '', 'fact'); card.append(node('b', f.label), node('p', f.value), node('small', `${f.year}, ${f.geography}. Uncertainty: ${uncertaintyLabels[f.uncertainty] || 'Uncertainty information unavailable'}`)); let source = node('span', f.source); try { const url = new URL(f.url); if (['http:', 'https:'].includes(url.protocol)) { source = node('a', f.source + ' ↗'); source.href = url.href; source.target = '_blank'; source.rel = 'noopener noreferrer'; } } catch {} card.append(source); details.append(card); } article.append(details); } if (answer.programs && answer.programs.length) { const details = document.createElement('details'); details.open = true; details.append(node('summary', 'Official program guidance')); for (const p of answer.programs) { const card = node('div', '', 'fact'); const link = node('a', p.name + ' ↗'); try { const url = new URL(p.url); if (['http:', 'https:'].includes(url.protocol)) { link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link); } else card.append(node('span', p.name)); } catch { card.append(node('span', p.name)); } details.append(card); } article.append(details); }  return article; }

function link(text, href) {
  try { const url = new URL(href); if (['http:', 'https:'].includes(url.protocol)) { const a = node('a', text + ' ↗'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; } } catch {}
  return node('span', text);
}

function chartFigure(c) {
  const fig = node('figure', '', 'chart');
  if (c.bars.some(b => b.text.length > 22)) fig.classList.add('wide');
  const head = node('figcaption', '', 'chart-head');
  head.append(node('span', c.label));
  if (c.tag) head.append(node('span', c.tag, c.lower ? 'chip strong' : 'chip'));
  fig.append(head);
  const max = Math.max(...c.bars.map(b => b.high)) || 1;
  c.bars.forEach((b, i) => {
    const row = node('div', '', 'bar-row');
    row.append(node('span', b.geography, 'bar-place'));
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
    fig.append(row);
  });
  return fig;
}

const ruler = document.createElement('canvas').getContext('2d');
function ems(text, weight) { ruler.font = `${weight} 100px Inter, system-ui, sans-serif`; return ruler.measureText(text).width / 100; }

// Big tile number after a small 'about' for estimates. --ems and --about are their widths in em, so the CSS can shrink long numbers to fit.
function tileValue(text) {
  const [, about, figure] = text.match(/^(about )?([\s\S]*)$/), value = node('div', '', 'tile-value'), num = node('span', figure, 'tile-num');
  if (about) {
    value.append(node('span', 'about ', 'tile-about'));
    value.style.setProperty('--about', ems('about ', 600).toFixed(3));
  }
  value.append(num);
  if (/\d/.test(figure)) num.style.setProperty('--ems', (ems(figure, 500) - 0.02 * figure.length).toFixed(3));
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
    if (!item) { list = null; if (line.trim()) article.append(node('p', line, 'lead')); continue; }
    if (!list) article.append(list = node('ul', '', 'lead-list'));
    list.append(node('li', item[1]));
  }
  if (answer.tiles.length) {
    const grid = node('div', '', 'tiles');
    for (const t of answer.tiles) {
      const tile = node('div', '', 'tile');
      tile.append(tileValue(t.value), node('div', t.label, 'tile-label'));
      const meta = [t.geography, t.spread].filter(Boolean).join(', ');
      if (meta) tile.append(node('div', meta, 'tile-meta'));
      grid.append(tile);
    }
    article.append(grid);
  }
  for (const c of answer.charts) article.append(chartFigure(c));
  for (const c of answer.caveats) article.append(node('p', c, 'caveat'));
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
  if (answer.programs && answer.programs.length) {
    const details = document.createElement('details');
    details.open = true;
    details.append(node('summary', 'Official program guidance'));
    const list = document.createElement('ul');
    for (const p of answer.programs) { const li = document.createElement('li'); li.append(link(p.name, p.url)); list.append(li); }
    details.append(list);
    article.append(details);
  }
  return article;
}
