// Interactive House and Senate district map that opens the district explorer.
(() => {
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('map');
  const tabs = document.getElementById('island-tabs');
  const list = document.getElementById('map-list');
  const go = document.getElementById('pick-go');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let data = null, chamber = 'house', group = 'all', picked = null, view = null, frame = 0;

  function el(tag, attrs) { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; }

  function setView(target) {
    cancelAnimationFrame(frame);
    const from = view || target, start = performance.now(), ms = reduceMotion || !view ? 0 : 450;
    const step = now => {
      const t = ms ? Math.min(1, (now - start) / ms) : 1, e = 1 - Math.pow(1 - t, 3);
      view = from.map((v, i) => v + (target[i] - v) * e);
      svg.setAttribute('viewBox', view.join(' '));
      sizeLabels();
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  }

  function sizeLabels() {
    const r = svg.getBoundingClientRect();
    if (!view || !r.width) return;
    const unitsPerPx = Math.max(view[2] / r.width, view[3] / r.height);
    svg.style.setProperty('--label', `${(12 * unitsPerPx).toFixed(3)}px`);
  }

  function pick(row, focusMap) {
    picked = row;
    for (const p of svg.querySelectorAll('[data-geo]')) p.classList.toggle('picked', Boolean(row) && p.dataset.geo === row.geo);
    document.getElementById('pick-name').textContent = row ? row.name : 'Tap an area on the map';
    document.getElementById('pick-island').textContent = row ? row.islands.toUpperCase() : 'SELECT A DISTRICT';
    go.disabled = !row;
    list.value = row ? row.geo : '';
    if (row && focusMap && group !== row.group && group !== 'all') zoom(row.group);
  }

  function zoom(name) {
    group = name;
    for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.dataset.group === name));
    svg.classList.toggle('zoomed', name !== 'all');
    setView(data.groups[name].box);
  }

  function draw() {
    const rows = data.chambers[chamber];
    const shapes = el('g'), labels = el('g', {class: 'labels', 'aria-hidden': 'true'});
    for (const row of rows) {
      const path = el('path', {d: row.d, class: 'district', tabindex: '0', role: 'button', 'aria-label': `${row.name}, ${row.islands}`});
      path.dataset.geo = row.geo;
      const title = el('title'); title.textContent = `${row.name} · ${row.islands}`; path.append(title);
      path.addEventListener('click', () => pick(row));
      path.addEventListener('dblclick', () => choose(row.geo, row.name));
      path.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (picked === row) choose(row.geo, row.name); else pick(row); } });
      shapes.append(path);
      const text = el('text', {x: row.label[0], y: row.label[1], class: `label g-${row.group}`});
      text.dataset.geo = row.geo;
      text.textContent = row.name.replace(/\D+/g, '');
      labels.append(text);
    }
    svg.replaceChildren(shapes, labels);
    list.replaceChildren(new Option(`Choose a ${chamber === 'house' ? 'House' : 'Senate'} district…`, ''));
    const byGroup = {};
    for (const row of rows) (byGroup[row.group] ||= []).push(row);
    for (const [key, info] of Object.entries(data.groups)) {
      if (!byGroup[key]) continue;
      const og = document.createElement('optgroup'); og.label = info.label;
      for (const row of byGroup[key]) og.append(new Option(`${row.name} · ${row.islands}`, row.geo));
      list.append(og);
    }
    pick(null);
  }

  document.querySelectorAll('[data-chamber]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.chamber === chamber || !data) return;
    chamber = b.dataset.chamber;
    document.querySelectorAll('[data-chamber]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    draw();
  }));
  list.addEventListener('change', () => { const row = data && data.chambers[chamber].find(r => r.geo === list.value); pick(row || null); if (row) zoom(row.group); });
  go.addEventListener('click', () => { if (picked) choose(picked.geo, picked.name); });
  window.addEventListener('resize', sizeLabels);

  fetch('/districts.json').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(json => {
    data = json;
    for (const [key, info] of Object.entries(data.groups)) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = info.label; b.dataset.group = key;
      b.addEventListener('click', () => zoom(key));
      tabs.append(b);
    }
    draw();
    zoom('all');
    document.getElementById('map-loading').hidden = true;
  }).catch(() => { document.getElementById('map-loading').textContent = 'The map could not load. Use the search or the list below.'; });
})();
