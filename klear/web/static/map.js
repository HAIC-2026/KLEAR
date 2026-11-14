// Interactive House and Senate district map that sets the district the chat asks about.
(() => {
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('map');
  const frameEl = svg.parentElement;
  const tabs = document.getElementById('island-tabs');
  const list = document.getElementById('map-list');
  const go = document.getElementById('pick-go');
  const hint = document.getElementById('map-hint');
  const zoomEl = document.querySelector('.zoom-buttons');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const coarse = window.matchMedia('(pointer: coarse)');
  const SNAP_SHOWN = 0.5, SNAP_FILL = 0.2, SNAP_ALL = 0.8, MIN_WIDTH = 12, DIGIT_PX = 7.6, LABEL_PX = 15;
  let data = null, chamber = 'house', group = 'all', picked = null, view = null, goal = null, anim = 0, settleTimer = 0, hintTimer = 0;
  const pointers = new Map();
  let gesture = null, dragged = false, area = null, waiting = null, labelInfo = [], districts = [], stop = null, fitted = null, frameSize = '';
  const zipStatus = document.getElementById('zip-status'), zipMatches = document.getElementById('zip-matches');
  // Coloring the map by a measure: the figures for the chosen measure and chamber, by district, or null for the plain map.
  const shadeSelect = document.getElementById('map-shade'), legend = document.getElementById('map-legend'), table = document.getElementById('map-table');
  const shadeStatus = document.getElementById('shade-status'), shadeLive = document.getElementById('shade-live'), pickValue = document.getElementById('pick-value');
  const SHADE_CLASSES = ['q1', 'q2', 'q3', 'q4', 'q5', 'none'], MISSING = 'not available', shadeCache = new Map();
  let shading = null, figures = new Map(), shadeAsk = 0, marks = null;

  function el(tag, attrs) { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; }

  // Stripes mark an unreliable estimate, ink on a light shade and white on a dark one, and dots a district with no
  // figure. They are drawn over the district's fill and keep the same size on screen at any zoom (see sizeLabels).
  const defs = el('defs');
  const stripeSets = [['map-stripes', 'stripe'], ['map-stripes-light', 'stripe light']].map(([id, cls]) => {
    const p = el('pattern', {id, patternUnits: 'userSpaceOnUse', width: 5, height: 5});
    p.append(el('line', {class: cls, x1: 2.5, y1: 0, x2: 2.5, y2: 5}));
    return p;
  });
  const dots = el('pattern', {id: 'map-dots', patternUnits: 'userSpaceOnUse', width: 5, height: 5});
  dots.append(el('circle', {class: 'dot', cx: 2.5, cy: 2.5, r: 1}));
  defs.append(...stripeSets, dots);
  // Shades 4 and 5 are dark, so their borders and stripes are white (style.css).
  const DARK_SHADE = 4;

  // Grow a box to the frame's aspect ratio so the viewBox is exactly what is visible.
  // With clear set on a narrow frame, fit the box left of the zoom buttons.
  function fit(box, clear) {
    const r = frameEl.getBoundingClientRect(), z = zoomEl.getBoundingClientRect();
    const inset = clear && r.width && r.width < 480 ? Math.max(0, r.right - z.left + 6) : 0;
    const aspect = r.width && r.height ? (r.width - inset) / r.height : 1.5;
    let [x, y, w, h] = box;
    if (w / h < aspect) { const nw = h * aspect; x -= (nw - w) / 2; w = nw; } else { const nh = w / aspect; y -= (nh - h) / 2; h = nh; }
    return [x, y, inset ? w * r.width / (r.width - inset) : w, h];
  }

  function target(name) { return fit(data.groups[name].box, name !== 'all'); }

  // Keep the zoom between the closest allowed view and the whole chain, and the center over the islands,
  // with room on narrow frames to move any island left of the zoom buttons.
  function clamp(v) {
    const all = fit(data.groups.all.box), room = fit(data.groups.all.box, true);
    let [x, y, w, h] = v;
    const s = Math.min(Math.max(w, MIN_WIDTH), all[2]) / w;
    const cx = x + w / 2, cy = y + h / 2;
    w *= s; h *= s;
    const nx = Math.min(Math.max(cx, room[0] + w / 2), room[0] + room[2] - w / 2) - w / 2;
    const ny = Math.min(Math.max(cy, room[1] + h / 2), room[1] + room[3] - h / 2) - h / 2;
    return [nx, ny, w, h];
  }

  function render(v) {
    view = v;
    svg.setAttribute('viewBox', view.join(' '));
    const px = svg.getBoundingClientRect().width;
    // Number districts past one map unit per pixel, and in any island view that has finished zooming in.
    const island = group && group !== 'all' && view[2] <= target(group)[2] * 1.05;
    svg.classList.toggle('zoomed', px > 0 && (view[2] / px < 1 || island));
    svg.classList.toggle('free', view[2] < fit(data.groups.all.box)[2] * 0.98);
    sizeLabels();
    declutter();
    updateStop();
  }

  function animateTo(target) {
    cancelAnimationFrame(anim);
    clearTimeout(settleTimer);
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
    const k = view[2] / r.width;
    svg.style.setProperty('--label', `${(12 * k).toFixed(3)}px`);
    for (const p of stripeSets) p.setAttribute('patternTransform', `scale(${k.toFixed(5)}) rotate(45)`);
    dots.setAttribute('patternTransform', `scale(${k.toFixed(5)})`);
  }

  // Hide district numbers that the frame edge or zoom buttons would cut, or that would overlap one that matters more:
  // the picked district, then ZIP hits, then larger districts. The picked number always shows.
  function declutter() {
    const r = svg.getBoundingClientRect(), zoomed = svg.classList.contains('zoomed');
    if (!view || !r.width || (!zoomed && !svg.classList.contains('zip-mode'))) return;
    const k = r.width / view[2], z = zoomEl.getBoundingClientRect();
    const kept = [[z.left - r.left, z.top - r.top, z.width, z.height]];
    const rank = info => (info.text.classList.contains('picked') ? 2e12 : 0) + (info.text.classList.contains('zip-hit') ? 1e12 : 0) + info.size;
    for (const info of [...labelInfo].sort((a, b) => rank(b) - rank(a))) {
      if (!zoomed && !info.text.classList.contains('zip-hit')) { info.text.classList.remove('crowded'); continue; }
      const w = info.text.textContent.length * DIGIT_PX + 4, x = (info.x - view[0]) * k - w / 2, y = (info.y - view[1]) * k - LABEL_PX / 2;
      const inside = x >= 0 && x + w <= r.width && y >= 0 && y + LABEL_PX <= r.height;
      const crowded = !info.text.classList.contains('picked') && (!inside || kept.some(([bx, by, bw, bh]) => x < bx + bw && bx < x + w && y < by + bh && by < y + LABEL_PX));
      info.text.classList.toggle('crowded', crowded);
      if (!crowded) kept.push([x, y, w, LABEL_PX]);
    }
  }

  function markTab(name) {
    group = name;
    for (const b of tabs.children) {
      const on = b.dataset.group === name;
      b.setAttribute('aria-pressed', String(on));
      if (on && tabs.scrollWidth > tabs.clientWidth) {
        const t = tabs.getBoundingClientRect(), r = b.getBoundingClientRect();
        tabs.scrollTo({left: tabs.scrollLeft + r.left - t.left - (t.width - r.width) / 2, behavior: reduceMotion ? 'auto' : 'smooth'});
      }
    }
  }

  // A fitted view is refit when the frame changes size; a view left by a gesture is not.
  function zoom(name) { markTab(name); fitted = () => target(name); animateTo(target(name)); }

  function overlap(a, b) {
    const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
    const h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    return w > 0 && h > 0 ? w * h : 0;
  }

  // After a gesture, snap to the island that fills most of the view, or back to all islands.
  // After the zoom-out button (out set), never zoom back in.
  function settle(out) {
    let best = null, bestFill = 0;
    for (const [name, info] of Object.entries(data.groups)) {
      if (name === 'all') continue;
      const b = info.box, shared = overlap(view, b);
      const shown = shared / (b[2] * b[3]), fill = shared / (view[2] * view[3]);
      if (shown >= SNAP_SHOWN && fill >= SNAP_FILL && fill > bestFill) { best = name; bestFill = fill; }
    }
    // Snap back only after zooming out past the island; leave closer views and pans alone.
    const close = best && view[2] <= target(best)[2] * 1.02;
    if (best && !close && !out) return zoom(best);
    if (!close && view[2] >= fit(data.groups.all.box)[2] * SNAP_ALL) return zoom('all');
    markTab(close ? best : null);
    if (!goal) render(view);
  }

  function settleSoon(ms, out) { clearTimeout(settleTimer); settleTimer = setTimeout(() => settle(out), ms); }

  function toMap(clientX, clientY) {
    const r = svg.getBoundingClientRect();
    return [view[0] + (clientX - r.left) / r.width * view[2], view[1] + (clientY - r.top) / r.height * view[3]];
  }

  // Scale the view by k around a map point that stays under the same screen spot.
  function zoomAt(k, [px, py]) {
    cancelAnimationFrame(anim); goal = null; fitted = null;
    const [x, y, w, h] = view;
    render(clamp([px - (px - x) * k, py - (py - y) * k, w * k, h * k]));
  }

  function showHint(text, ms) {
    hint.textContent = text;
    hint.hidden = false;
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => { hint.hidden = true; }, ms || 1400);
  }

  svg.addEventListener('wheel', e => {
    if (!view) return;
    if (!e.ctrlKey && !e.metaKey) { showHint('Pinch, or hold Ctrl while scrolling, to zoom'); return; }
    e.preventDefault();
    zoomAt(Math.exp(Math.max(-50, Math.min(50, e.deltaY)) * 0.004), toMap(e.clientX, e.clientY));
    settleSoon(350);
  }, {passive: false});

  svg.addEventListener('dblclick', e => {
    if (!view) return;
    e.preventDefault();
    const target = clamp((() => { const [px, py] = toMap(e.clientX, e.clientY), [, , w, h] = view; return [px - w / 4, py - h / 4, w / 2, h / 2]; })());
    fitted = null;
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
      cancelAnimationFrame(anim); goal = null; fitted = null;
      render(clamp([nx, ny, nw, nh]));
      dragged = true;
    } else if (pointers.size === 1 && gesture.start && svg.classList.contains('free')) {
      const dx = e.clientX - gesture.start[0], dy = e.clientY - gesture.start[1];
      if (!dragged && Math.hypot(dx, dy) < 5) return;
      // On touch screens one finger scrolls the page, so moving the map takes two.
      if (coarse.matches && e.pointerType === 'touch') { if (!gesture.hinted) { gesture.hinted = true; showHint('Use two fingers to move the map'); } return; }
      if (!dragged) svg.setPointerCapture(e.pointerId);
      dragged = true;
      const [x, y, w, h] = gesture.view;
      cancelAnimationFrame(anim); goal = null; fitted = null;
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
  // A drag captures the pointer, so a district may miss its pointerleave; leaving the map always clears the outline.
  svg.addEventListener('pointerleave', () => showHover(null));
  svg.addEventListener('pointercancel', endPointer);
  // A drag ends with a click on whatever district is under the finger; ignore it.
  svg.addEventListener('click', e => { if (dragged) { e.stopPropagation(); dragged = false; } }, true);

  function zoomBy(k) {
    if (!view) return;
    const [x, y, w, h] = goal || view, cx = x + w / 2, cy = y + h / 2;
    fitted = null;
    animateTo(clamp([cx - w * k / 2, cy - h * k / 2, w * k, h * k]));
    settleSoon(500, k > 1);
  }
  document.getElementById('zoom-in').addEventListener('click', () => zoomBy(0.5));
  document.getElementById('zoom-out').addEventListener('click', () => zoomBy(2));

  function pick(row) {
    picked = row;
    for (const p of svg.querySelectorAll('[data-geo]')) p.classList.toggle('picked', Boolean(row) && p.dataset.geo === row.geo);
    for (const d of districts) d.path.setAttribute('aria-pressed', String(d.row === row));
    for (const p of pickMarks) p.setAttribute('d', row ? row.d : '');
    document.getElementById('pick-name').textContent = row ? row.name : 'Tap an area on the map';
    document.getElementById('pick-island').textContent = row ? row.islands : 'No district picked yet';
    showPickValue();
    go.disabled = !row;
    list.value = row ? row.geo : '';
    declutter();
    updateStop();
  }

  // Share of a district's box inside the view it is heading to.
  function shown(d) { const v = goal || view, b = d.box; return overlap(v, b) / (b[2] * b[3]); }

  function setStop(d) {
    if (d === stop) return;
    if (stop) stop.path.setAttribute('tabindex', '-1');
    stop = d;
    if (d) d.path.setAttribute('tabindex', '0');
  }

  // Tab reaches the map at one district: the focused one (unless focus is leaving the map), else the picked one,
  // a ZIP match or the last stop when fully in view, else the first fully in view, so focus never lands out of sight.
  function updateStop(leaving) {
    if (!districts.length) return;
    // Before the first frame has a view, Tab still reaches the map at its first district.
    if (!view) { setStop(stop || districts[0]); return; }
    const full = d => d && shown(d) > 0.99 ? d : null;
    const focused = !leaving && districts.find(d => d.path === document.activeElement);
    const mostShown = () => districts.reduce((a, d) => (shown(d) > shown(a) ? d : a));
    setStop(focused || full(districts.find(d => d.row === picked)) || districts.find(d => full(d) && d.path.classList.contains('zip-hit')) ||
      full(stop) || districts.find(full) || mostShown());
  }

  // Arrow keys move between districts in number order, zooming to a district's island when it is out of view or too small to see.
  function onKey(e, d) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (picked === d.row) choose(d.row.geo, d.row.name); else pick(d.row); return; }
    const i = districts.indexOf(d), last = districts.length - 1;
    const to = {ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: last}[e.key];
    if (to === undefined || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const next = districts[Math.max(0, Math.min(last, to))], r = next.path.getBoundingClientRect();
    setStop(next);
    next.path.focus({preventScroll: true});
    if (shown(next) < 0.99 || Math.min(r.width, r.height) < 16) zoom(next.row.group);
  }

  // The keyboard focus ring and the mouse hover outline are drawn over every district, so neighbors drawn later never
  // cover half of them. The focus ring is a white halo under a dark line, which shows on light and dark fills alike;
  // the hover outline is a thinner dark line, so the two never look the same. On a shaded map, where fills show figures,
  // the picked district gets a white line edged in ink and a ZIP code's districts a dashed outline instead of new fills.
  const hoverLine = el('path', {class: 'hover-line'}), focusRing = [el('path', {class: 'ring-halo'}), el('path', {class: 'ring-line'})];
  const pickMarks = [el('path', {class: 'pick-casing'}), el('path', {class: 'pick-line'})], zipLines = el('g');
  // On a shaded map the hover line gets a white edge under it, so it shows on dark shades too.
  const hoverHalo = el('path', {class: 'hover-halo'});
  const ring = el('g', {class: 'focus-ring', 'aria-hidden': 'true'});
  ring.append(zipLines, ...pickMarks, hoverHalo, hoverLine, ...focusRing);
  function showRing(path) {
    const on = Boolean(path) && path.matches(':focus-visible');
    for (const p of focusRing) p.setAttribute('d', on ? path.getAttribute('d') : '');
  }
  // Only a mouse or pen hovers; a finger would leave the outline behind after a tap.
  function showHover(path, e) {
    if (e && e.pointerType === 'touch') return;
    for (const p of [hoverHalo, hoverLine]) p.setAttribute('d', path ? path.getAttribute('d') : '');
  }

  function draw() {
    const rows = data.chambers[chamber];
    const shapes = el('g'), labels = el('g', {class: 'labels', 'aria-hidden': 'true'}), pairs = [];
    for (const row of rows) {
      // WebKit does not name a button from an SVG title, so the name is set directly; the title is the hover tooltip.
      const name = `${row.name}, ${row.islands}`;
      const path = el('path', {d: row.d, class: 'district', tabindex: '-1', role: 'button', 'aria-pressed': 'false', 'aria-label': name});
      path.dataset.geo = row.geo;
      const title = el('title'); title.textContent = name; path.append(title);
      path.addEventListener('click', () => pick(row));
      path.addEventListener('focus', () => showRing(path));
      path.addEventListener('blur', () => showRing(null));
      path.addEventListener('pointerenter', e => showHover(path, e));
      path.addEventListener('pointerleave', e => showHover(null, e));
      shapes.append(path);
      const text = el('text', {x: row.label[0], y: row.label[1], class: 'label'});
      text.dataset.geo = row.geo;
      text.textContent = row.name.replace(/\D+/g, '');
      labels.append(text);
      pairs.push([path, text, row]);
    }
    showRing(null);
    showHover(null);
    marks = el('g', {class: 'marks', 'aria-hidden': 'true'});
    svg.replaceChildren(defs, shapes, marks, ring, labels);
    stop = null;
    districts = pairs.map(([path, , row]) => { const b = path.getBBox(); return {path, row, box: [b.x, b.y, b.width, b.height]}; });
    for (const d of districts) d.path.addEventListener('keydown', e => onKey(e, d));
    labelInfo = pairs.map(([, text, row], i) => ({text, x: row.label[0], y: row.label[1], size: districts[i].box[2] * districts[i].box[3]}));
    list.replaceChildren(new Option('Choose a district…', ''));
    const byGroup = {};
    for (const row of rows) (byGroup[row.group] ||= []).push(row);
    for (const [key, info] of Object.entries(data.groups)) {
      if (!byGroup[key]) continue;
      const og = document.createElement('optgroup'); og.label = info.label;
      for (const row of byGroup[key]) og.append(new Option(row.name, row.geo));
      list.append(og);
    }
    pick(null);
    showArea();
    loadShade();
  }

  const span = (text, className) => { const s = document.createElement('span'); s.textContent = text; if (className) s.className = className; return s; };

  // Highlight the districts an area's residents live in, a ZIP code typed here or a place found by the search,
  // and zoom to them. The area holds its label and, for each chamber, [district, share of residents] pairs.
  function showArea() {
    zipMatches.replaceChildren();
    zipLines.replaceChildren();
    for (const p of svg.querySelectorAll('.zip-hit')) p.classList.remove('zip-hit');
    svg.classList.toggle('zip-mode', Boolean(area));
    if (!area) { declutter(); return; }
    const rowsByGeo = new Map(data.chambers[chamber].map(r => [r.geo, r]));
    const hits = area[chamber].map(([geo, share]) => ({row: rowsByGeo.get(geo), share})).filter(h => h.row);
    const kind = chamber === 'house' ? 'House' : 'Senate';
    zipStatus.textContent = hits.length === 1 ? `${area.label} is all in ${hits[0].row.name}.` : `${area.label} spans ${hits.length} ${kind} districts. Share of its residents in each:`;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const main = hits.some(h => h.share >= 0.05) ? hits.filter(h => h.share >= 0.05) : hits;
    for (const {row, share} of hits) {
      for (const n of svg.querySelectorAll(`[data-geo="${row.geo}"]`)) n.classList.add('zip-hit');
      zipLines.append(el('path', {class: 'zip-halo', d: row.d}), el('path', {class: 'zip-line', d: row.d}));
      const path = svg.querySelector(`.district[data-geo="${row.geo}"]`);
      const b = path.getBBox();
      if (main.some(h => h.row === row)) { x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height); }
      if (hits.length > 1) {
        const chip = document.createElement('button');
        chip.type = 'button'; chip.className = `match ${chamber}`;
        const label = `${kind} ${row.name.replace(/\D+/g, '')}`, part = share < 0.01 ? '<1%' : `${Math.round(share * 100)}%`;
        // The spoken name is the visible text with the rest of the sentence hidden after it.
        chip.append(span(label), ' ', span(part, 'share'), span(` of ${area.label} residents`, 'visually-hidden'));
        chip.addEventListener('click', () => pick(row));
        zipMatches.append(chip);
      }
    }
    const padX = (x1 - x0) * 0.12 + 2, padY = (y1 - y0) * 0.12 + 2, box = [x0 - padX, y0 - padY, x1 - x0 + 2 * padX, y1 - y0 + 2 * padY];
    markTab(null);
    fitted = () => clamp(fit(box, true));
    animateTo(fitted());
    if (hits.length === 1) pick(hits[0].row);
  }

  document.getElementById('zip-form').addEventListener('submit', e => {
    e.preventDefault();
    if (!data) return;
    const zip = document.getElementById('zip').value.replace(/\D/g, '');
    area = null;
    if (!/^\d{5}$/.test(zip)) { showArea(); zipStatus.textContent = 'Enter a 5-digit ZIP code.'; return; }
    const entry = data.zips[zip];
    if (!entry) { showArea(); zipStatus.textContent = `No map area for ZIP ${zip}. Hawaiʻi home ZIPs start with 967 or 968, and PO box ZIPs have no area. Try the ZIP where you live.`; return; }
    if (!entry.house.length) { showArea(); zipStatus.textContent = `ZIP ${zip} had no residents in the 2020 census, so it has no district. Try the ZIP where you live.`; return; }
    area = {label: `ZIP ${zip}`, ...entry};
    pick(null);
    showArea();
  });
  document.getElementById('zip').addEventListener('input', e => { if (!e.target.value.trim() && area) { area = null; zipStatus.textContent = ''; showArea(); } });

  const html = (name, text, className) => { const n = document.createElement(name); if (text) n.textContent = text; if (className) n.className = className; return n; };
  // Fill an element with text, marking the Hawaiian words for groups of people so screen readers can pronounce them,
  // as app.js does.
  const HAWAIIAN = /(?<![\p{L}\p{M}])(kūpuna|keiki|ʻohana)(?![\p{L}\p{M}])/giu;
  function says(n, text) {
    let at = 0;
    n.replaceChildren();
    for (const m of text.matchAll(HAWAIIAN)) {
      const word = html('span', m[0]);
      word.lang = 'haw';
      n.append(text.slice(at, m.index), word);
      at = m.index + m[0].length;
    }
    n.append(text.slice(at));
    return n;
  }

  // A source link opens in a new tab and says so.
  function sourceLink(text, href) {
    if (!href || !/^https?:\/\//.test(href)) return html('span', text);
    const a = html('a', text, 'ext');
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.append(html('span', ' (opens in a new tab)', 'visually-hidden'));
    return a;
  }

  // A district's figure for the chosen measure. A district the store has no row for has no figure.
  function figureOf(row) {
    if (!shading) return null;
    return figures.get(row.geo) || {geo: row.geo, name: row.name, value: MISSING, spread: '', status: MISSING, shade: null, reason: '', caution: '', text: `${shading.label}: no figure.`};
  }

  // Color the map by the chosen measure, for the chamber shown, or go back to the plain map.
  function loadShade() {
    const indicator = shadeSelect.value, ask = ++shadeAsk;
    shadeStatus.textContent = '';
    if (!indicator || !data) {
      if (shading) shadeLive.textContent = 'The map is plain again.';
      applyShade(null);
      return;
    }
    const key = `${indicator} ${chamber}`;
    if (shadeCache.has(key)) { applyShade(shadeCache.get(key)); return; }
    applyShade(null);
    shadeStatus.textContent = 'Loading the figures…';
    fetch(`/api/map?indicator=${encodeURIComponent(indicator)}&chamber=${chamber}`)
      .then(r => r.json().catch(() => ({})).then(body => { if (!r.ok || !body.districts) throw new Error(body.error || ''); return body; }))
      .then(body => { shadeCache.set(key, body); if (ask === shadeAsk) { shadeStatus.textContent = ''; applyShade(body); } })
      .catch(err => { if (ask === shadeAsk) shadeStatus.textContent = err.message || 'The figures could not load. Check your connection and try again.'; });
  }

  function applyShade(body) {
    shading = body && body.chamber === chamber ? body : null;
    figures = new Map(shading ? shading.districts.map(d => [d.geo, d]) : []);
    svg.classList.toggle('shaded', Boolean(shading));
    if (marks) marks.replaceChildren();
    for (const d of districts) {
      const fig = figureOf(d.row);
      let name = `${d.row.name}, ${d.row.islands}`;
      d.path.classList.remove(...SHADE_CLASSES);
      if (fig) {
        d.path.classList.add(fig.shade ? `q${fig.shade}` : 'none');
        if (fig.status !== 'ok') marks.append(el('path', {d: d.row.d, class: fig.status !== 'unreliable' ? 'none' : fig.shade >= DARK_SHADE ? 'unreliable on-dark' : 'unreliable'}));
        name += fig.status === MISSING ? ': no figure' : `: ${fig.value} ${fig.spread}`.trimEnd() + (fig.status === 'unreliable' ? ', unreliable estimate' : '');
      }
      d.path.setAttribute('aria-label', name);
      d.path.querySelector('title').textContent = name;
    }
    showLegend();
    showTable();
    showPickValue();
    if (shading) shadeLive.textContent = `${shading.title}, now shown on the map.`;
  }

  // Every district on the map with its figure: highest first, then those with no figure.
  function allFigures() {
    const rows = data.chambers[chamber], known = new Set(shading.districts.map(d => d.geo));
    return [...shading.districts, ...rows.filter(r => !known.has(r.geo)).map(figureOf)];
  }

  function legendKey(swatch, text, spoken) {
    const li = html('li'), mark = html('span', '', `swatch ${swatch}`);
    mark.setAttribute('aria-hidden', 'true');
    li.append(mark);
    if (spoken) li.append(html('span', spoken, 'visually-hidden'));
    li.append(text);
    return li;
  }

  // The legend: each shade's range, low to high, the marks for unreliable estimates and missing figures, how to read
  // the shades, and the source, year and state figure.
  function showLegend() {
    legend.hidden = !shading;
    legend.replaceChildren();
    if (!shading) return;
    legend.dataset.ramp = shading.chamber;
    const title = html('p', '', 'legend-title');
    title.append(says(html('strong'), shading.label), `, by ${chamber === 'house' ? 'House' : 'Senate'} district`);
    const keys = html('ul', '', 'legend-keys'), last = shading.shades.length - 1;
    shading.shades.forEach((s, i) => keys.append(legendKey(`q${s.shade}`, s.text, i === 0 && last ? 'Lightest shade: ' : i === last && last ? 'Darkest shade: ' : `Shade ${i + 1}: `)));
    const all = allFigures();
    if (all.some(f => f.status === 'unreliable')) keys.append(legendKey('unreliable', 'Unreliable estimate'));
    if (all.some(f => f.status === MISSING)) keys.append(legendKey('none', 'No figure'));
    legend.append(title, keys, html('p', `${shading.how} ${shading.overlap}`, 'legend-note'));
    for (const text of [shading.unreliable, shading.caveat]) if (text) legend.append(html('p', text, 'legend-note'));
    const source = html('p', '', 'legend-source');
    source.append('Source: ');
    shading.sources.forEach((s, i) => source.append(...(i ? ['; '] : []), sourceLink(s.name, s.url), `, ${s.year}`));
    source.append('.');
    if (shading.state) source.append(` Hawaiʻi as a whole: ${`${shading.state.value} ${shading.state.spread}`.trim()}.`);
    legend.append(source);
  }

  // The same figures as a table, folded under the map.
  function showTable() {
    table.hidden = !shading;
    const body = document.getElementById('map-table-body');
    body.replaceChildren();
    if (!shading) return;
    says(document.getElementById('map-table-caption'), `${shading.title}, highest first`);
    const all = allFigures(), notes = all.some(f => f.status !== 'ok');
    // The note column shows only when some district has an unreliable estimate or no figure.
    document.getElementById('map-table-note').hidden = !notes;
    for (const f of all) {
      const tr = html('tr'), th = html('th', f.name);
      th.scope = 'row';
      const missing = f.status === MISSING, note = missing ? ['No figure', f.reason].filter(Boolean).join(': ') : f.caution;
      tr.append(th, html('td', missing ? 'Not available' : f.value), html('td', missing ? '' : f.spread || 'None published'));
      if (notes) tr.append(html('td', note, 'note'));
      body.append(tr);
    }
  }

  // The picked district's figure, margin and how it compares with the state, under its name. Screen readers hear it.
  function showPickValue() {
    const fig = picked && figureOf(picked);
    says(pickValue, fig ? fig.text : '');
    pickValue.hidden = !fig;
  }

  shadeSelect.addEventListener('change', loadShade);

  function setChamber(name) {
    chamber = name;
    document.querySelectorAll('[data-chamber]').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.chamber === name)));
    draw();
  }
  document.querySelectorAll('[data-chamber]').forEach(b => b.addEventListener('click', () => { if (data && b.dataset.chamber !== chamber) setChamber(b.dataset.chamber); }));

  // Show the district the chat asks about, unless the map already has it picked.
  // While the reader works in the map, wait until focus leaves it.
  const mapSection = document.getElementById('district-map');
  function follow(geo, now) {
    if (!now && mapSection.contains(document.activeElement)) { waiting = geo; return; }
    waiting = null;
    if (picked && picked.geo === geo) return;
    area = null;
    zipStatus.textContent = '';
    document.getElementById('zip').value = '';
    const kind = geo.split(':')[0];
    if (kind !== chamber) setChamber(kind); else showArea();
    const row = data.chambers[chamber].find(r => r.geo === geo);
    if (row) { pick(row); zoom(row.group); }
  }
  document.addEventListener('klear:district', e => { if (data) follow(e.detail.geo); else waiting = e.detail.geo; });
  // The search sends a place's districts here when the reader asks to see them on the map.
  let waitingArea = null;
  function showFound(found) {
    waiting = null;
    area = found;
    document.getElementById('zip').value = '';
    pick(null);
    showArea();
  }
  document.addEventListener('klear:area', e => { if (data) showFound(e.detail); else waitingArea = e.detail; });
  list.addEventListener('change', () => { const row = data && data.chambers[chamber].find(r => r.geo === list.value); pick(row || null); if (row) zoom(row.group); });
  go.addEventListener('click', () => { if (picked) choose(picked.geo, picked.name); });
  // Refit when the frame itself changes size, not when only the window height does (phone toolbars).
  window.addEventListener('resize', () => {
    if (!data || !view) return;
    const r = frameEl.getBoundingClientRect(), size = `${r.width} ${r.height}`;
    if (size === frameSize) return;
    frameSize = size;
    const next = fitted ? fitted() : clamp(fit(goal || view));
    cancelAnimationFrame(anim); goal = null;
    render(next);
  });
  mapSection.addEventListener('focusout', e => { if (waiting && data && !mapSection.contains(e.relatedTarget)) follow(waiting, true); });
  // Keep a keyboard-focused island tab inside the scrolling row on phones. Taps scroll the row through markTab instead.
  tabs.addEventListener('focusin', e => {
    if (tabs.scrollWidth > tabs.clientWidth && e.target.matches(':focus-visible')) e.target.scrollIntoView({inline: 'nearest', block: 'nearest'});
  });
  svg.addEventListener('focusin', e => {
    if (!svg.contains(e.relatedTarget) && svg.querySelector(':focus-visible')) showHint('Use the arrow keys to move between districts', 3000);
  });
  svg.addEventListener('focusout', e => { if (!svg.contains(e.relatedTarget)) updateStop(true); });

  fetch('/districts.json').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(json => {
    data = json;
    for (const [key, info] of Object.entries(data.groups)) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = info.label; b.dataset.group = key;
      b.addEventListener('click', () => zoom(key));
      tabs.append(b);
    }
    draw();
    zoom('all');
    if (waitingArea) showFound(waitingArea);
    else if (waiting) follow(waiting);
    document.getElementById('map-loading').hidden = true;
  }).catch(() => { document.getElementById('map-loading').textContent = 'The map could not load. Use the search or the list below.'; });
})();
