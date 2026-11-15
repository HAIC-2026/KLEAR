// The Checks section of the data and methods page: the latest build's checks, eval results and limits from
// /api/checks. Its own scope, so its helpers do not meet data.js's.
(() => {
const $ = id => document.getElementById(id);
const REPO = 'https://github.com/HAIC-2026/KLEAR';
const RESULTS = {PASS: ['Passed', 'pass'], FAIL: ['Failed', 'fail'], SKIPPED: ['Did not run', 'skip']};
// Checks with their own section above the details list.
const FEATURED = {population: 'cks-populations', apportionment: 'cks-accuracy'};

function node(tag, text, className) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; }

function chip(result) {
  const [text, kind] = RESULTS[result] || [result, 'skip'];
  return node('span', text, `result ${kind}`);
}

function table(t, caption) {
  const wrap = node('div', '', 'table-block'), scroll = node('div', '', 'table-scroll'), tab = node('table', '', 'data-table');
  if (caption) wrap.append(node('h4', t.title, 'table-title'));
  if (t.columns.length > 4) wrap.append(node('p', 'Swipe the table sideways to see every column.', 'scroll-hint'));
  const cap = node('caption', t.title, 'visually-hidden'), head = node('thead'), hr = node('tr'), body = node('tbody');
  for (const c of t.columns) { const th = node('th', c.label, c.align === 'right' ? 'num' : ''); th.scope = 'col'; hr.append(th); }
  head.append(hr);
  for (const row of t.rows) {
    const tr = node('tr');
    row.forEach((v, i) => {
      const cell = node(i ? 'td' : 'th', v, t.columns[i].align === 'right' ? 'num' : '');
      if (!i) cell.scope = 'row';
      if (v === 'NO') cell.classList.add('bad');
      tr.append(cell);
    });
    body.append(tr);
  }
  tab.append(cap, head, body);
  scroll.append(tab);
  wrap.append(scroll);
  if (t.note) wrap.append(node('p', t.note, 'table-note'));
  return wrap;
}

function fillList(list, items) {
  for (const text of items) list.append(node('li', /[.!?]$/.test(text) ? text : `${text}.`));
}

function renderTable(checks) {
  const body = $('check-table').tBodies[0];
  for (const c of checks) {
    const tr = node('tr'), name = node('th'), link = node('a', c.title);
    name.scope = 'row';
    link.href = `#check-${c.name}`;
    link.onclick = () => openCheck(c.name);
    name.append(link);
    const result = node('td', '', 'result-cell');
    result.append(chip(c.result));
    tr.append(name, node('td', c.gate ? 'Required' : 'Reported', 'type-cell'), result, node('td', c.summary, 'found-cell'));
    body.append(tr);
  }
  $('cks-list').hidden = false;
}

function renderPopulation(c) {
  if (!c) return;
  fillList($('population-notes'), c.notes);
  for (const t of c.tables) {
    const d = node('details', '', 'table-toggle');
    d.append(node('summary', `Show all ${t.rows.length} districts`), table(t, false));
    $('population-table').append(d);
  }
  $('cks-populations').hidden = false;
}

function renderAccuracy(c) {
  if (!c || !c.tables.length) return;
  for (const t of c.tables) $('accuracy-table').append(table(t, false));
  $('cks-accuracy').hidden = false;
}

function renderAnswers(ev) {
  if (!ev || !ev.tables || !ev.tables.length) return;
  for (const t of ev.tables) $('answer-tables').append(table(t, true));
  const notes = [], practice = ev.practice || {}, held = ev.heldout || {};
  if (practice.error) notes.push(`The practice set was not scored for this build: ${practice.error}`);
  else {
    const failed = practice.failed || [];
    notes.push(`Practice answers were recorded from ${practice.model} on ${practice.recorded_at} and scored again against this build’s data${failed.length ? `. Questions that failed: ${failed.join(', ')}` : ''}`);
  }
  if (held.error) notes.push(`Held-out totals are not available: ${held.error}`);
  else notes.push(`Held-out run: safety reviewer ${held.reviewer}. ${held.source}`);
  fillList($('answer-notes'), notes);
  $('cks-answers').hidden = false;
}

function renderDetails(checks) {
  const box = $('check-details');
  for (const c of checks) {
    const d = node('details', '', 'check-detail'), s = node('summary');
    d.id = `check-${c.name}`;
    s.append(node('span', c.title, 'detail-title'), chip(c.result));
    d.append(s);
    const body = node('div', '', 'detail-body');
    body.append(node('p', `${c.gate ? 'Required check' : 'Reported check, does not stop the build'}. ${c.summary}.`, 'detail-summary'));
    if (c.about) body.append(node('p', c.about));
    if (c.notes.length) {
      body.append(node('p', 'Key numbers', 'detail-label'));
      const list = node('ul', '', 'key-list');
      fillList(list, c.notes);
      body.append(list);
    }
    if (FEATURED[c.name] && c.tables.length) {
      const p = node('p', 'Its table is shown in ', 'detail-label'), a = node('a', 'the section above');
      a.href = `#${FEATURED[c.name]}`;
      p.append(a, '.');
      body.append(p);
    } else for (const t of c.tables) body.append(table(t, true));
    d.append(body);
    box.append(d);
  }
  $('cks-what').hidden = false;
}

function openCheck(name) {
  const d = document.getElementById(`check-${name}`);
  if (d) d.open = true;
}

function renderLimits(data) {
  const items = [...(data.caveats || []), ...((data.eval || {}).limits || [])];
  if (!items.length) return;
  fillList($('limit-list'), items);
  $('cks-limits').hidden = false;
}

async function load() {
  let data;
  try {
    const r = await fetch('/api/checks');
    data = await r.json();
    if (!r.ok) throw new Error(data && data.error ? data.error : `The check results could not load (error ${r.status}).`);
  } catch (err) {
    const msg = err instanceof SyntaxError || err instanceof TypeError ? 'The check results could not load. Please try again.' : err.message;
    $('checks-loading').hidden = true;
    $('checks-status').textContent = msg;
    return;
  }
  const checks = data.checks || [];
  $('checks-loading').hidden = true;
  renderTable(checks);
  renderPopulation(checks.find(c => c.name === 'population'));
  renderAccuracy(checks.find(c => c.name === 'apportionment'));
  renderAnswers(data.eval);
  renderDetails(checks);
  renderLimits(data);
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith('check-')) { openCheck(hash.slice(6)); const el = document.getElementById(hash); if (el) el.scrollIntoView(); }
}
window.addEventListener('hashchange', () => { const h = location.hash.slice(1); if (h.startsWith('check-')) openCheck(h.slice(6)); });
load();
})();
