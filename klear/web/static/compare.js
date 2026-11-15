// The compare page: three tabs. Districts side by side reads up to three districts' profiles and sets their figures in
// one table, by the profile's groups, with Hawaiʻi as a whole beside them. Rankings and map colors every district by
// one measure (map.js). Outreach groups districts by who meets a program's income test (outreach.js). The tab and the
// districts live in the address, so a link, Back and refresh keep them.
(() => {
  const VIEWS = ['districts', 'rankings', 'outreach'], MOST = 3, STATE = 'state:15';
  const tabs = VIEWS.map(v => $(`tab-${v}`)), params = new URLSearchParams(location.search);

  // Put the page's choices in the address without adding a history entry for each one.
  function setParams(changes) {
    const url = new URL(location.href);
    for (const [k, v] of Object.entries(changes)) { if (v) url.searchParams.set(k, v); else url.searchParams.delete(k); }
    try { history.replaceState(null, '', url.pathname + url.search); } catch { /* history unavailable */ }
  }

  // The tabs: an ARIA tab list, with the arrow keys, Home and End moving between them.
  function showView(view, focus) {
    VIEWS.forEach((v, i) => {
      const on = v === view;
      tabs[i].setAttribute('aria-selected', String(on));
      tabs[i].tabIndex = on ? 0 : -1;
      $(`panel-${v}`).hidden = !on;
    });
    if (focus) tabs[VIEWS.indexOf(view)].focus();
    setParams({view: view === 'districts' ? '' : view});
    // The map measures its frame to fit the islands, so it refits once its tab shows.
    if (view === 'rankings') window.dispatchEvent(new Event('resize'));
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => showView(VIEWS[i]));
    t.addEventListener('keydown', e => {
      const to = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: VIEWS.length - 1}[e.key];
      if (to === undefined) return;
      e.preventDefault();
      showView(VIEWS[(to + VIEWS.length) % VIEWS.length], true);
    });
  });
  showView(VIEWS.includes(params.get('view')) ? params.get('view') : 'districts');

  // Districts side by side.
  const chosen = [], profiles = new Map();
  const GEO = /^(house|senate):\d{3}$/;
  const normal = geo => { const m = /^(house|senate):(\d{1,3})$/.exec(geo || ''); return m ? `${m[1]}:${m[2].padStart(3, '0')}` : null; };

  async function profileOf(geo) {
    if (!profiles.has(geo)) profiles.set(geo, api('/api/profile?district=' + encodeURIComponent(geo)).catch(err => { profiles.delete(geo); throw err; }));
    return profiles.get(geo);
  }

  function add(geo, name) {
    resetFind();
    if (chosen.some(c => c.geo === geo)) { $('cmp-status').textContent = `${name} is already in the table.`; return; }
    if (chosen.length >= MOST) { $('cmp-status').textContent = `Up to ${MOST} districts at once. Remove one to add ${name}.`; return; }
    chosen.push({geo, name});
    $('cmp-live').textContent = `${name} added.`;
    update();
  }
  function remove(geo) {
    const i = chosen.findIndex(c => c.geo === geo);
    if (i < 0) return;
    const [gone] = chosen.splice(i, 1);
    $('cmp-live').textContent = `${gone.name} removed.`;
    update();
    $('cmp-place').focus();
  }

  // The chosen districts as chips that each remove their district.
  function showChosen() {
    $('cmp-chosen').replaceChildren(...chosen.map(c => {
      const li = node('li', '', `cmp-chip ${c.geo.split(':')[0]}`), b = node('button', '', 'cmp-remove');
      b.type = 'button';
      const x = node('span', '×');
      x.setAttribute('aria-hidden', 'true');
      b.append(x, hidden(`Remove ${c.name}`));
      b.onclick = () => remove(c.geo);
      const a = node('a', c.name);
      a.href = districtPath(c.geo);
      li.append(a, b);
      return li;
    }));
    $('cmp-place').placeholder = chosen.length ? 'Add another district' : 'e.g. Hilo, 96744, or Senate 12';
  }

  // One district's figure in its cell: the estimate, its margin of error or range, and a caution when it is shaky.
  function cell(f, place) {
    const td = node('td', '', 'cmp-figure');
    const where = node('span', place, 'cmp-place');
    where.setAttribute('aria-hidden', 'true');
    td.append(where);
    if (!f || f.missing) { td.append(node('span', 'No figure', 'none')); return td; }
    td.append(node('span', f.plain || f.value, 'num'));
    if (f.spread) td.append(node('span', f.spread, 'moe'));
    if (f.caution) td.append(cautionChip(f.caution));
    return td;
  }

  // Every measure any chosen district has, in the profile's group and row order, with each district's figure and the
  // state's. A measure only some districts have shows "No figure" for the rest.
  function table(list) {
    const groups = PROFILE_GROUPS.map(([key, title, names]) => ({key, title, names, rows: new Map()}));
    for (const p of list) {
      for (const sec of p.sections) {
        const g = groups.find(x => x.names.includes(sec.title)) || groups.at(-1);
        for (const r of sec.rows) {
          const row = g.rows.get(r.id) || {label: r.label, by: new Map(), state: null};
          row.by.set(p.geo, r);
          row.state ||= (r.against || []).find(a => a.geo === STATE) || null;
          g.rows.set(r.id, row);
        }
      }
    }
    const t = node('table', '', 'cmp-table'), head = t.createTHead().insertRow();
    t.createCaption().textContent = `${list.map(p => p.district).join(', ')} and Hawaiʻi, side by side`;
    t.caption.className = 'visually-hidden';
    const th = (text, cls) => { const h = node('th', '', cls); h.scope = 'col'; h.append(...[].concat(text)); head.append(h); };
    th('Measure', 'cmp-measure');
    for (const p of list) {
      const name = node('a', p.district);
      name.href = districtPath(p.geo);
      th([name, node('span', p.where || '', 'cmp-where')], `cmp-figure ${p.geo.split(':')[0]}`);
    }
    th('Hawaiʻi', 'cmp-figure state');
    for (const g of groups) {
      if (!g.rows.size) continue;
      const body = t.createTBody(), top = body.insertRow(), title = node('th', g.title, 'cmp-group');
      title.colSpan = list.length + 2;
      title.scope = 'colgroup';
      top.append(title);
      for (const row of g.rows.values()) {
        const tr = body.insertRow(), label = says('th', row.label, 'cmp-measure');
        label.scope = 'row';
        tr.append(label, ...list.map(p => cell(row.by.get(p.geo), p.district)), cell(row.state, 'Hawaiʻi'));
      }
    }
    const box = node('div', '', 'table-scroll cmp-scroll');
    box.tabIndex = 0;
    box.setAttribute('role', 'region');
    box.setAttribute('aria-label', 'Districts side by side');
    box.append(t);
    return box;
  }

  let seq = 0;
  async function update() {
    showChosen();
    setParams({d: chosen.map(c => c.geo).join(',')});
    const mine = ++seq, out = $('cmp-result');
    if (!chosen.length) {
      out.replaceChildren(node('p', 'Add up to three districts to see their figures side by side, each with its margin of error.', 'cmp-empty'));
      return;
    }
    out.setAttribute('aria-busy', 'true');
    try {
      const list = await Promise.all(chosen.map(c => profileOf(c.geo)));
      if (mine !== seq) return;
      list.forEach((p, i) => { chosen[i].name = p.district; });
      showChosen();
      const note = node('p', 'Each figure shows its 90% margin of error or range. Where two ranges overlap, the data cannot say which district is higher.', 'cmp-note');
      out.replaceChildren(note, table(list));
    } catch (err) {
      if (mine === seq) out.replaceChildren(node('p', `The figures could not load. ${err.message}`, 'status'));
    } finally {
      if (mine === seq) out.removeAttribute('aria-busy');
    }
  }

  const resetFind = lookupForm($('cmp-find'), $('cmp-place'), $('cmp-status'), $('cmp-matches'), add);
  for (const geo of (params.get('d') || '').split(',').map(normal).filter(g => g && GEO.test(g)).slice(0, MOST)) {
    if (!chosen.some(c => c.geo === geo)) chosen.push({geo, name: districtName(geo)});
  }
  update();
})();
