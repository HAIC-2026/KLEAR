const $ = id => document.getElementById(id);
let district = null;
let suggestedQuestion = null;
let busy = false;
const uncertaintyLabels = {ok: '90% margin of error shown above', controlled: 'Fixed to official totals; no sampling margin of error', range: 'Estimated range shown above', open_ended: 'Open-ended published value', not_computable: 'Margin of error unavailable; treat as uncertain', not_applicable: 'No margin of error applies', unavailable: 'Uncertainty information unavailable'};
const examples = ['Give me a socioeconomic profile of this district.', 'How many kūpuna live in this district?', 'How does poverty here compare with the state?', 'What housing support programs could residents explore?'];
function node(tag, text, className) { const n = document.createElement(tag); n.textContent = text; if (className) n.className = className; return n; }
async function api(url, options) { const r = await fetch(url, options); const data = await r.json(); if (!r.ok) throw new Error(data.error || 'Something went wrong. Please try again.'); return data; }
function choose(geo, name) { district = geo; $('landing').hidden = true; $('chat').hidden = false; $('district-title').textContent = name; $('messages').replaceChildren(); $('question').value = suggestedQuestion || ''; suggestedQuestion = null; $('chat-status').textContent = ''; window.scrollTo({top: 0, behavior: 'instant'}); $('question').focus(); }
$('change').onclick = () => { if (busy) return; $('chat').hidden = true; $('landing').hidden = false; $('district-search').scrollIntoView({behavior: 'smooth'}); $('place').focus(); };
$('lookup').onsubmit = async e => { e.preventDefault(); const button = e.submitter; button.disabled = true; $('matches').replaceChildren(); $('lookup-status').textContent = 'Finding matching districts…'; try { const data = await api('/api/resolve?q=' + encodeURIComponent($('place').value)); const found = new Map(); for (const match of data.matches || []) { for (const g of [match, ...(match.overlaps || [])]) { if (/^(house|senate):/.test(g.geo || '')) found.set(g.geo, g.name); } } $('lookup-status').textContent = found.size ? 'Choose a district to begin:' : 'No matching districts. Try House 22, Senate 17, a town, or a Hawaiʻi ZIP code.'; for (const [geo, name] of found) { const b = node('button', name, 'match'); b.onclick = () => choose(geo, name); $('matches').append(b); } } catch (err) { $('lookup-status').textContent = err.message; } finally { button.disabled = false; } };
for (const question of examples) { const b = node('button', question); b.onclick = () => { $('question').value = question; $('question-form').requestSubmit(); }; $('examples').append(b); }
const refusalLabels = {decline_partisan: 'Question declined · partisan request', decline_individual: 'Question declined · personal request', not_available: 'Data not available', out_of_scope: 'Outside KLEAR’s scope'};
$('question-form').onsubmit = async e => { e.preventDefault(); if (busy || !district) return; const question = $('question').value.trim(); if (!question) return; busy = true; $('send').disabled = true; $('change').disabled = true; $('examples').querySelectorAll('button').forEach(b => b.disabled = true); $('messages').append(node('p', question, 'user')); $('question').value = suggestedQuestion || ''; suggestedQuestion = null; $('chat-status').textContent = 'Checking district data and sources…'; $('messages').setAttribute('aria-busy', 'true'); try { const answer = await api('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question, district})}); $('messages').append(renderAnswer(answer)); $('chat-status').textContent = ''; } catch (err) { $('messages').append(node('p', err.message, 'error')); $('question').value = question; $('chat-status').textContent = 'Your question is ready to retry.'; } finally { busy = false; $('send').disabled = false; $('change').disabled = false; $('examples').querySelectorAll('button').forEach(b => b.disabled = false); $('messages').setAttribute('aria-busy', 'false'); $('question').focus(); } };

for (const button of document.querySelectorAll('.topic')) { button.addEventListener('click', () => { suggestedQuestion = button.dataset.question; $('district-search').scrollIntoView({behavior: 'smooth'}); $('place').focus({preventScroll: true}); }); }

function basicAnswer(answer) { const article = document.createElement('article'); article.append(node('h2', refusalLabels[answer.refusal] || 'KLEAR')); for (const p of answer.text.split('\n\n')) article.append(node('p', p)); if (answer.facts.length) { const details = document.createElement('details'); details.open = true; details.append(node('summary', 'Figures & sources')); for (const f of answer.facts) { const card = node('div', '', 'fact'); card.append(node('b', f.label), node('p', f.value), node('small', `${f.year} · ${f.geography} · Uncertainty: ${uncertaintyLabels[f.uncertainty] || 'Uncertainty information unavailable'}`)); let source = node('span', f.source); try { const url = new URL(f.url); if (['http:', 'https:'].includes(url.protocol)) { source = node('a', f.source + ' ↗'); source.href = url.href; source.target = '_blank'; source.rel = 'noopener noreferrer'; } } catch {} card.append(source); details.append(card); } article.append(details); } if (answer.programs && answer.programs.length) { const details = document.createElement('details'); details.open = true; details.append(node('summary', 'Official program guidance')); for (const p of answer.programs) { const card = node('div', '', 'fact'); const link = node('a', p.name + ' ↗'); try { const url = new URL(p.url); if (['http:', 'https:'].includes(url.protocol)) { link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link); } else card.append(node('span', p.name)); } catch { card.append(node('span', p.name)); } details.append(card); } article.append(details); }  return article; }

function link(text, href) {
  try { const url = new URL(href); if (['http:', 'https:'].includes(url.protocol)) { const a = node('a', text + ' ↗'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; } } catch {}
  return node('span', text);
}

function chartFigure(c) {
  const fig = node('figure', '', 'chart');
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
    const whisker = node('div', '', 'whisker');
    whisker.style.left = (b.low / max * 100) + '%';
    whisker.style.width = ((b.high - b.low) / max * 100) + '%';
    track.append(bar, whisker);
    row.append(track, node('span', b.text, 'bar-value'));
    fig.append(row);
  });
  return fig;
}

function renderAnswer(answer) {
  if (!answer.lead) return basicAnswer(answer);
  const article = document.createElement('article');
  article.className = 'answer';
  article.append(node('h2', refusalLabels[answer.refusal] || 'KLEAR'));
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
      tile.append(node('div', t.value, 'tile-value'), node('div', t.label, 'tile-label'));
      const meta = [t.geography, t.spread].filter(Boolean).join(' · ');
      if (meta) tile.append(node('div', meta, 'tile-meta'));
      grid.append(tile);
    }
    article.append(grid);
  }
  for (const c of answer.charts) article.append(chartFigure(c));
  for (const c of answer.caveats) article.append(node('p', c, 'caveat'));
  if (answer.sources.length) {
    const years = [...new Set(answer.sources.map(s => s.year))].join(', ');
    article.append(node('p', `${years}. ± is the 90% margin of error.`, 'source-line'));
    const details = document.createElement('details');
    details.append(node('summary', 'Sources'));
    const list = document.createElement('ul');
    for (const s of answer.sources) { const li = document.createElement('li'); li.append(link(s.name, s.url), node('span', ` · ${s.year}`)); list.append(li); }
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
