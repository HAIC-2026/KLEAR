// The rankings page's two tabs: Rankings and map colors every district by one measure (map.js), and Outreach groups
// districts by who meets a program's income test (outreach.js). The tab lives in the address (?view=outreach), so a
// link, Back and refresh keep it.
(() => {
  const VIEWS = ['rankings', 'outreach'], tabs = VIEWS.map(v => $(`tab-${v}`));
  if (tabs.some(t => !t)) return;

  // Put the page's choice in the address without adding a history entry for each one.
  function setView(view) {
    const url = new URL(location.href);
    if (view === 'rankings') url.searchParams.delete('view'); else url.searchParams.set('view', view);
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
    setView(view);
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
  showView(VIEWS.includes(asked) ? asked : 'rankings');
})();
