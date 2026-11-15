// index.html is three pages: Find (/), Ask (/ask) and a district's own page. This runs before the page first paints,
// so each page shows only its own parts from the start; app.js keeps it in step as the reader moves between them.
document.documentElement.dataset.page = /^\/district\//.test(location.pathname) ? 'district' : location.pathname === '/ask' ? 'ask' : 'home';
