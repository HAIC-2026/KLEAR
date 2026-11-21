// Compare: two districts side by side, with an optional chat panel.
// Each column is a search until a district is chosen there, then the district's name, where it is, its legislator and
// a small map. Under the columns the two districts' figures run row by row: each measure's name and Hawaiʻi's figure
// across both columns, each district's figure under its own name, and whether the two differ beyond their margins of
// error. The districts live in the address (/compare?d=house:022,house:023), so a link, Back and refresh keep them.
(() => {
  const root = $('compare-view');
  if (!root) return;
  const SLOTS = ['a', 'b'], chosen = {a: null, b: null}, loaded = {a: null, b: null};
  const normal = geo => { const m = /^(house|senate):(\d{1,3})$/.exec(geo || ''); return m ? `${m[1]}:${m[2].padStart(3, '0')}` : null; };
  const valid = geo => { const m = /^(house|senate):(\d{3})$/.exec(geo || ''); return Boolean(m) && +m[2] >= 1 && +m[2] <= (m[1] === 'house' ? 51 : 25); };

  const cols = node('div', '', 'cmp-cols'), live = node('p', '', 'visually-hidden'), result = node('div', '', 'cmp-result');
  live.id = 'cmp-live';
  live.setAttribute('role', 'status');
  result.id = 'cmp-result';
  const colOf = {};
  for (const slot of SLOTS) {
    colOf[slot] = node('div', '', `cmp-col cmp-col-${slot}`);
    colOf[slot].dataset.slot = slot;
    colOf[slot].tabIndex = -1;
    colOf[slot].setAttribute('role', 'group');
    colOf[slot].setAttribute('aria-label', `District ${slot.toUpperCase()}`);
    cols.append(colOf[slot]);
  }
  root.append(cols, live, result);
  // The House or Senate switch in the bar over the columns, for the numbers in whichever column is still choosing. It
  // shows only while one is (style.css).
  const barSwitch = $('chamber-switch').cloneNode(true);
  barSwitch.removeAttribute('id');
  $('stage-bar').prepend(barSwitch);

  const pickTitle = slot => (chosen[slot === 'a' ? 'b' : 'a'] ? 'Choose a district to compare' : slot === 'a' ? 'Choose a district' : 'Choose a second district');

  // A column with no district: the same search and numbers as the Find page, for the chamber the House or Senate
  // switch in the bar over the columns shows; the switch is there only while a column is choosing. The numbers are a copy of the Find page's, so a click fills this column instead of opening the
  // district's page.
  const resets = {};
  function picker(slot) {
    const box = node('div', '', 'cmp-pick'), id = `cmp-${slot}`;
    const heading = node('h2', pickTitle(slot), 'cmp-pick-title');
    heading.tabIndex = -1;
    box.append(heading);
    const form = node('form', '', 'find cmp-find');
    form.id = `${id}-find`;
    const label = node('label', 'Town, ZIP code, street address, district or legislator');
    label.htmlFor = `${id}-place`;
    const row = node('div', '', 'inputrow'), combo = node('div', '', 'combo'), input = node('input', '');
    Object.assign(input, {id: `${id}-place`, required: true, maxLength: 160, placeholder: 'Hilo, 96744 or Senate 12', autocomplete: 'off'});
    for (const [k, v] of Object.entries({role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': `${id}-place-list`})) input.setAttribute(k, v);
    const list = node('ul', '', 'suggestions');
    list.id = `${id}-place-list`;
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Suggestions');
    list.hidden = true;
    const button = node('button', 'Find district');
    button.type = 'submit';
    combo.append(input, list);
    row.append(combo, button);
    const count = node('p', '', 'visually-hidden'), status = node('p', '', 'status'), matches = node('div', '', 'cmp-matches');
    count.id = `${id}-place-count`;
    count.setAttribute('role', 'status');
    status.setAttribute('role', 'status');
    const privacy = $('find').querySelector('.privacy').cloneNode(true);
    privacy.id = `${id}-privacy`;
    input.setAttribute('aria-describedby', privacy.id);
    form.append(label, row, privacy, count, status, matches);
    const seats = $('seat-index').cloneNode(true);
    seats.removeAttribute('id');
    seats.classList.add('cmp-seats');
    seats.dataset.seats = $('find-section').dataset.seats || 'house';
    // The copy's headings need ids of their own for the groups they name.
    for (const n of seats.querySelectorAll('[id]')) n.id = `${id}-${n.id}`;
    for (const attr of ['aria-labelledby', 'aria-describedby']) {
      for (const n of [seats, ...seats.querySelectorAll(`[${attr}]`)]) if (n.hasAttribute(attr)) n.setAttribute(attr, `${id}-${n.getAttribute(attr)}`);
    }
    box.append(form, seats);
    colOf[slot].replaceChildren(box);
    if (resets[slot]) resets[slot]();
    resets[slot] = lookupForm(form, input, status, matches, (geo, name) => set(slot, geo, name, true));
    seatLinks(seats, (geo, name) => set(slot, geo, name, true));
  }

  // A and B: a gold badge on each district's card and over its column of figures, so two districts of one chamber,
  // the same color, are easy to tell apart. Their names say which is which to screen readers.
  function slotBadge(slot) {
    const b = node('span', slot.toUpperCase(), 'cmp-badge');
    b.setAttribute('aria-hidden', 'true');
    return b;
  }

  // A column with a district: its chamber and number, where it is, its legislator and a small map, with buttons to
  // change or remove it. Its name opens its page.
  function card(slot, p) {
    const box = node('div', '', `cmp-card ${p.geo.split(':')[0]}`), head = node('div', '', 'cmp-card-head');
    const h = node('h2', '', 'cmp-name'), a = node('a', '');
    h.append(slotBadge(slot));
    const m = /^(House|Senate) District (\d+)$/.exec(p.district);
    if (m) a.append(node('span', `${m[1]} District`, 'mast-chamber'), ' ', node('span', m[2], 'mast-number'));
    else a.textContent = p.district;
    a.href = districtPath(p.geo);
    a.addEventListener('click', e => { if (!plainClick(e)) return; e.preventDefault(); transition(() => choose(p.geo, p.district, false, true, 'profile')); });
    h.append(a);
    const facts = node('div', '', 'cmp-facts');
    if (p.where) facts.append(node('p', p.where, 'district-where'));
    facts.append(...districtGeography(p).map(line => node('p', line, 'district-meta')));
    for (const mem of p.members || []) {
      const who = node('p', '', 'profile-member-name');
      who.append(node('span', mem.title, 'profile-member-title'), ' ', link(mem.reads || mem.name, mem.url));
      facts.append(who);
    }
    const mapBox = node('div', '', 'cmp-map');
    locatorMap(p.geo).then(svg => { if (svg && chosen[slot] === p.geo) mapBox.replaceChildren(svg); });
    const actions = node('div', '', 'cmp-card-actions');
    actions.append(changeButton(slot, p.district));
    head.append(h, actions);
    box.append(head, mapBox, facts);
    colOf[slot].replaceChildren(box);
  }

  // A column while its district loads: the district's name, then gray bars where its map and facts will be.
  function columnLoading(geo) {
    const box = node('div', '', 'cmp-card');
    box.append(node('p', `Loading ${districtName(geo)}…`, 'profile-status'),
      skeleton([], 'cmp-map skeleton-bar'), skeleton(['w-80', 'w-60'], 'cmp-facts'));
    return box;
  }

  function focusPicker(slot) {
    colOf[slot].querySelector(coarsePointer.matches ? '.cmp-pick-title' : 'input').focus();
  }

  function changeButton(slot, name) {
    const change = node('button', 'Change', 'textbutton');
    change.type = 'button';
    change.append(hidden(` ${name}`));
    change.onclick = () => { set(slot, null); focusPicker(slot); };
    return change;
  }

  function update() {
    const list = SLOTS.filter(s => chosen[s]).map(s => chosen[s]);
    document.documentElement.classList.toggle('cmp-choosing', list.length < SLOTS.length);
    if (document.documentElement.dataset.page === 'compare') {
      try { history.replaceState(null, '', list.length ? `/compare?d=${list.join(',')}` : '/compare'); } catch { /* history unavailable */ }
    }
    for (const slot of SLOTS) {
      if (!chosen[slot]) {
        if (!colOf[slot].querySelector('.cmp-pick')) picker(slot);
        else colOf[slot].querySelector('.cmp-pick-title').textContent = pickTitle(slot);
      } else if (!(loaded[slot] && loaded[slot].geo === chosen[slot])) {
        colOf[slot].replaceChildren(columnLoading(chosen[slot]));
      }
    }
    load();
  }

  // The districts' figures aligned row by row by the server (/api/compare): each district's profile holds every row
  // either one's profile shows, read for both, and a row a district has no figure for says why. Asked for once per
  // pair; a failed request is forgotten, so the next try asks again.
  const comparisons = new Map();
  function comparisonOf(geos) {
    const key = geos.join(',');
    if (!comparisons.has(key)) comparisons.set(key, api(`/api/compare?d=${encodeURIComponent(key)}`).catch(err => { comparisons.delete(key); throw err; }));
    return comparisons.get(key);
  }

  let seq = 0, readyProfiles = [];
  async function load() {
    const mine = ++seq, slots = SLOTS.filter(s => chosen[s]);
    readyProfiles = [];
    announce();
    if (!slots.length) {
      result.removeAttribute('aria-busy');
      result.replaceChildren(node('p', 'Choose two districts to see their figures side by side, each with its margin of error.', 'cmp-empty'));
      return;
    }
    result.setAttribute('aria-busy', 'true');
    // While a district's figures load, the line over the table says so.
    const waiting = slots.filter(s => !(loaded[s] && loaded[s].geo === chosen[s]));
    const text = `Loading the figures for ${slots.map(s => districtName(chosen[s])).join(' and ')}…`;
    result.replaceChildren(node('p', text, 'cmp-note'), ...['w-20', 'w-30', 'w-30'].map(w => skeleton([`title ${w}`, 'w-60', 'w-45'], 'cmp-section')));
    try {
      const list = (await comparisonOf(slots.map(s => chosen[s]))).districts;
      if (mine !== seq) return;
      slots.forEach((s, i) => {
        const fresh = !loaded[s] || loaded[s].geo !== list[i].geo;
        loaded[s] = list[i];
        if (fresh || !colOf[s].querySelector('.cmp-card')) card(s, list[i]);
      });
      result.replaceChildren(...table(slots.map(s => loaded[s]), slots));
      readyProfiles = list;
      live.textContent = `Figures ready for ${list.map(p => p.district).join(' and ')}.`;
      announce();
    } catch (err) {
      if (mine !== seq) return;
      for (const s of waiting) {
        const name = districtName(chosen[s]), box = node('div', '', 'cmp-card');
        box.append(node('h2', name, 'cmp-pick-title'), changeButton(s, name));
        colOf[s].replaceChildren(box);
      }
      live.textContent = `The figures could not load. ${err.message}`;
      const retry = node('button', 'Retry comparison', 'button secondary');
      retry.type = 'button';
      retry.onclick = () => {
        live.textContent = text;
        colOf[slots[0]].focus();
        update();
      };
      result.replaceChildren(node('p', live.textContent, 'status'), retry);
    } finally {
      if (mine === seq) result.removeAttribute('aria-busy');
    }
  }

  // Tell the chat and the share bar which districts are compared.
  function announce() {
    const districts = SLOTS.filter(s => chosen[s]).map(s => ({geo: chosen[s], name: loaded[s] && loaded[s].geo === chosen[s] ? loaded[s].district : districtName(chosen[s])}));
    document.dispatchEvent(new CustomEvent('klear:compare', {detail: {districts}}));
  }

  function set(slot, geo, name, focus) {
    if (geo && SLOTS.some(s => s !== slot && chosen[s] === geo)) {
      const status = colOf[slot].querySelector('.status');
      if (status) status.textContent = `${name || districtName(geo)} is already in the other column. Choose another district.`;
      return;
    }
    chosen[slot] = geo || null;
    if (geo) resets[slot]?.();
    if (!geo) loaded[slot] = null;
    live.textContent = geo ? `${name || districtName(geo)} added.` : '';
    update();
    if (focus && geo) {
      const next = SLOTS.find(s => !chosen[s]);
      if (next) focusPicker(next);
      else colOf[slot].focus();
    }
  }

  // One district's figure under its name, with a comparison line only under the higher figure if they differ. A
  // district with no figure says why: not published for it, or suppressed. A figure is never inferred for it.
  function figure(f, place, versus, chamber, span = 1) {
    const td = node('td', '', `cmp-figure ${chamber}`);
    td.colSpan = span;
    td.append(node('span', place, 'cmp-place'));
    if (!f || f.missing) {
      td.classList.add('missing');
      td.append(node('span', 'No figure', 'none'));
      if (f && f.missing) td.append(node('span', f.missing, 'moe'));
      return td;
    }
    td.append(node('span', f.plain || f.value, 'num'));
    if (f.spread) td.append(node('span', f.spread, 'moe'));
    if (f.caution) td.append(cautionChip(f.caution));
    if (versus) td.append(versus);
    return td;
  }

  const stateOf = r => (r && (r.against || []).find(a => !a.missing && String(a.geo || '').startsWith('state:'))) || null;
  const sourceKey = s => `${s.name}\u0000${s.year}`;

  // The citations of one compared part: every source its districts' figures come from, numbered once, with the links
  // where each district's figures can be checked; then the sources only Hawaiʻi's or a county's figures come from. A
  // row's number points into the first list, so each figure keeps its source and year.
  function citations(parts) {
    const sources = [], compared = [], at = new Map();
    const add = (list, s) => {
      if (!s || !s.name) return 0;
      const key = sourceKey(s);
      if (at.has(key)) {
        const known = at.get(key);
        if (s.links) known.links = [...(known.links || []), ...s.links.filter(l => !(known.links || []).some(k => k.url === l.url))];
        return list === sources ? sources.indexOf(known) + 1 : 0;
      }
      const copy = {...s, links: [...(s.links || [])]};
      at.set(key, copy);
      list.push(copy);
      return list === sources ? sources.length : 0;
    };
    for (const sec of parts) for (const s of sec.sources || []) add(sources, s);
    for (const sec of parts) for (const r of sec.rows || []) if (r.source) add(sources, r.source);
    for (const sec of parts) for (const s of sec.compared || []) add(compared, s);
    for (const sec of parts) for (const r of sec.rows || []) { const st = stateOf(r); if (st && st.source) add(compared, st.source); }
    const number = s => (s && sources.length > 1 ? sources.findIndex(x => sourceKey(x) === sourceKey(s)) + 1 : 0);
    return {sources, compared, number};
  }

  // What a measure means and how its figures were made, for both districts at once: the plain definition one tap
  // away, then any line on how a figure was estimated, a note on the measure, or a caution, each said once.
  let aboutCount = 0;
  function rowNotes(rows, cols) {
    const first = rows.find(r => r && !r.missing) || rows.find(Boolean), lines = [], seen = new Set();
    const once = (key, make) => { if (key && !seen.has(key)) { seen.add(key); lines.push(make()); } };
    for (const r of rows) {
      if (!r || r.missing) continue;
      if (r.method) once(`m ${r.method.text}`, () => methodLine(r.method));
      if (r.caveat) once(`c ${r.caveat}`, () => node('p', r.caveat, 'profile-caveat'));
    }
    const parts = first ? meaningParts(first.label, first.meaning) : null;
    if (!lines.length && !parts) return {row: null, button: null};
    const tr = node('tr', '', 'cmp-notes'), cell = node('td', '');
    cell.colSpan = cols;
    cell.append(...lines);
    tr.append(cell);
    tr.hidden = !lines.length;
    let button = null;
    if (parts) {
      const about = node('div', '', 'meaning');
      about.id = `cmp-meaning-${++aboutCount}`;
      about.hidden = true;
      about.append(...parts);
      cell.append(about);
      button = node('button', '', 'info');
      button.type = 'button';
      button.setAttribute('aria-expanded', 'false');
      button.setAttribute('aria-controls', about.id);
      button.append(icon('info'), hidden(`What this means: ${first.label}`));
      button.onclick = () => {
        about.hidden = !about.hidden;
        tr.hidden = about.hidden && !lines.length;
        button.setAttribute('aria-expanded', String(!about.hidden));
      };
    }
    return {row: tr, button};
  }

  // One part of a group, such as Income or Work and education, as a table: each measure is a row group, its name
  // with its source number and Hawaiʻi's figure across the top, then each district's figure in its own column, then
  // whether they differ and by how much, then notes on how the figures were made. County-level figures say whose they
  // are: one cell for the county both districts share, or each district's county named in its cell.
  function partTable(parts, list, title, heading) {
    const names = list.map(p => p.district), cols = list.length, box = node('div', '', 'cmp-part');
    const county = countyPart(parts.find(Boolean)), live = parts.filter(Boolean), shared = county && cols === 2 && parts[0] && parts[1] && parts[0].geo === parts[1].geo;
    const shown = county ? 'County context' : title;
    if (heading) box.append(says('h3', shown, 'profile-part-title'));
    else if (county) box.append(node('h3', shown, 'profile-part-title'));
    if (county) box.append(node('p', countyContext(parts, names), 'profile-scope'));
    const intro = live.map(s => s.intro).find(Boolean);
    if (intro) box.append(node('p', intro, 'profile-intro'));
    const caveats = [...new Set(live.map(s => s.caveat).filter(Boolean))], methods = [...new Map(live.filter(s => s.method).map(s => [s.method.text, s.method])).values()];
    for (const c of caveats) box.append(node('p', c, 'profile-caveat lead'));
    for (const m of methods) box.append(methodLine(m));
    const cite = citations(live), t = node('table', '', `cmp-table cols-${cols}`);
    t.createCaption().textContent = `${shown}: ${(county ? live.map(s => s.place) : names).filter((n, i, a) => a.indexOf(n) === i).join(' and ')}${cols === 2 ? ', side by side' : ''}`;
    t.caption.className = 'visually-hidden';
    const head = t.createTHead().insertRow();
    if (shared) { const th = node('th', live[0].place); th.scope = 'col'; th.colSpan = 2; head.append(th); }
    else parts.forEach((s, j) => { const th = node('th', county && s ? `${s.place} (for ${names[j]})` : names[j]); th.scope = 'col'; head.append(th); });
    const ids = [...new Set(live.flatMap(s => s.rows.map(r => r.id)))];
    for (const id of ids) {
      const rows = parts.map(s => (s ? s.rows.find(r => r.id === id) || null : null)), first = rows.find(r => r && !r.missing) || rows.find(Boolean);
      const body = t.createTBody(), top = body.insertRow(), label = node('th', '', 'cmp-label');
      label.colSpan = cols;
      label.scope = 'rowgroup';
      const name = says('span', first.label, 'cmp-measure');
      const numbers = [...new Set(rows.map(r => cite.number(r && r.source)).filter(Boolean))].sort((a, b) => a - b);
      if (numbers.length) { const sup = node('sup', ''); sup.append(hidden(numbers.length > 1 ? ' sources ' : ' source '), numbers.join(',')); name.append(sup); }
      label.append(name);
      const state = rows.map(stateOf).find(Boolean);
      if (state && (state.plain || state.value)) label.append(' ', node('span', `Hawaiʻi: ${state.plain || state.value}`, 'cmp-state'));
      const notes = rowNotes(rows, cols);
      if (notes.button) label.append(' ', notes.button);
      top.append(label);
      const values = body.insertRow();
      if (shared) {
        // One county figure, read once: both districts point at it, so there is no difference to show.
        values.append(figure(rows[0], live[0].place, null, 'county', 2));
      } else {
        const diff = cols === 2 ? pairDifference(rows[0], rows[1]) : null, places = parts.map((s, j) => (county && s ? s.place : names[j]));
        values.append(...rows.map((r, j) => {
          const versus = diff === SLOTS[j] ? comparisonTo(r, rows[1 - j], 'higher', places[1 - j].replace(' District', '')) : null;
          return figure(r, places[j], versus, county ? 'county' : list[j].geo.split(':')[0]);
        }));
        if (diff === 'same') {
          const same = body.insertRow().insertCell();
          same.colSpan = cols;
          same.className = 'cmp-shared';
          same.append(comparisonLine('within_margin', NO_CLEAR_DIFFERENCE));
        }
      }
      if (notes.row) body.append(notes.row);
    }
    box.append(t);
    if (cols === 2 && !shared) box.append(node('p', PAIR_COMPARISON_KEY, 'profile-key'));
    if (cite.sources.length || cite.compared.length) box.append(foldedSources(cite.sources, cite.compared));
    return box;
  }

  // Every measure either district has, in the profile's group and part order, one table per part: At a glance, then
  // the profile's numbered groups. The groups open and shut like the profile's sections, numbered from At a glance.
  function table(list, slots) {
    const glance = list.map(p => ({title: 'At a glance', rows: p.headline, sources: [], compared: []}));
    const groups = [['glance', 'At a glance', [glance]]];
    for (const [key, title, sectionNames] of PROFILE_GROUPS) {
      const parts = sectionNames.filter(n => !n.startsWith('@'))
        .map(n => list.map(p => p.sections.find(s => s.title === n && s.rows.length) || null))
        .filter(part => part.some(Boolean));
      if (parts.length) groups.push([key, title, parts]);
    }
    const out = [node('p', list.length === 2
      ? 'Each figure shows its 90% margin of error or range, and a number after its name points to its source. When there’s a clear difference, the gap appears under the higher figure.'
      : 'Choose a second district to set its figures beside these.', 'cmp-note')];
    const source = list.find(p => p.shares_source && (p.places.length || p.overlaps.length))?.shares_source;
    if (source) {
      out[0].append(' Place and overlap percentages are shares of district residents in the ', link(source.year, source.url), '.');
    }
    // The districts' names over their columns stay in view while the rows scroll under them.
    const heads = node('div', '', 'cmp-sticky');
    heads.setAttribute('aria-hidden', 'true');
    list.forEach((p, i) => {
      const name = node('span', '', `cmp-sticky-name ${p.geo.split(':')[0]}`);
      name.append(slotBadge(slots[i]), p.district);
      heads.append(name);
    });
    out.push(heads);
    const saved = openedSections();
    groups.forEach(([key, title, parts], i) => {
      const d = node('details', '', 'profile-section cmp-section'), summary = node('summary', '');
      d.dataset.key = `cmp-${key}`;
      summary.append(sectionNumber(i + 1), node('span', title, 'sec-title'));
      summary.addEventListener('click', () => rememberOpen(d.dataset.key, !d.open));
      d.append(summary, ...parts.map(part => partTable(part, list, part.find(Boolean).title, parts.length > 1)));
      d.open = typeof saved[d.dataset.key] === 'boolean' ? saved[d.dataset.key] : i < 3;
      out.push(d);
    });
    return out;
  }

  // The districts the address names: up to two, House or Senate.
  function fromAddress() {
    const list = (new URLSearchParams(location.search).get('d') || '').split(',').map(normal).filter(valid);
    show([...new Set(list)].slice(0, 2));
  }
  function show(list) {
    for (const [i, s] of SLOTS.entries()) {
      const geo = list[i] || null;
      if (chosen[s] !== geo) { loaded[s] = null; if (colOf[s].querySelector('.cmp-card')) colOf[s].replaceChildren(); }
      chosen[s] = geo;
    }
    update();
  }

  window.klearCompare = {
    show,
    fromAddress,
    geos: () => SLOTS.filter(s => chosen[s]).map(s => chosen[s]),
    loaded: () => readyProfiles,
    // After Compare opens: the search in the first empty column, or the page's title.
    focusNext() {
      const next = SLOTS.find(s => !chosen[s]);
      const input = next && colOf[next].querySelector('input');
      if (input && !coarsePointer.matches) input.focus({preventScroll: true});
      else $('compare-title').focus({preventScroll: true});
    },
  };
})();
