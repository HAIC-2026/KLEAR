// Interactive House and Senate district map. On the Find page it finds a district: the search beside it highlights a
// place's districts, and a click on a district opens its page, where the same map, zoomed to the district, sits beside
// its name. On the rankings page it is colored by a measure, with the district ranking beside it: a click picks a
// district, marked in the ranking, and a second click opens it. Each page holds only the controls it uses, so every
// optional element may be missing.
(() => {
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('map');
  const frameEl = svg.parentElement;
  const tabs = document.getElementById('island-tabs');
  const hint = document.getElementById('map-hint');
  const islandHint = document.getElementById('map-island-hint');
  const zoomEl = document.querySelector('.zoom-buttons');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const coarse = window.matchMedia('(pointer: coarse)');
  const SNAP_SHOWN = 0.5, SNAP_FILL = 0.2, SNAP_ALL = 0.8, MIN_WIDTH = 12, DISTRICT_PX = 16, OVERVIEW_PAD = 8;
  // On Find the switch already reflects the chamber saved for this tab.
  let chamber = document.querySelector('.chamber-switch [aria-pressed=true]')?.dataset.chamber || 'house';
  let data = null, group = 'all', picked = null, view = null, goal = null, anim = 0, settleTimer = 0, hintTimer = 0;
  const pointers = new Map();
  let gesture = null, dragged = false, area = null, waiting = null, captions = [], districts = [], stop = null, fitted = null, frameSize = '';
  const zipStatus = document.getElementById('zip-status') || {}, zipMatches = document.getElementById('zip-matches');
  // Coloring the map by a measure: the figures for the chosen measure and chamber, by district, or null for the plain map.
  const shadeSelect = document.getElementById('map-shade'), legend = document.getElementById('map-legend'), table = document.getElementById('map-table');
  const shadeStatus = document.getElementById('shade-status') || {}, shadeLive = document.getElementById('shade-live') || {}, pickValue = document.getElementById('pick-value');
  const SHADE_CLASSES = ['q1', 'q2', 'q3', 'q4', 'q5', 'none'], MISSING = 'not available', shadeCache = new Map();
  let shading = null, figures = new Map(), shadeAsk = 0, marks = null, focusedGeo = null;

  function el(tag, attrs) { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; }

  // The whole chain where it lies. The island tabs zoom the map to each island and to urban Honolulu, where a dozen
  // House districts share a few miles; the list and the arrow keys take a district there to urban Honolulu too.
  const CAPTIONS = [['kauai', 'below'], ['oahu', 'above'], ['maui', 'left'], ['hawaii', 'left']];
  const CAPTION_HEIGHT = 26;
  const islandOf = row => row.group === 'honolulu' ? 'oahu' : row.group;
  const inBox = ([x, y], [bx, by, bw, bh]) => x >= bx && x <= bx + bw && y >= by && y <= by + bh;
  function layout(json) {
    const city = json.groups.honolulu.box, chambers = {};
    for (const [chamber, rows] of Object.entries(json.chambers)) {
      chambers[chamber] = rows.map(row => ({...row, group: row.group === 'oahu' && inBox(row.label, city) ? 'honolulu' : row.group}));
    }
    return {...json, chambers};
  }
  const outlineOf = row => (row ? row.d : '');

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

  // Fit to the SVG's inner size so the same scale applies to both axes. On a narrow frame,
  // closer views leave room at the right for the zoom controls.
  function fit([x, y, w, h], clear = false, pad = 0) {
    const r = svg.getBoundingClientRect();
    const width = r.width || 300, height = r.height || 200;
    const inset = clear && r.width && r.width < 480 ? Math.max(0, r.right - zoomEl.getBoundingClientRect().left + 6) : 0;
    const scale = Math.min(Math.max(1, width - inset - 2 * pad) / w, Math.max(1, height - 2 * pad) / h);
    return [x + w / 2 - (width - inset) / (2 * scale), y + h / 2 - height / (2 * scale), width / scale, height / scale];
  }

  function target(name) { return fit(data.groups[name].box, name !== 'all', name === 'all' ? OVERVIEW_PAD : 0); }

  const scaleOf = r => Math.min(r.width / view[2], r.height / view[3]);

  // Keep the zoom between the closest allowed view and the whole chain, and the center over the islands,
  // with room on narrow frames to move any island left of the zoom buttons.
  function clamp(v) {
    const all = target('all'), room = fit(data.groups.all.box, true, OVERVIEW_PAD);
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
    const r = svg.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const statewide = view[2] >= target('all')[2] * 0.98, scale = scaleOf(r);
    svg.classList.add('zoomed');
    svg.classList.toggle('statewide', statewide);
    svg.classList.toggle('free', !statewide);
    islandHint.hidden = !statewide;
    if (statewide) placeCaptions(r, scale);
    sizeLabels(scale);
    declutter(r, scale);
    updateStop();
  }

  function animateTo(target, length = 450) {
    cancelAnimationFrame(anim);
    clearTimeout(settleTimer);
    goal = target;
    const from = view || target, start = performance.now(), ms = reduceMotion || !view ? 0 : length;
    const step = now => {
      const t = ms ? Math.min(1, (now - start) / ms) : 1, e = 1 - Math.pow(1 - t, 3);
      render(from.map((v, i) => v + (target[i] - v) * e));
      if (t < 1) anim = requestAnimationFrame(step); else goal = null;
    };
    anim = requestAnimationFrame(step);
  }

  function sizeLabels(scale) {
    const k = 1 / scale;
    svg.style.setProperty('--label', `${(12 * k).toFixed(3)}px`);
    for (const p of stripeSets) p.setAttribute('patternTransform', `scale(${k.toFixed(5)}) rotate(45)`);
    dots.setAttribute('patternTransform', `scale(${k.toFixed(5)})`);
  }

  // Hide numbers that cannot fit at statewide zoom, would be clipped, or would overlap a button or
  // a number that matters more: the picked district, then ZIP hits, then larger districts.
  function declutter(r = svg.getBoundingClientRect(), scale = view && scaleOf(r)) {
    if (!view || !scale) return;
    const statewide = svg.classList.contains('statewide');
    const bounds = (el, pad = 0) => {
      const b = el.getBoundingClientRect();
      return [b.left - pad, b.top - pad, b.width + 2 * pad, b.height + 2 * pad];
    };
    const kept = [bounds(zoomEl), ...(statewide ? captions.map(c => bounds(c.button)) : [])];
    const rank = d => (d.row === picked ? 2e12 : 0) + (d.text.classList.contains('zip-hit') ? 1e12 : 0) + d.box[2] * d.box[3];
    // Measure before changing visibility. The three extra pixels include the label's outline.
    const labels = [...districts].sort((a, b) => rank(b) - rank(a)).map(d => [d, bounds(d.text, 3)]);
    for (const [d, b] of labels) {
      const [x, y, w, h] = b, inside = x >= r.left && y >= r.top && x + w <= r.right && y + h <= r.bottom;
      const crowded = (statewide && d.box[2] * scale < DISTRICT_PX) || !inside || kept.some(other => overlap(b, other));
      d.text.classList.toggle('crowded', crowded);
      if (!crowded) kept.push(b);
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
  function zoom(name) { hideTip(); markTab(name); focusedGeo = null; fitted = () => target(name); animateTo(fitted()); }

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
    if (!close && view[2] >= target('all')[2] * SNAP_ALL) return zoom('all');
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
  svg.addEventListener('pointerleave', () => { showHover(null); hideTip(); });
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
    for (const p of pickMarks) p.setAttribute('d', outlineOf(row));
    showPickValue();
    markRow();
    declutter();
    updateStop();
  }

  // A click or Enter on a district. On the rankings page the first picks it, to read its figure and find it in the
  // ranking, and a second opens its page; on Find and a district's page it opens the district's page at once.
  function activate(row) {
    if (shadeSelect) {
      if (picked !== row) { pick(row); return; }
      const [kind, id] = row.geo.split(':');
      location.href = `/district/${kind}-${Number(id)}`;
      return;
    }
    pick(row);
    if (typeof openDistrict === 'function') openDistrict(row.geo, row.name);
  }

  // A tiny target, or any target on an island where most districts are tiny, opens the island first.
  // Keyboard activation can still pick a named district directly.
  function islandToZoom(d) {
    if (!svg.classList.contains('statewide')) return null;
    const scale = scaleOf(svg.getBoundingClientRect()), island = islandOf(d.row);
    const small = ({box: [, , w, h]}) => Math.min(w, h) * scale < DISTRICT_PX;
    if (small(d)) return island;
    const neighbors = districts.filter(other => islandOf(other.row) === island);
    return neighbors.filter(small).length > neighbors.length / 2 ? island : null;
  }

  // The view around one district, with room for its neighbors: the district fills about half of it.
  function around(row) {
    const d = districts.find(x => x.row === row);
    const [x, y, w, h] = d ? d.box : data.groups[row.group].box, k = 2.2, cx = x + w / 2, cy = y + h / 2;
    const side = Math.max(w * k, h * k, MIN_WIDTH * 1.5);
    return clamp(fit([cx - side / 2, cy - side / 2, side, side], true));
  }
  // A district's page: the map zoomed to the district, picked.
  function focusRow(row, length) {
    pick(row);
    markTab(null);
    focusedGeo = row.geo;
    fitted = () => around(row);
    animateTo(fitted(), length);
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
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(d.row); return; }
    const i = districts.indexOf(d), last = districts.length - 1;
    const to = {ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: last}[e.key];
    if (to === undefined || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const next = districts[Math.max(0, Math.min(last, to))], r = next.path.getBoundingClientRect();
    setStop(next);
    next.path.focus({preventScroll: true});
    if (shown(next) < 0.99 || Math.min(r.width, r.height) < DISTRICT_PX) zoom(next.row.group);
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
    const on = Boolean(path) && path.matches(':focus-visible'), d = on && districts.find(x => x.path === path);
    for (const p of focusRing) p.setAttribute('d', on ? (d ? outlineOf(d.row) : path.getAttribute('d')) : '');
  }
  // Only a mouse or pen hovers; a finger would leave the outline behind after a tap.
  function showHover(path, e) {
    if (e && e.pointerType === 'touch') return;
    for (const p of [hoverHalo, hoverLine]) p.setAttribute('d', path ? path.getAttribute('d') : '');
  }

  // A label beside the pointer says what a click does. When a district can be picked, it also gives the
  // district's name and, on Rankings, its figure.
  const tip = document.createElement('div');
  tip.className = 'map-tip';
  tip.setAttribute('aria-hidden', 'true');
  tip.hidden = true;
  frameEl.append(tip);
  function showTip(d, e) {
    if (!e || e.pointerType === 'touch') return;
    const {row} = d, island = islandToZoom(d), fig = figureOf(row);
    const action = island ? 'Click to zoom in' : !shadeSelect ? 'click to open' : picked === row ? 'click again to open' : 'click to pick';
    const key = `${row.geo} ${action} ${fig ? fig.value : ''}`;
    if (tip.dataset.key !== key) {
      tip.dataset.key = key;
      const name = document.createElement('strong'), where = document.createElement('span');
      name.textContent = island ? data.groups[island].label : fig ? `${row.name}: ${fig.value === MISSING ? 'no figure' : fig.value}` : row.name;
      where.textContent = island ? action : `${row.islands} · ${action}`;
      tip.replaceChildren(name, where);
    }
    tip.hidden = false;
    const r = frameEl.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
    if (x + w > r.width - 4) x = e.clientX - r.left - w - 14;
    if (y + h > r.height - 4) y = e.clientY - r.top - h - 14;
    // Custom properties the style sheet turns into a position, so no style is written here.
    tip.style.setProperty('--tip-x', `${Math.max(4, x)}px`);
    tip.style.setProperty('--tip-y', `${Math.max(4, y)}px`);
  }
  function hideTip() { tip.hidden = true; delete tip.dataset.key; }

  // The island buttons go before the districts in the Tab order. When a caption is activated
  // with focus, hand focus into that island so the arrow keys work there immediately.
  function drawCaptions() {
    const layer = el('g', {class: 'frame-captions'}), counts = {};
    for (const row of data.chambers[chamber]) {
      const g = islandOf(row);
      counts[g] = (counts[g] || 0) + 1;
    }
    const word = chamber === 'house' ? 'House' : 'Senate';
    captions = CAPTIONS.map(([g, side]) => {
      const label = `${data.groups[g].label} · ${counts[g]} ${word}`;
      const button = el('g', {class: 'frame-caption', role: 'button', tabindex: '0', 'aria-label': `${label} districts, zoom in`});
      const rect = el('rect', {rx: 4, height: CAPTION_HEIGHT}), text = el('text', {x: 6, y: CAPTION_HEIGHT / 2});
      text.textContent = label;
      const activateCaption = () => {
        zoom(g);
        if (document.activeElement !== button) return;
        const next = districts.find(d => islandOf(d.row) === g && shown(d) > 0.99);
        if (next) { setStop(next); next.path.focus({preventScroll: true}); }
      };
      button.addEventListener('click', activateCaption);
      button.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activateCaption(); }
      });
      button.append(rect, text);
      layer.append(button);
      return {button, rect, text, box: data.groups[g].box, side};
    });
    return layer;
  }

  // Position buttons in the water beside the islands, at a constant size in screen pixels.
  function placeCaptions(r, scale) {
    const offsetX = (r.width - view[2] * scale) / 2, offsetY = (r.height - view[3] * scale) / 2;
    const zoom = zoomEl.getBoundingClientRect(), gap = 6, edge = 4;
    const widths = captions.map(({text}) => Math.ceil(text.getComputedTextLength()) + 12);
    captions.forEach(({button, rect, box: [x, y, w, h], side}, i) => {
      const left = offsetX + (x - view[0]) * scale, top = offsetY + (y - view[1]) * scale;
      const width = widths[i];
      let bx = side === 'below' ? left : side === 'above' ? left + (w * scale - width) / 2 : left - width - gap;
      let by = side === 'below' ? top + h * scale + gap : side === 'above' ? top - CAPTION_HEIGHT - gap : top + (h * scale - CAPTION_HEIGHT) / 2 + gap;
      by = Math.max(edge, Math.min(by, r.height - CAPTION_HEIGHT - edge));
      const right = by < zoom.bottom - r.top + gap ? zoom.left - r.left - gap : r.width - edge;
      bx = Math.max(edge, Math.min(bx, right - width));
      rect.setAttribute('width', width);
      button.setAttribute('transform', `translate(${view[0] + (bx - offsetX) / scale} ${view[1] + (by - offsetY) / scale}) scale(${1 / scale})`);
    });
  }

  function draw() {
    const rows = data.chambers[chamber];
    const shapes = el('g'), labels = el('g', {class: 'labels', 'aria-hidden': 'true'}), islandButtons = drawCaptions();
    districts = [];
    for (const row of rows) {
      // WebKit does not name a button from an SVG title, so the name is set directly; the title is the hover tooltip.
      const name = `${row.name}, ${row.islands}`;
      const path = el('path', {d: row.d, class: 'district', tabindex: '-1', role: 'button', 'aria-pressed': 'false', 'aria-label': name});
      path.dataset.geo = row.geo;
      const title = el('title'); title.textContent = name; path.append(title);
      path.addEventListener('focus', () => showRing(path));
      path.addEventListener('blur', () => showRing(null));
      path.addEventListener('pointerenter', e => showHover(path, e));
      path.addEventListener('pointerleave', e => { showHover(null, e); hideTip(); });
      shapes.append(path);
      const text = el('text', {x: row.label[0], y: row.label[1], class: 'label'});
      text.dataset.geo = row.geo;
      text.textContent = row.name.replace(/\D+/g, '');
      labels.append(text);
      districts.push({path, text, row});
    }
    showRing(null);
    showHover(null);
    marks = el('g', {class: 'marks', 'aria-hidden': 'true'});
    svg.replaceChildren(defs, islandButtons, shapes, marks, ring, labels);
    stop = null;
    for (const d of districts) {
      const b = d.path.getBBox();
      d.box = [b.x, b.y, b.width, b.height];
      d.path.addEventListener('keydown', e => onKey(e, d));
      d.path.addEventListener('pointermove', e => showTip(d, e));
      d.path.addEventListener('click', e => {
        const island = e.detail && islandToZoom(d);
        if (island) { showHover(null); zoom(island); } else activate(d.row);
      });
    }
    // A district picked in this chamber stays picked when the map is drawn again.
    pick(picked && rows.includes(picked) ? picked : null);
    showArea();
    loadShade();
    if (view) render(view);
  }

  const span = (text, className) => { const s = document.createElement('span'); s.textContent = text; if (className) s.className = className; return s; };

  // Highlight the districts an area's residents live in, a ZIP code typed here or a place found by the search,
  // and zoom to them. The area holds its label and, for each chamber, [district, share of residents] pairs.
  function showArea() {
    if (zipMatches) zipMatches.replaceChildren();
    zipLines.replaceChildren();
    for (const p of svg.querySelectorAll('.zip-hit')) p.classList.remove('zip-hit');
    svg.classList.toggle('zip-mode', Boolean(area));
    if (!area) { declutter(); return; }
    const rowsByGeo = new Map(data.chambers[chamber].map(r => [r.geo, r]));
    const hits = area[chamber].map(([geo, share]) => ({row: rowsByGeo.get(geo), share})).filter(h => h.row);
    const kind = chamber === 'house' ? 'House' : 'Senate';
    zipStatus.textContent = hits.length === 1 ? `${area.label} is all in ${hits[0].row.name}.` : `${area.label} spans ${hits.length} ${kind} districts${zipMatches ? '. Share of its residents in each:' : ', outlined on the map.'}`;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const main = hits.some(h => h.share >= 0.05) ? hits.filter(h => h.share >= 0.05) : hits;
    for (const {row, share} of hits) {
      for (const n of svg.querySelectorAll(`[data-geo="${row.geo}"]`)) n.classList.add('zip-hit');
      zipLines.append(el('path', {class: 'zip-halo', d: outlineOf(row)}), el('path', {class: 'zip-line', d: outlineOf(row)}));
      const path = svg.querySelector(`.district[data-geo="${row.geo}"]`);
      const b = path.getBBox();
      if (main.some(h => h.row === row)) { x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height); }
      if (hits.length > 1 && zipMatches) {
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

  const zipForm = document.getElementById('zip-form');
  if (zipForm) zipForm.addEventListener('submit', e => {
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
  if (zipForm) document.getElementById('zip').addEventListener('input', e => { if (!e.target.value.trim() && area) { area = null; zipStatus.textContent = ''; showArea(); } });

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
    const indicator = shadeSelect ? shadeSelect.value : '', ask = ++shadeAsk;
    shadeStatus.textContent = '';
    if (!indicator || !data) {
      if (shading) shadeLive.textContent = 'The map is plain again.';
      showLoading(false);
      applyShade(null);
      return;
    }
    const key = `${indicator} ${chamber}`;
    if (shadeCache.has(key)) { showLoading(false); applyShade(shadeCache.get(key)); return; }
    applyShade(null);
    showLoading(true);
    fetch(`/api/map?indicator=${encodeURIComponent(indicator)}&chamber=${chamber}`)
      .then(r => r.json().catch(() => ({})).then(body => { if (!r.ok || !body.districts) throw new Error(body.error || ''); return body; }))
      .then(body => { shadeCache.set(key, body); if (ask === shadeAsk) { showLoading(false); applyShade(body); } })
      .catch(err => { if (ask === shadeAsk) { showLoading(false); shadeStatus.textContent = err.message || 'The figures could not load. Check your connection and try again.'; showTable(); } });
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
        if (fig.status !== 'ok') marks.append(el('path', {d: outlineOf(d.row), class: fig.status !== 'unreliable' ? 'none' : fig.shade >= DARK_SHADE ? 'unreliable on-dark' : 'unreliable'}));
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
    if (!legend) return;
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

  // The same figures as a ranked table beside the map, in the order the reader picks: highest or lowest first, with the
  // districts that have no figure last; by district number; or by island, the highest first on each. Each row starts
  // with the district's rank, 1 for the highest figure, shared by districts with the same figure. Each district's name
  // opens its page, pointing at its row outlines it on the map, and the district picked on the map is marked.
  const order = document.getElementById('map-order'), ORDERS = {high: 'highest first', low: 'lowest first', number: 'by district number', island: 'by island'};
  const ISLANDS = CAPTIONS.map(([g]) => g);
  const districtNumber = geo => Number(geo.split(':')[1]);
  function ordered() {
    const all = allFigures(), known = all.filter(f => f.status !== MISSING), none = all.filter(f => f.status === MISSING);
    const how = order ? order.value : 'high';
    if (how === 'low') known.reverse();
    if (how === 'number') return all.sort((a, b) => districtNumber(a.geo) - districtNumber(b.geo));
    return [...known, ...none];
  }
  // Each district's rank, highest first. Districts with the same figure share a rank, and the next takes the place
  // after them (1, 2, 2, 4); a district with no figure has none.
  function ranks() {
    const out = new Map();
    let last = null, rank = 0;
    shading.districts.forEach((f, i) => {
      if (f.status === MISSING) return;
      if (f.value !== last) { rank = i + 1; last = f.value; }
      out.set(f.geo, rank);
    });
    return out;
  }
  function showTable() {
    if (!table) return;
    table.hidden = !shading;
    const body = document.getElementById('map-table-body');
    body.replaceChildren();
    if (!shading) return;
    const how = order ? order.value : 'high';
    says(document.getElementById('map-table-caption'), `${shading.title}, ${ORDERS[how]}`);
    const all = ordered(), rankOf = ranks(), columns = 4;
    // Each figure gets a bar in its map shade, from zero, with its margin of error as a whisker; the same scale for
    // every row. The figure beside it says the same, so screen readers skip the bar.
    table.dataset.ramp = chamber;
    const known = all.filter(f => f.status !== MISSING && Number.isFinite(f.number));
    const ends = known.flatMap(f => [f.number, f.low, f.high]).filter(Number.isFinite);
    const floor = Math.min(0, ...ends), top = Math.max(...ends), across = top - floor;
    const at = v => `${(((v - floor) / across) * 100).toFixed(2)}%`;
    function figureBar(f) {
      if (!(across > 0) || f.status === MISSING || !Number.isFinite(f.number)) return null;
      const bar = html('span', '', 'rank-bar'), fill = html('span', '', `rank-fill q${f.shade || 1}`);
      bar.setAttribute('aria-hidden', 'true');
      fill.style.setProperty('--bar-w', at(f.number));
      bar.append(fill);
      if (Number.isFinite(f.low) && Number.isFinite(f.high) && f.high > f.low) {
        const whisker = html('span', '', 'rank-whisker');
        whisker.style.setProperty('--whisker-x', at(f.low));
        whisker.style.setProperty('--whisker-w', `${(((f.high - f.low) / across) * 100).toFixed(2)}%`);
        bar.append(whisker);
      }
      return bar;
    }
    // A red mark beside an unreliable estimate, explained under the table when there is one.
    document.getElementById('map-table-key').hidden = !all.some(f => f.status === 'unreliable');
    const rowOf = new Map(data.chambers[chamber].map(r => [r.geo, r]));
    const groups = how === 'island' ? ISLANDS.map(g => [data.groups[g].label, all.filter(f => rowOf.has(f.geo) && islandOf(rowOf.get(f.geo)) === g)]) : [['', all]];
    for (const [label, list] of groups) {
      if (!list.length) continue;
      if (label) {
        const tr = html('tr', '', 'rank-group'), th = html('th', label);
        th.scope = 'colgroup';
        th.colSpan = columns;
        tr.append(th);
        body.append(tr);
      }
      for (const f of list) {
        const tr = html('tr'), th = html('th'), row = rowOf.get(f.geo), name = html('a', f.name, 'rank-name'), [kind, id] = f.geo.split(':');
        tr.dataset.geo = f.geo;
        th.scope = 'row';
        name.href = `/district/${kind}-${Number(id)}`;
        th.append(name);
        // Pointing at a row outlines its district on the map.
        if (row) {
          tr.addEventListener('pointerenter', () => { for (const p of [hoverHalo, hoverLine]) p.setAttribute('d', outlineOf(row)); });
          tr.addEventListener('pointerleave', () => { for (const p of [hoverHalo, hoverLine]) p.setAttribute('d', ''); });
        }
        const missing = f.status === MISSING, figure = html('td', missing ? 'Not available' : f.value);
        if (missing && f.reason) figure.title = f.reason;
        if (f.status === 'unreliable') {
          const mark = html('span', '!', 'unreliable-mark');
          mark.setAttribute('role', 'img');
          mark.setAttribute('aria-label', 'Unreliable estimate');
          mark.title = f.caution || 'Unreliable estimate';
          figure.append(' ', mark);
        }
        const bar = figureBar(f);
        if (bar) figure.append(bar);
        const rank = html('td', rankOf.has(f.geo) ? String(rankOf.get(f.geo)) : '–', 'rank-num');
        if (!rankOf.has(f.geo)) rank.append(html('span', 'no rank', 'visually-hidden'));
        tr.append(rank, th, figure, html('td', missing ? '' : f.spread || 'None published'));
        body.append(tr);
      }
    }
    markRow(false);
  }
  if (order) order.addEventListener('change', () => { if (shading) showTable(); });
  // Mark the picked district's row, and scroll the table, not the page, to show it.
  function markRow(scroll = true) {
    if (!table) return;
    let mine = null;
    for (const tr of document.querySelectorAll('#map-table-body tr[data-geo]')) {
      const on = Boolean(picked) && tr.dataset.geo === picked.geo;
      tr.classList.toggle('picked', on);
      if (on) { tr.setAttribute('aria-current', 'true'); mine = tr; } else tr.removeAttribute('aria-current');
    }
    const box = table.querySelector('.table-scroll');
    if (!scroll || !mine || box.scrollHeight <= box.clientHeight) return;
    const r = mine.getBoundingClientRect(), b = box.getBoundingClientRect(), head = table.querySelector('thead').getBoundingClientRect().height;
    if (r.top < b.top + head || r.bottom > b.bottom) box.scrollTop += r.top - b.top - head - (b.height - head - r.height) / 2;
  }
  // While the figures load, as on every page: gray bars where the ranking and the legend will be, and the districts
  // gray on the map. Screen readers hear that the figures are loading.
  function showLoading(on) {
    if (!table) return;
    const bars = document.getElementById('map-table-loading'), scroller = table.querySelector('.table-scroll');
    if (on) table.setAttribute('aria-busy', 'true'); else table.removeAttribute('aria-busy');
    svg.classList.toggle('waiting', on);
    bars.hidden = !on;
    scroller.hidden = on;
    if (on) {
      table.hidden = false;
      shadeLive.textContent = 'Loading the figures…';
      bars.replaceChildren(...Array.from({length: 12}, () => skeleton(['w-20', 'w-60', 'w-30', 'w-45'], 'skeleton-line')));
      if (legend) { legend.hidden = false; legend.replaceChildren(skeleton(['w-45', 'w-80', 'w-60'])); }
    } else showLegend();
  }

  // What screen readers hear when a district is picked on the rankings map: its name, its figure with its margin and
  // how it compares with the state, and that picking it again opens it. Sighted readers see it marked in the ranking.
  function showPickValue() {
    if (!pickValue) return;
    const fig = picked && figureOf(picked);
    says(pickValue, fig ? `${picked.name}. ${fig.text} Press Enter again to open its page.` : '');
  }

  if (shadeSelect) shadeSelect.addEventListener('change', loadShade);

  // The House or Senate switch: over the Find page's map, or in the rankings page's map controls.
  const SWITCH = '.chamber-switch [data-chamber], .segmented [data-chamber]';
  function setChamber(name) {
    chamber = name;
    document.querySelectorAll(SWITCH).forEach(x => x.setAttribute('aria-pressed', String(x.dataset.chamber === name)));
    document.dispatchEvent(new CustomEvent('klear:chamber', {detail: name}));
    if (data) draw();
  }
  document.addEventListener('click', e => {
    const b = e.target.closest(SWITCH);
    if (b && b.dataset.chamber !== chamber) setChamber(b.dataset.chamber);
  });
  // A link such as /rankings?measure=pct_65_plus&chamber=senate&order=low, as a ranking answer gives, opens the map on
  // that measure, chamber and order. Anything the page does not offer is left as it is.
  if (shadeSelect) {
    const asked = new URLSearchParams(location.search), measure = asked.get('measure'), house = asked.get('chamber'), how = asked.get('order');
    if (measure && [...shadeSelect.options].some(o => o.value === measure)) shadeSelect.value = measure;
    if (house === 'house' || house === 'senate') setChamber(house);
    const orderSelect = document.getElementById('map-order');
    if (how && orderSelect && [...orderSelect.options].some(o => o.value === how)) orderSelect.value = how;
  }

  // Show the district the chat asks about, unless the map already has it picked.
  // While the reader works in the map, wait until focus leaves it.
  const mapSection = document.getElementById('district-map');
  function follow(geo, now) {
    if (!geo) return;
    if (!now && mapSection.contains(document.activeElement)) { waiting = geo; return; }
    waiting = null;
    if (picked && picked.geo === geo && focusedGeo === geo) return;
    area = null;
    zipStatus.textContent = '';
    if (zipForm) document.getElementById('zip').value = '';
    const kind = geo.split(':')[0];
    if (kind !== chamber) setChamber(kind); else showArea();
    const row = data.chambers[chamber].find(r => r.geo === geo);
    if (!row) return;
    pick(row);
    if (document.documentElement.dataset.page === 'district') focusRow(row, 600); else zoom(row.group);
  }
  document.addEventListener('klear:district', e => { if (data) follow(e.detail.geo); else waiting = e.detail.geo; });
  // A number pointed at in the list of every district is outlined on the map.
  document.addEventListener('klear:hover', e => {
    if (!data) return;
    const row = e.detail && data.chambers[chamber].find(r => r.geo === e.detail);
    for (const p of [hoverHalo, hoverLine]) p.setAttribute('d', row ? outlineOf(row) : '');
  });
  // The search sends a place's districts here when the reader asks to see them on the map.
  let waitingArea = null;
  function showFound(found) {
    waiting = null;
    const wasShowingArea = Boolean(area);
    area = found;
    if (zipForm) document.getElementById('zip').value = '';
    pick(null);
    if (!area) zipStatus.textContent = '';
    showArea();
    if (!area && wasShowingArea) zoom('all');
  }
  document.addEventListener('klear:area', e => { if (data) showFound(e.detail); else waitingArea = e.detail; });
  // Refit when the frame itself changes size, as the page moves between its views, but not when only the window
  // height does (phone toolbars).
  function refit(force) {
    if (!data || !view) return;
    const r = frameEl.getBoundingClientRect(), size = `${r.width} ${r.height}`;
    if (!r.width || (size === frameSize && !force)) return;
    frameSize = size;
    const next = fitted ? fitted() : clamp(fit(goal || view));
    cancelAnimationFrame(anim); goal = null;
    render(next);
  }
  new ResizeObserver(() => refit()).observe(frameEl);
  document.fonts.ready.then(() => { if (view) render(view); });

  // What the rest of the page asks of the map: zoom to a district before its page opens, fit the frame again in a new
  // layout, and go back to all the islands.
  let resolveReady;
  const ready = new Promise(r => { resolveReady = r; });
  window.klearMap = {
    ready,
    zoomTo(geo) {
      if (!data) return Promise.resolve();
      const kind = geo.split(':')[0];
      if (kind !== chamber) setChamber(kind);
      const row = data.chambers[chamber].find(r => r.geo === geo);
      if (!row) return Promise.resolve();
      area = null;
      zipStatus.textContent = '';
      showArea();
      focusRow(row, 650);
      return new Promise(r => setTimeout(r, reduceMotion ? 0 : 700));
    },
    refit() { refit(true); },
    // Pick a district and zoom to it, as a district's page opens or comes back into view.
    show(geo) { if (data) follow(geo, true); else waiting = geo; },
    // Back to Find: the whole chain, with nothing picked.
    overview() {
      if (!data) return;
      waiting = null;
      area = null;
      zipStatus.textContent = '';
      pick(null);
      showArea();
      zoom('all');
    },
  };
  mapSection.addEventListener('focusout', e => { if (waiting && data && !mapSection.contains(e.relatedTarget)) follow(waiting, true); });
  // Keep a keyboard-focused island tab inside the scrolling row on phones. Taps scroll the row through markTab instead.
  tabs.addEventListener('focusin', e => {
    if (tabs.scrollWidth > tabs.clientWidth && e.target.matches(':focus-visible')) e.target.scrollIntoView({inline: 'nearest', block: 'nearest'});
  });
  svg.addEventListener('focusin', e => {
    if (e.target.matches('.district:focus-visible') && !e.relatedTarget?.matches('#map .district')) showHint('Use the arrow keys to move between districts', 3000);
  });
  svg.addEventListener('focusout', e => { if (!svg.contains(e.relatedTarget)) updateStop(true); });

  // On the rankings page the table shows gray rows from the start, until the map and its figures are in.
  if (shadeSelect && shadeSelect.value) showLoading(true);
  loadShapes().then(json => {
    data = layout(json);
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
    resolveReady();
  }).catch(() => {
    const note = document.getElementById('map-loading');
    note.textContent = shadeSelect ? 'The map could not load. The district ranking still lists every district.' : 'The map could not load. Use the search or the numbers.';
    note.classList.add('failed');
    if (table) table.hidden = true;
  });
})();
