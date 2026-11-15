// Compare: two districts side by side in the left and middle thirds of the page, with the agent in the right third.
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
    box.append(node('h2', pickTitle(slot), 'cmp-pick-title'));
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
    form.append(label, row, count, status, matches);
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
    for (const mem of p.members || []) {
      const who = node('p', '', 'profile-member-name');
      who.append(node('span', mem.title, 'profile-member-title'), ' ', link(mem.reads || mem.name, mem.url));
      facts.append(who);
    }
    const mapBox = node('div', '', 'cmp-map');
    locatorMap(p.geo).then(svg => { if (svg && chosen[slot] === p.geo) mapBox.replaceChildren(svg); });
    const actions = node('div', '', 'cmp-card-actions'), change = node('button', 'Change', 'textbutton');
    change.type = 'button';
    change.append(hidden(` ${p.district}`));
    change.onclick = () => { set(slot, null); const input = colOf[slot].querySelector('input'); if (input) input.focus(); };
    actions.append(change);
    head.append(h, actions);
    box.append(head, mapBox, facts);
    colOf[slot].replaceChildren(box);
  }

  // A column while its district loads: the district's name, then gray bars where its map and facts will be.
  function columnLoading(geo) {
    const box = node('div', '', 'cmp-card');
    box.append(node('p', `Loading ${districtName(geo)}…`, 'profile-status'), skeleton(['block', 'w-60', 'w-45']));
    return box;
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

  let seq = 0;
  async function load() {
    const mine = ++seq, slots = SLOTS.filter(s => chosen[s]);
    announce();
    if (!slots.length) {
      result.replaceChildren(node('p', 'Choose two districts to see their figures side by side, each with its margin of error.', 'cmp-empty'));
      return;
    }
    result.setAttribute('aria-busy', 'true');
    // While a district's figures load, the line over the table says so.
    const waiting = slots.filter(s => !(loaded[s] && loaded[s].geo === chosen[s]));
    if (waiting.length) {
      const text = `Loading the figures for ${waiting.map(s => districtName(chosen[s])).join(' and ')}…`, note = result.querySelector('.cmp-note');
      if (note) note.textContent = text;
      else result.replaceChildren(node('p', text, 'cmp-note'), ...['w-20', 'w-30', 'w-30'].map(w => skeleton([`title ${w}`, 'w-60', 'w-45'], 'cmp-section')));
    }
    try {
      const list = await Promise.all(slots.map(s => profileOf(chosen[s])));
      if (mine !== seq) return;
      slots.forEach((s, i) => {
        const fresh = !loaded[s] || loaded[s].geo !== list[i].geo;
        loaded[s] = list[i];
        if (fresh || !colOf[s].querySelector('.cmp-card')) card(s, list[i]);
      });
      result.replaceChildren(...table(slots.map(s => loaded[s]), slots));
      announce();
    } catch (err) {
      if (mine === seq) result.replaceChildren(node('p', `The figures could not load. ${err.message}`, 'status'));
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
    if (!geo) loaded[slot] = null;
    live.textContent = geo ? `${name || districtName(geo)} added.` : '';
    update();
    if (focus && geo) {
      const next = SLOTS.find(s => !chosen[s]);
      const input = next && colOf[next].querySelector('input');
      if (input) input.focus({preventScroll: true});
    }
  }

  // One district's figure under its name: the estimate, its margin of error or range, a caution when it is shaky, and
  // how far above or below the other district's figure it is.
  function figure(f, place, versus, chamber) {
    const td = node('td', '', `cmp-figure ${chamber}`);
    td.append(node('span', place, 'cmp-place'));
    if (!f || f.missing) { td.append(node('span', 'No figure', 'none')); return td; }
    td.append(node('span', f.plain || f.value, 'num'));
    if (f.spread) td.append(node('span', f.spread, 'moe'));
    if (f.caution) td.append(cautionChip(f.caution));
    if (versus) td.append(versus);
    return td;
  }

  // Every measure either district has, in the profile's group and row order, as one table per group: each measure is
  // a row group, its name and Hawaiʻi's figure across the top, then each district's figure in its own column, then
  // whether they differ and by how much. The groups open and shut like the profile's sections, numbered from At a glance.
  function table(list, slots) {
    const names = list.map(p => p.district), byId = list.map(p => new Map([...p.headline, ...p.sections.flatMap(s => s.rows)].map(r => [r.id, r])));
    const groups = [['glance', 'At a glance', [...new Set(list.flatMap(p => p.headline.map(r => r.id)))]]];
    for (const [key, title, sectionNames] of PROFILE_GROUPS) {
      const ids = [...new Set(list.flatMap(p => p.sections.filter(s => sectionNames.includes(s.title)).flatMap(s => s.rows.map(r => r.id))))];
      if (ids.length) groups.push([key, title, ids]);
    }
    const out = [node('p', list.length === 2
      ? 'Each figure shows its 90% margin of error or range, and under it how far above or below the other district’s it is: ▲ higher or ▼ lower beyond the margins of error, ≈ no clear difference.'
      : 'Choose a second district to set its figures beside these.', 'cmp-note')];
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
    groups.forEach(([key, title, ids], i) => {
      const d = node('details', '', 'profile-section cmp-section'), summary = node('summary', '');
      d.dataset.key = `cmp-${key}`;
      summary.append(sectionNumber(i + 1), node('span', title, 'sec-title'));
      summary.addEventListener('click', () => rememberOpen(d.dataset.key, !d.open));
      const t = node('table', '', `cmp-table cols-${list.length}`);
      t.createCaption().textContent = `${title}: ${names.join(' and ')}${list.length === 2 ? ', side by side' : ''}`;
      t.caption.className = 'visually-hidden';
      const head = t.createTHead().insertRow();
      for (const name of names) { const th = node('th', name); th.scope = 'col'; head.append(th); }
      for (const id of ids) {
        const rows = byId.map(m => m.get(id) || null), first = rows.find(Boolean);
        const body = t.createTBody(), top = body.insertRow(), label = node('th', '', 'cmp-label');
        label.colSpan = list.length;
        label.scope = 'rowgroup';
        label.append(says('span', first.label, 'cmp-measure'));
        const state = rows.map(r => r && (r.against || []).find(a => !a.missing && String(a.geo || '').startsWith('state:'))).find(Boolean);
        if (state && (state.plain || state.value)) label.append(' ', node('span', `Hawaiʻi: ${state.plain || state.value}`, 'cmp-state'));
        top.append(label);
        const values = body.insertRow(), diff = list.length === 2 ? pairDifference(rows[0], rows[1]) : null;
        values.className = 'cmp-values';
        values.append(...rows.map((r, j) => figure(r, names[j], diff && pairLine(r, rows[1 - j], diff, j ? 'b' : 'a', names[1 - j]), list[j].geo.split(':')[0])));
        if (diff) values.classList.add(`higher-${diff}`);
      }
      d.append(summary, t);
      d.open = typeof saved[d.dataset.key] === 'boolean' ? saved[d.dataset.key] : i < 3;
      out.push(d);
    });
    return out;
  }

  // Under a district's figure, how far above or below the other district's it is, as a percentage of the other's: "▲ 56%
  // above House 23", green above and red below. About-equal, and set lighter, when their margins of error overlap;
  // screen readers hear that it is no clear difference.
  function pairLine(mine, theirs, diff, slot, other) {
    const apart = percentApart(mine.num, theirs.num);
    if (!apart) return null;
    const kind = diff === 'same' ? 'within_margin' : diff === slot ? 'higher' : 'lower';
    const line = node('span', '', `compare-line ${kind} ${sideOf(mine.num, theirs.num)}`.trim()), mark = node('span', MARKS[kind], 'reading-mark');
    mark.setAttribute('aria-hidden', 'true');
    line.append(mark, ' ', `${apart} ${other.replace(' District', '')}`);
    if (kind === 'within_margin') line.append(hidden(', no clear difference'));
    return line;
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
    loaded: () => SLOTS.filter(s => chosen[s] && loaded[s] && loaded[s].geo === chosen[s]).map(s => loaded[s]),
    // After Compare opens: the search in the first empty column, or the page's title.
    focusNext() {
      const next = SLOTS.find(s => !chosen[s]);
      const input = next && colOf[next].querySelector('input');
      if (input && !coarsePointer.matches) input.focus({preventScroll: true});
      else $('compare-title').focus({preventScroll: true});
    },
  };
})();
