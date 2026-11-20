// The rankings page's two tabs: Rankings and map colors every district by one measure (map.js), and Outreach groups
// districts by who meets a program's income test (outreach.js). The tab lives in the address (?view=outreach), so a
// link, Back and refresh keep it.
(() => {
  const VIEWS = ['rankings', 'outreach'], tabs = VIEWS.map(v => $(`tab-${v}`));
  if (tabs.some(t => !t)) return;

  // Put the page's choice in the address without adding a history entry for each one.
  function saveFilters(view) {
    const url = new URL(location.href);
    for (const name of ['view', 'pair', 'measure', 'chamber', 'order']) url.searchParams.delete(name);
    if (view === 'outreach') {
      url.searchParams.set('view', view);
      const filters = new FormData($('out-choose'));
      if (filters.get('pair') !== 'snap' || filters.get('chamber') !== 'house') {
        for (const name of ['pair', 'chamber']) url.searchParams.set(name, filters.get(name));
      }
    } else {
      const measure = $('map-shade'), order = $('map-order');
      const chamber = $('panel-rankings').querySelector('[data-chamber][aria-pressed=true]').dataset.chamber;
      if (measure.selectedIndex !== 0) url.searchParams.set('measure', measure.value);
      if (chamber !== 'house') url.searchParams.set('chamber', chamber);
      if (order.selectedIndex !== 0) url.searchParams.set('order', order.value);
    }
    try { history.replaceState(null, '', url.pathname + url.search); } catch { /* history unavailable */ }
  }

  // The tabs: an ARIA tab list, with the arrow keys, Home and End moving between them.
  function showView(view, focus, initial = false) {
    VIEWS.forEach((v, i) => {
      const on = v === view;
      tabs[i].setAttribute('aria-selected', String(on));
      tabs[i].tabIndex = on ? 0 : -1;
      $(`panel-${v}`).hidden = !on;
    });
    if (focus) tabs[VIEWS.indexOf(view)].focus();
    if (!initial) saveFilters(view);
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
  const asked = new URLSearchParams(location.search).get('view');
  showView(VIEWS.includes(asked) ? asked : 'rankings', false, true);
  $('panel-rankings').addEventListener('change', () => saveFilters('rankings'));
  $('out-choose').addEventListener('change', () => saveFilters('outreach'));
  // The map first restores a shared link, then emits the same event for user changes.
  document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('klear:chamber', () => saveFilters('rankings'));
  });
})();
