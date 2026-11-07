// Interactive House and Senate district map that opens the district explorer.
(() => {
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('map');
  const frameEl = svg.parentElement;
  const tabs = document.getElementById('island-tabs');
  const list = document.getElementById('map-list');
  const go = document.getElementById('pick-go');
  const hint = document.getElementById('map-hint');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const SNAP_SHOWN = 0.5, SNAP_FILL = 0.2, SNAP_ALL = 0.8, MIN_WIDTH = 12;
  let data = null, chamber = 'house', group = 'all', picked = null, view = null, goal = null, anim = 0, settleTimer = 0, hintTimer = 0;
  const pointers = new Map();
  let gesture = null, dragged = false, zipCode = null;
  const zipStatus = document.getElementById('zip-status'), zipMatches = document.getElementById('zip-matches');

  function el(tag, attrs) { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; }

  // Grow a box to the frame's aspect ratio so the viewBox is exactly what is visible.
  function fit(box) {
    const r = frameEl.getBoundingClientRect(), aspect = r.width && r.height ? r.width / r.height : 1.5;
    let [x, y, w, h] = box;
    if (w / h < aspect) { const nw = h * aspect; x -= (nw - w) / 2; w = nw; } else { const nh = w / aspect; y -= (nh - h) / 2; h = nh; }
    return [x, y, w, h];
  }

  // Keep the zoom between the closest allowed view and the whole chain, and the center over the islands.
  function clamp(v) {
    const all = fit(data.groups.all.box);
    let [x, y, w, h] = v;
    const s = Math.min(Math.max(w, MIN_WIDTH), all[2]) / w;
    const cx = x + w / 2, cy = y + h / 2;
    w *= s; h *= s;
    const nx = Math.min(Math.max(cx, all[0] + w / 2), all[0] + all[2] - w / 2) - w / 2;
    const ny = Math.min(Math.max(cy, all[1] + h / 2), all[1] + all[3] - h / 2) - h / 2;
    return [nx, ny, w, h];
  }

  function render(v) {
    view = v;
    svg.setAttribute('viewBox', view.join(' '));
    const px = svg.getBoundingClientRect().width;
    svg.classList.toggle('zoomed', px > 0 && view[2] / px < 1);
    svg.classList.toggle('free', view[2] < fit(data.groups.all.box)[2] * 0.98);
    sizeLabels();
  }

  function animateTo(target) {
    cancelAnimationFrame(anim);
    goal = target;
    const from = view || target, start = performance.now(), ms = reduceMotion || !view ? 0 : 450;
    const step = now => {
      const t = ms ? Math.min(1, (now - start) / ms) : 1, e = 1 - Math.pow(1 - t, 3);
      render(from.map((v, i) => v + (target[i] - v) * e));
      if (t < 1) anim = requestAnimationFrame(step); else goal = null;
    };
    anim = requestAnimationFrame(step);
  }

  function sizeLabels() {
    const r = svg.getBoundingClientRect();
    if (!view || !r.width) return;
    svg.style.setProperty('--label', `${(12 * view[2] / r.width).toFixed(3)}px`);
  }

  function markTab(name) {
    group = name;
    for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.dataset.group === name));
  }

  function zoom(name) { markTab(name); animateTo(fit(data.groups[name].box)); }

  function overlap(a, b) {
    const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
    const h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    return w > 0 && h > 0 ? w * h : 0;
  }

  // After a gesture, snap to the island that fills most of the view, or back to all islands.
  function settle() {
    let best = null, bestFill = 0;
    for (const [name, info] of Object.entries(data.groups)) {
      if (name === 'all') continue;
      const b = info.box, shared = overlap(view, b);
      const shown = shared / (b[2] * b[3]), fill = shared / (view[2] * view[3]);
      if (shown >= SNAP_SHOWN && fill >= SNAP_FILL && fill > bestFill) { best = name; bestFill = fill; }
    }
    if (best) return zoom(best);
    if (view[2] >= fit(data.groups.all.box)[2] * SNAP_ALL) return zoom('all');
    markTab(null);
  }

  function settleSoon(ms) { clearTimeout(settleTimer); settleTimer = setTimeout(settle, ms); }

  function toMap(clientX, clientY) {
    const r = svg.getBoundingClientRect();
    return [view[0] + (clientX - r.left) / r.width * view[2], view[1] + (clientY - r.top) / r.height * view[3]];
  }

  // Scale the view by k around a map point that stays under the same screen spot.
  function zoomAt(k, [px, py]) {
    cancelAnimationFrame(anim); goal = null;
    const [x, y, w, h] = view;
    render(clamp([px - (px - x) * k, py - (py - y) * k, w * k, h * k]));
  }

  function showHint() {
    hint.hidden = false;
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => { hint.hidden = true; }, 1400);
  }

  svg.addEventListener('wheel', e => {
    if (!view) return;
    if (!e.ctrlKey && !e.metaKey) { showHint(); return; }
    e.preventDefault();
    zoomAt(Math.exp(Math.max(-50, Math.min(50, e.deltaY)) * 0.004), toMap(e.clientX, e.clientY));
    settleSoon(350);
  }, {passive: false});

  svg.addEventListener('dblclick', e => {
    if (!view) return;
    e.preventDefault();
    const target = clamp((() => { const [px, py] = toMap(e.clientX, e.clientY), [, , w, h] = view; return [px - w / 4, py - h / 4, w / 2, h / 2]; })());
    animateTo(target);
    settleSoon(500);
  });

  svg.addEventListener('pointerdown', e => {
    if (!view) return;
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    dragged = false;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      gesture = {dist: Math.hypot(a[0] - b[0], a[1] - b[1]), mid: toMap((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), view: [...view]};
    } else {
      gesture = {start: [e.clientX, e.clientY], view: [...view]};
    }
  });

  svg.addEventListener('pointermove', e => {
    if (!pointers.has(e.pointerId) || !gesture) return;
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    const r = svg.getBoundingClientRect();
    if (pointers.size === 2 && gesture.dist) {
      const [a, b] = [...pointers.values()];
      const k = gesture.dist / Math.max(1, Math.hypot(a[0] - b[0], a[1] - b[1]));
      const [x, y, w, h] = gesture.view, [px, py] = gesture.mid;
      const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const nw = w * k, nh = h * k;
      const nx = px - (mid[0] - r.left) / r.width * nw, ny = py - (mid[1] - r.top) / r.height * nh;
      cancelAnimationFrame(anim);
      render(clamp([nx, ny, nw, nh]));
      dragged = true;
    } else if (pointers.size === 1 && gesture.start && svg.classList.contains('free')) {
      const dx = e.clientX - gesture.start[0], dy = e.clientY - gesture.start[1];
      if (!dragged && Math.hypot(dx, dy) < 5) return;
      if (!dragged) svg.setPointerCapture(e.pointerId);
      dragged = true;
      const [x, y, w, h] = gesture.view;
      cancelAnimationFrame(anim);
      render(clamp([x - dx / r.width * w, y - dy / r.height * h, w, h]));
    }
  });

  function endPointer(e) {
    if (!pointers.delete(e.pointerId)) return;
    if (pointers.size === 1) { const [p] = pointers.values(); gesture = {start: p, view: [...view]}; return; }
    gesture = null;
    if (dragged) settleSoon(120);
  }
  svg.addEventListener('pointerup', endPointer);
  svg.addEventListener('pointercancel', endPointer);
  // A drag ends with a click on whatever district is under the finger; ignore it.
  svg.addEventListener('click', e => { if (dragged) { e.stopPropagation(); dragged = false; } }, true);

  function zoomBy(k) {
    if (!view) return;
    const [x, y, w, h] = goal || view, cx = x + w / 2, cy = y + h / 2;
    animateTo(clamp([cx - w * k / 2, cy - h * k / 2, w * k, h * k]));
    settleSoon(500);
  }
  document.getElementById('zoom-in').addEventListener('click', () => zoomBy(0.5));
  document.getElementById('zoom-out').addEventListener('click', () => zoomBy(2));

  function pick(row) {
    picked = row;
    for (const p of svg.querySelectorAll('[data-geo]')) p.classList.toggle('picked', Boolean(row) && p.dataset.geo === row.geo);
    document.getElementById('pick-name').textContent = row ? row.name : 'Tap an area on the map';
    document.getElementById('pick-island').textContent = row ? row.islands.toUpperCase() : 'SELECT A DISTRICT';
    go.disabled = !row;
    list.value = row ? row.geo : '';
  }

  function draw() {
    const rows = data.chambers[chamber];
    const shapes = el('g'), labels = el('g', {class: 'labels', 'aria-hidden': 'true'});
    for (const row of rows) {
      const path = el('path', {d: row.d, class: 'district', tabindex: '0', role: 'button', 'aria-label': `${row.name}, ${row.islands}`});
      path.dataset.geo = row.geo;
      const title = el('title'); title.textContent = `${row.name} · ${row.islands}`; path.append(title);
      path.addEventListener('click', () => pick(row));
      path.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (picked === row) choose(row.geo, row.name); else pick(row); } });
      shapes.append(path);
      const text = el('text', {x: row.label[0], y: row.label[1], class: 'label'});
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
    showZip();
  }

  const pct = share => share < 0.01 ? 'under 1%' : `${Math.round(share * 100)}%`;

  // Highlight the districts a ZIP area's residents live in and zoom to them.
  function showZip() {
    zipMatches.replaceChildren();
    for (const p of svg.querySelectorAll('.zip-hit')) p.classList.remove('zip-hit');
    svg.classList.toggle('zip-mode', Boolean(zipCode));
    if (!zipCode) return;
    const rowsByGeo = new Map(data.chambers[chamber].map(r => [r.geo, r]));
    const hits = data.zips[zipCode][chamber].map(([geo, share]) => ({row: rowsByGeo.get(geo), share}));
    const kind = chamber === 'house' ? 'House' : 'Senate';
    zipStatus.textContent = hits.length === 1 ? `ZIP ${zipCode} is all in ${hits[0].row.name}.` : `ZIP ${zipCode} spans ${hits.length} ${kind} districts. Share of its residents in each:`;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const main = hits.some(h => h.share >= 0.05) ? hits.filter(h => h.share >= 0.05) : hits;
    for (const {row, share} of hits) {
      for (const n of svg.querySelectorAll(`[data-geo="${row.geo}"]`)) n.classList.add('zip-hit');
      const path = svg.querySelector(`.district[data-geo="${row.geo}"]`);
      const b = path.getBBox();
      if (main.some(h => h.row === row)) { x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height); }
      if (hits.length > 1) {
        const chip = document.createElement('button');
        chip.type = 'button'; chip.className = 'match'; chip.textContent = `District ${row.name.replace(/\D+/g, '')} · ${pct(share)}`;
        chip.setAttribute('aria-label', `${row.name}, ${pct(share)} of residents`);
        chip.addEventListener('click', () => pick(row));
        zipMatches.append(chip);
      }
    }
    const padX = (x1 - x0) * 0.12 + 2, padY = (y1 - y0) * 0.12 + 2;
    markTab(null);
    animateTo(clamp(fit([x0 - padX, y0 - padY, x1 - x0 + 2 * padX, y1 - y0 + 2 * padY])));
    if (hits.length === 1) pick(hits[0].row);
  }

  document.getElementById('zip-form').addEventListener('submit', e => {
    e.preventDefault();
    if (!data) return;
    const zip = document.getElementById('zip').value.replace(/\D/g, '');
    zipCode = null;
    if (!/^\d{5}$/.test(zip)) { showZip(); zipStatus.textContent = 'Enter a 5-digit ZIP code.'; return; }
    const entry = data.zips[zip];
    if (!entry) { showZip(); zipStatus.textContent = `No map area for ZIP ${zip}. Hawaiʻi home ZIPs start with 967 or 968, and PO box ZIPs have no area. Try the ZIP where you live.`; return; }
    if (!entry.house.length) { showZip(); zipStatus.textContent = `ZIP ${zip} had no residents in the 2020 census, so it has no district. Try the ZIP where you live.`; return; }
    zipCode = zip;
    pick(null);
    showZip();
  });
  document.getElementById('zip').addEventListener('input', e => { if (!e.target.value.trim() && zipCode) { zipCode = null; zipStatus.textContent = ''; showZip(); } });

  document.querySelectorAll('[data-chamber]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.chamber === chamber || !data) return;
    chamber = b.dataset.chamber;
    document.querySelectorAll('[data-chamber]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    draw();
  }));
  list.addEventListener('change', () => { const row = data && data.chambers[chamber].find(r => r.geo === list.value); pick(row || null); if (row) zoom(row.group); });
  go.addEventListener('click', () => { if (picked) choose(picked.geo, picked.name); });
  window.addEventListener('resize', () => { if (data && view) render(group ? fit(data.groups[group].box) : clamp(view)); });

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
