// index.html is three views: Find (/), a district's own page (/district/house-22) and Compare (/compare). This runs
// before the page first paints, so each view shows only its own parts from the start; app.js keeps it in step as the
// reader moves between them. /ask is the Find page with the agent's panel open.
document.documentElement.dataset.page = /^\/district\//.test(location.pathname) ? 'district' : location.pathname === '/compare' ? 'compare' : 'home';
