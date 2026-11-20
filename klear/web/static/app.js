let district = null, asked = null, busy = false, queued = null, districtLabel = '';
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const uncertaintyLabels = {ok: '90% margin of error shown above', controlled: 'Fixed to official totals; no sampling margin of error', range: 'Estimated range shown above', open_ended: 'Open-ended published value', not_computable: 'Margin of error unavailable; treat as uncertain', not_applicable: 'No margin of error applies', unavailable: 'Uncertainty information unavailable'};
// Suggested questions beside a district's profile, written out in full.
const examples = ['Give me a socioeconomic profile of this district.', 'How many kūpuna live in this district?', 'What does the data say about housing needs in this district?', 'How does poverty here compare with the state?', 'What support programs could residents of this district explore?'];
// A suggestion that asks about every district; beside a Senate district it asks about the Senate's.
const statewideExample = 'Which House districts have the highest share of kūpuna?';
const statewideFor = chamber => (chamber === 'senate' ? statewideExample.replace('House', 'Senate') : statewideExample);
// Starter questions on the Find page and in the agent's panel there, by topic. Each names its place or asks about the
// whole state, so none needs a district chosen first. All but Kāneʻohe lie in one House district; Kāneʻohe spans
// three, so its answer lists them to choose.
const homeStarters = [
  ['People', ['How many kūpuna live in Kāneʻohe?', statewideExample]],
  ['Income', ['How does poverty in Pāhoa compare with the state?']],
  ['Housing', ['What does the data say about housing costs in Kīhei?']],
  ['Programs', ['What support programs could families in Waiʻanae explore?']],
];
const refusalLabels = {decline_partisan: 'Partisan request declined', decline_individual: 'Personal request declined', not_available: 'Data not available', out_of_scope: 'Outside KLEAR’s scope'};

// The view the address shows: the Find page, a district's page, or Compare.
const view = () => document.documentElement.dataset.page;

// A wide district profile has a permanent chat. Everywhere else the reader can open and minimize it.
const FLOAT = window.matchMedia('(max-width: 1199px)'), PHONE = window.matchMedia('(max-width: 640px)');
const coarsePointer = window.matchMedia('(pointer: coarse)');
const chatFloats = () => FLOAT.matches || view() !== 'district';
let chatOpen = false;
// While the panel covers a phone's screen, the page behind it is inert, so the keyboard and screen readers stay in it.
function behindChat() {
  const stage = [...$('find-section').children].filter(n => n !== $('chat'));
  return [document.querySelector('.skip'), document.querySelector('.site-header'), $('hero'), ...stage, $('ask-teaser'), $('sources'), $('chat-fab'), document.querySelector('body > footer')].filter(Boolean);
}
function applyChat() {
  const floats = chatFloats(), open = floats && chatOpen, cover = open && PHONE.matches, root = document.documentElement;
  root.classList.toggle('chat-floats', floats);
  root.classList.toggle('chat-open', open);
  root.classList.toggle('chat-cover', cover);
  $('chat-fab').setAttribute('aria-expanded', String(open));
  for (const n of behindChat()) n.inert = cover;
  if (!floats || open) $('fab-new').hidden = true;
}
// Open the panel. Focus goes to the question box, or to the panel's title on a touch screen, where the box would
// bring up the keyboard before the reader has read the panel.
const SAVED_OPEN = 'klear-chat-open';
function rememberChat() { try { sessionStorage.setItem(SAVED_OPEN, chatOpen ? '1' : ''); } catch { /* blocked */ } }
function openChat(focus = true) {
  chatOpen = true;
  rememberChat();
  applyChat();
  fitQuestion();
  if (!FLOAT.matches && view() === 'compare') $('chat').scrollIntoView({block: 'nearest'});
  if (focus) (coarsePointer.matches ? $('chat-panel-title') : $('question')).focus({preventScroll: true});
}
function closeChat(focus = true) {
  chatOpen = false;
  rememberChat();
  applyChat();
  if (focus && chatFloats()) $('chat-fab').focus({preventScroll: true});
}
$('chat-fab').onclick = () => (chatOpen ? closeChat() : openChat());
$('chat-min').onclick = () => closeChat();
$('chat').addEventListener('keydown', e => {
  if (e.key !== 'Escape' || !chatFloats() || !chatOpen || e.target.closest('[aria-expanded=true][role=combobox]')) return;
  e.preventDefault();
  closeChat();
});
FLOAT.addEventListener('change', applyChat);
PHONE.addEventListener('change', applyChat);

// How this server answers, from /api/ready: with the AI assistant, or with the basic lookup alone (no key, or today's
// spending cap reached), and whether street address search is on. The notes under the question boxes say where a
// question goes in each case, and until the server says, only what is true either way. When the assistant fails or
// reaches its cap mid-conversation, the answer says so (fallback) and the notes change with it.
const capability = {known: false, assistant: false, capped: false, address: true, fellBack: ''};
const PRIVACY = {
  assistant: 'An AI model answers from KLEAR’s data. Questions go to its provider, Anthropic, so leave out personal details.',
  basic: 'Basic lookup: KLEAR matches questions to its own data, with no AI model. Questions are not sent to an AI provider. Leave out personal details.',
  capped: 'The AI assistant has reached today’s spending limit, so the basic lookup answers, with no AI model, until the day ends (HST). Questions are not sent to an AI provider. Leave out personal details.',
  failed: 'The AI assistant could not answer the last question, so the basic lookup did. The next question tries the assistant again, which sends it to Anthropic. Leave out personal details.',
  unknown: 'Answers come from KLEAR’s data. Leave out personal details.',
};
const MODE_LINES = {
  basic: 'Basic lookup, with no AI model: ask about a district, town, ZIP code or county, two places side by side, or which districts rank highest on a measure.',
  capped: 'Basic lookup until the day ends (HST): the AI assistant has reached today’s spending limit.',
};
function capabilityState() {
  if (!capability.known) return 'unknown';
  if (!capability.assistant) return 'basic';
  if (capability.capped || capability.fellBack === 'capped') return 'capped';
  return capability.fellBack === 'failed' ? 'failed' : 'assistant';
}
function showCapability() {
  const state = capabilityState(), line = MODE_LINES[state] || '';
  $('chat-privacy-text').textContent = PRIVACY[state];
  $('home-privacy').textContent = PRIVACY[state];
  $('chat-mode').textContent = line;
  $('chat-mode').hidden = !line;
  document.documentElement.dataset.answers = state;
  if (capability.known && !capability.address) {
    for (const note of document.querySelectorAll('.find .privacy')) note.textContent = 'Street address search is off on this site. Search by town, ZIP code, district or legislator.';
  }
}
async function loadCapability() {
  try {
    const c = ((await (await fetch('/api/ready')).json()) || {}).capabilities;
    if (c && typeof c === 'object') Object.assign(capability, {known: true, assistant: Boolean(c.assistant), capped: Boolean(c.assistant_capped), address: c.address_lookup !== false});
  } catch { /* the notes stay true either way */ }
  showCapability();
}
loadCapability();

// The panel's title names what the agent asks about: the district, the two districts compared, or nothing yet.
// #district-title holds the district questions go about.
let compareNames = [];
function chatTitle() {
  const names = view() === 'compare' ? compareNames : district ? [districtLabel] : [];
  $('chat-panel-title').textContent = names.length ? `Ask about ${names.join(' and ')}` : 'Ask KLEAR';
  $('welcome-text').textContent = names.length === 2 ? COMPARE_WELCOME : names.length ? DISTRICT_WELCOME : NO_DISTRICT_WELCOME;
  $('district-title').textContent = district ? districtLabel : 'a district';
}

// Bring the chat into view: open its panel where it floats, else move its card or question box onto the screen.
function showChat() {
  if (chatFloats()) { if (!chatOpen) openChat(false); return; }
  const card = $('chat-card'), box = card.getBoundingClientRect();
  if (box.top >= 0 && box.bottom <= window.innerHeight) return;
  (card.offsetHeight <= window.innerHeight ? card : $('question-form')).scrollIntoView({block: 'center'});
}

// Grow the question box with its text, up to a limit, then scroll inside it.
const QUESTION_MAX = 192;
function fitQuestion() {
  const q = $('question');
  q.style.height = 'auto';
  const full = q.scrollHeight + q.offsetHeight - q.clientHeight;
  q.style.height = `${Math.min(full, QUESTION_MAX)}px`;
  q.style.overflowY = full > QUESTION_MAX ? 'auto' : 'hidden';
}
$('question').addEventListener('input', fitQuestion);
window.addEventListener('resize', fitQuestion);
document.fonts.ready.then(fitQuestion);

function logScrolls() { return getComputedStyle($('messages')).overflowY === 'auto'; }
// Whether a node shows on screen, and inside the log's own scroll area when the log scrolls.
function onScreen(n) {
  const r = n.getBoundingClientRect(), log = $('messages');
  const box = logScrolls() && log.contains(n) ? log.getBoundingClientRect() : {top: 0, bottom: window.innerHeight};
  return r.bottom > Math.max(0, box.top) && r.top < Math.min(window.innerHeight, box.bottom) && r.height > 0;
}

// Keep the field the reader is typing in where it is while the page grows above it.
function holdField() {
  const f = document.activeElement;
  if (!f || !f.matches('input, select, textarea') || !onScreen(f)) return null;
  const before = f.getBoundingClientRect().top;
  const undo = () => { const shift = f.getBoundingClientRect().top - before; if (shift) window.scrollBy({top: shift, behavior: 'instant'}); };
  undo.field = true;
  return undo;
}

// When the log grows the page, return a function that undoes the shift: for what sits below the chat when
// the change is above the screen, else for a field the reader is typing in.
function holdPage(mark) {
  if (logScrolls()) return null;
  if (mark.getBoundingClientRect().bottom > 0) return holdField();
  const section = $('chat'), before = section.getBoundingClientRect().bottom;
  return () => { const shift = section.getBoundingClientRect().bottom - before; if (shift) window.scrollBy({top: shift, behavior: 'instant'}); };
}

// Scroll the conversation so a message sits at the top, inside the log when it scrolls on its own.
function reveal(message) {
  if (logScrolls()) $('messages').scrollTo({top: message.offsetTop - 16, behavior: reduceMotion.matches ? 'auto' : 'smooth'});
  else message.scrollIntoView({block: 'start'});
}

// Each district has its own address, like /district/house-22, and so does a comparison, like
// /compare?d=house:022,house:023, so a link, Back, Forward and refresh all keep them.
const BASE_TITLE = document.title, COMPARE_TITLE = 'Compare – KLEAR';
const SEATS = {house: 51, senate: 25};
// Change the address bar, adding a history entry unless replacing. A page the browser will not let change its
// address, such as one opened from a file, keeps working without it.
function setAddress(path, replace) {
  try {
    if (replace) history.replaceState(null, '', path); else history.pushState(null, '', path);
  } catch { /* history unavailable */ }
}
// The district the address names, or null for any other page or a district that does not exist.
function routeGeo() {
  const m = location.pathname.match(/^\/district\/(house|senate)-(\d{1,2})$/);
  return m && +m[2] >= 1 && +m[2] <= SEATS[m[1]] ? `${m[1]}:${m[2].padStart(3, '0')}` : null;
}

// Show one of the three views. The header marks its tab as the current page: Districts on Find and a district's
// page, Compare on Compare.
function setView(name) {
  document.documentElement.dataset.page = name;
  const tab = name === 'compare' ? 'compare' : 'districts';
  for (const a of document.querySelectorAll('.site-header a[data-page]')) {
    if (a.dataset.page === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  if (name === 'home') document.title = BASE_TITLE;
  else if (name === 'compare') document.title = COMPARE_TITLE;
  else if (districtLabel) document.title = `${districtLabel} – KLEAR`;
  $('district-head').hidden = name !== 'district';
  $('compare-view').hidden = name !== 'compare';
  $('chat').setAttribute('aria-labelledby', 'chat-panel-title');
  // The panel stays open across views where it floats, and a docked chat has nothing to open.
  if (!chatFloats()) chatOpen = false;
  applyChat();
  chatTitle();
  shareBar();
  if (name !== 'compare') { compareNames = []; pairExamples([]); }
  refreshSheet();
}

// A change of view that moves things: the browser's view transition, when it has one and the device allows motion,
// slides the map from its place on the Find page to its corner of the district's page, and the rest fades in.
let moving = null;
function transition(update) {
  if (reduceMotion.matches || typeof document.startViewTransition !== 'function') { update(); return Promise.resolve(); }
  // The map's card is named for the transition only while it runs (style.css), so nothing else meets the name.
  const root = document.documentElement;
  root.classList.add('moving');
  const t = moving = document.startViewTransition(() => { update(); if (window.klearMap) window.klearMap.refit(); });
  return t.finished.catch(() => {}).finally(() => { root.classList.remove('moving'); if (moving === t) moving = null; });
}
// Printing skips a transition still under way: some browsers cannot lay a page out for print in the middle of one.
window.addEventListener('beforeprint', () => { if (moving) moving.skipTransition(); });

// After the first answer the suggested questions fold to the one row that fits, with More questions at its end for
// the rest. A new answer folds them again.
let suggestOpen = false, suggestWidth = 0;
function foldSuggestions() {
  const box = $('suggest'), more = $('more-questions'), buttons = [...$('examples').children].filter(b => !b.classList.contains('off'));
  // In the panel they fold from the start too, so the question box stays near the top.
  const fold = (Boolean($('log').querySelector('article')) || chatFloats()) && !suggestOpen;
  box.classList.toggle('folded', fold);
  for (const b of buttons) b.hidden = false;
  more.hidden = !fold && !suggestOpen;
  more.setAttribute('aria-expanded', String(suggestOpen));
  if (!fold || !box.offsetWidth) return;
  // Hide the last question shown until the rest and More questions share the first row.
  const shown = buttons.slice();
  while (shown.length > 1 && shown.at(-1).offsetTop > shown[0].offsetTop) shown.pop().hidden = true;
  if (shown.length === buttons.length) more.hidden = true;
  // Focus on a question that just folded away moves to More questions.
  if (buttons.some(b => b.hidden && b === document.activeElement)) more.focus();
}
$('more-questions').onclick = () => {
  suggestOpen = !suggestOpen;
  foldSuggestions();
};
// The row holds a different number of questions as the chat changes width, or when its panel first opens.
new ResizeObserver(() => {
  const width = $('suggest').offsetWidth;
  if (width !== suggestWidth) { suggestWidth = width; foldSuggestions(); }
}).observe($('suggest'));

// On wide screens the profile comes first, so a link at the top skips past it to the question box.
$('skip-ask').onclick = e => { e.preventDefault(); showChat(); $('question').focus(); };

// The note under the question box says where questions go; Privacy opens the rest.
$('privacy-toggle').onclick = () => {
  const open = $('privacy-more').hidden;
  $('privacy-more').hidden = !open;
  $('privacy-toggle').setAttribute('aria-expanded', String(open));
};

// After a reader picks a district, the top of its page with focus on its name, or the chat when they picked it to ask
// about it.
function arrive(land) {
  if (land === 'ask') { showChat(); $('question').focus({preventScroll: !chatFloats()}); return; }
  window.scrollTo({top: 0, behavior: 'instant'});
  $('district-name').focus({preventScroll: true});
}

const NO_DISTRICT_WELCOME = 'Ask about a district, a town or ZIP code, or all of Hawaiʻi.';
const DISTRICT_WELCOME = 'Pick a question or write your own. Answers use only KLEAR’s data, and each figure names its source.';
const COMPARE_WELCOME = 'Ask how the two districts compare, or about either one. Answers use only KLEAR’s data, and each figure names its source.';

// While an answer loads, the ask and suggestion buttons say they are unavailable but keep focus: disabling a focused
// button would drop focus to the page, and keyboard and screen reader users would lose their place.
function setBusy(on) {
  busy = on;
  for (const b of [$('send'), ...document.querySelectorAll('#examples button, #starters button, #home-starters button, #log .followup, #log .retry')]) {
    if (on) b.setAttribute('aria-disabled', 'true'); else b.removeAttribute('aria-disabled');
  }
}

function switchLine(name) {
  const line = node('p', '', 'switch');
  line.append(node('span', `Now asking about ${name}`));
  return line;
}

// The district the chat asks about. A change adds a line to the conversation once something has been asked, and
// drops the questions that named the district before.
function setChatDistrict(geo, name, quiet) {
  const switched = geo !== district;
  if (!switched) return false;
  const log = $('log'), last = log.lastElementChild;
  if (last && last.classList.contains('switch')) last.remove();
  if (asked && geo && geo !== asked) {
    log.append(switchLine(name));
    if (!quiet) $('messages').scrollTop = $('messages').scrollHeight;
  }
  district = geo;
  districtLabel = geo ? name : '';
  if (geo) $('chat').dataset.chamber = document.body.dataset.chamber = geo.split(':')[0];
  else { delete $('chat').dataset.chamber; delete document.body.dataset.chamber; }
  chatTitle();
  document.dispatchEvent(new CustomEvent('klear:district', {detail: {geo, name}}));
  return true;
}

// Set the district the chat asks about and show its page, or queue the switch while an answer loads. A switch adds the
// district's address to the browser history, unless it came from there (push false): Back, Forward or an opened link.
// Unless quiet, the page then moves to the district (see arrive), landing on its profile or, by default, in the chat.
function choose(geo, name, quiet, push = true, land = 'ask') {
  const wasDistrict = view() === 'district';
  setView('district');
  if (busy) {
    queued = geo === district ? null : {geo, name, push};
    $('chat-status').textContent = queued ? `Switching to ${name} after this answer.` : '';
    // The profile needs no model, so it shows the chosen district while the answer loads.
    loadProfile(geo, name);
    if (!quiet) arrive(land);
    return;
  }
  const switched = setChatDistrict(geo, name, quiet);
  // The map zooms to the district even when the chat already asks about it, as when coming back from Compare.
  if (!switched && window.klearMap) window.klearMap.show(geo);
  document.title = `${name} – KLEAR`;
  if (push && (switched || !wasDistrict || location.pathname !== districtPath(geo))) setAddress(districtPath(geo));
  loadProfile(geo, name);
  shareBar();
  const ready = $('question').value.trim() ? 'Your question is ready. Press Ask KLEAR.' : '';
  // A conversation that moves to another district says so; a district page opened fresh needs no line, its title names it.
  $('chat-status').textContent = quiet && switched && asked ? `Now asking about ${name}. ${ready}`.trim() : ready;
  if (!quiet) arrive(land);
}

// Open a district's page from the Find page: the map zooms to the district, then the page turns into the district's,
// the map moving to the top left beside its name. From anywhere else, or without motion, it opens at once.
let opening = false;
async function openDistrict(geo, name = districtName(geo)) {
  if (opening) return;
  opening = true;
  try {
    const frame = $('district-map').querySelector('.map-frame').getBoundingClientRect();
    const seen = frame.bottom > 0 && frame.top < window.innerHeight && frame.height > 0;
    if (view() === 'home' && seen && window.klearMap && !reduceMotion.matches) await window.klearMap.zoomTo(geo);
    await transition(() => choose(geo, name, false, true, 'profile'));
  } finally {
    opening = false;
  }
}

// Back to the Find page with no district: the district's suggestions leave the chat, and the conversation stays.
function clearDistrict() {
  queued = null;
  setChatDistrict(null, '', true);
  profileSeq++;
  profileGeo = null;
  setView('home');
  districtHead('', null);
  $('chat-status').textContent = '';
  $('profile').hidden = true;
  profileJumpResize.disconnect();
  $('profile').replaceChildren();
  $('profile-live').textContent = '';
  dropDistrictActions();
  $('suggest').hidden = true;
  $('starter').hidden = false;
  if (window.klearMap) window.klearMap.overview();
}

// A click with no modifier key, which the page handles in place; with one, a link opens as the browser does.
const plainClick = e => !(e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey);
// Focus in the search box, or on its section's title on a touch screen, where the box would bring up the keyboard.
const focusFind = () => (coarsePointer.matches ? $('find-title') : $('find-place')).focus({preventScroll: true});

// "Search another district": the Find page at its search and map, the map zooming back out from the district left.
async function goFind(push = true) {
  await transition(() => {
    clearDistrict();
    if (push) setAddress('/');
    $('find-section').scrollIntoView({block: 'start', behavior: 'instant'});
  });
  focusFind();
}
for (const id of ['search-another', 'compare-home']) $(id).addEventListener('click', e => { if (plainClick(e)) { e.preventDefault(); goFind(); } });
// On the Find page, the / key jumps to the search from anywhere but a field being typed in, as on many search sites.
document.addEventListener('keydown', e => {
  if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || view() !== 'home' || document.documentElement.classList.contains('chat-cover')) return;
  if (e.target.closest('input, textarea, select, [contenteditable], dialog')) return;
  e.preventDefault();
  $('find-section').scrollIntoView({block: 'start'});
  $('find-place').focus({preventScroll: true});
});

// Compare: this district in the left third, and a search for another in the middle.
function openCompare(geos) {
  const list = geos.filter(Boolean);
  setAddress(list.length ? `/compare?d=${list.join(',')}` : '/compare');
  return transition(() => {
    setView('compare');
    if (window.klearCompare) window.klearCompare.show(list);
    window.scrollTo({top: 0, behavior: 'instant'});
  }).then(() => { if (window.klearCompare) window.klearCompare.focusNext(); });
}
$('compare-another').addEventListener('click', e => { if (plainClick(e)) { e.preventDefault(); openCompare([district]); } });
// The header's Compare link opens Compare in place when this page holds it.
document.querySelector('.site-header a[data-page=compare]').addEventListener('click', e => {
  if (!plainClick(e)) return;
  e.preventDefault();
  if (view() !== 'compare') openCompare([]);
});

// The compare view tells the chat which districts it holds: questions go about the first, and the panel's title and
// suggestions name both.
document.addEventListener('klear:compare', e => {
  if (view() !== 'compare') return;
  const list = e.detail.districts || [];
  compareNames = list.map(d => d.name);
  const first = list[0];
  if (busy) {
    queued = first && first.geo !== district ? {geo: first.geo, name: first.name, push: false, compare: true} : null;
  } else if (first) setChatDistrict(first.geo, first.name, true);
  else setChatDistrict(null, '', true);
  $('suggest').hidden = !list.length;
  $('starter').hidden = Boolean(list.length);
  pairExamples(list);
  chatTitle();
  shareBar();
  refreshSheet();
});

window.addEventListener('popstate', () => route(false));
// Show what the address names: a district, a comparison, or the Find page.
function route(fresh) {
  const geo = routeGeo();
  if (geo) {
    if (view() === 'district' && geo === (queued ? queued.geo : district)) return;
    choose(geo, districtName(geo), true, false);
  } else if (location.pathname === '/compare') {
    setView('compare');
    if (window.klearCompare) window.klearCompare.fromAddress();
  } else if (view() !== 'home' || fresh) {
    clearDistrict();
  }
}

// The search on the Find page opens the district's page at its profile, the map zooming to it first.
// Its matches also light up on the map beside it: the largest place found, with each district's share of its residents.
const resetFind = lookupForm($('find'), $('find-place'), $('find-status'), $('find-matches'), (geo, name) => { resetFind(); openDistrict(geo, name); }, groups => {
  const g = groups[0], pairs = c => g.rows.filter(r => r.geo.startsWith(`${c}:`)).map(r => [r.geo, r.share ?? 1]);
  document.dispatchEvent(new CustomEvent('klear:area', {detail: g ? {label: g.place, house: pairs('house'), senate: pairs('senate')} : null}));
});

// The House or Senate switch over the Find page's map and numbers. The map follows it (map.js), the numbers show the
// chosen chamber, and the choice holds while the tab is open.
const SAVED_CHAMBER = 'klear-chamber';
function showSeats(name) {
  for (const box of document.querySelectorAll('[data-seats]')) box.dataset.seats = name;
  for (const b of document.querySelectorAll('.chamber-switch [data-chamber]')) b.setAttribute('aria-pressed', String(b.dataset.chamber === name));
  try { sessionStorage.setItem(SAVED_CHAMBER, name); } catch { /* blocked */ }
}
document.addEventListener('klear:chamber', e => showSeats(e.detail));
// A switch pressed on the Find page says which numbers now show, wherever the reader's eyes are.
document.addEventListener('click', e => {
  const b = e.target.closest('.chamber-switch [data-chamber]');
  if (b && view() === 'home') $('seats-live').textContent = `Showing the ${b.dataset.chamber === 'senate' ? '25 Senate' : '51 House'} districts.`;
});

// Every district by number. Each number is a link to its page, so it works before the script runs and opens in a new
// tab like any link; a plain click opens it in place, the map zooming to it first. Pointing at a number outlines its
// district on the map. Tab reaches each chamber's list once, and the arrow keys move between its numbers, as on the map.
function seatLinks(root, open) {
  for (const group of root.querySelectorAll('.seat-chamber')) {
    const links = [...group.querySelectorAll('a')];
    links.forEach((a, i) => {
      a.tabIndex = i ? -1 : 0;
      const m = a.pathname.match(/^\/district\/(house|senate)-(\d+)$/), geo = m && `${m[1]}:${m[2].padStart(3, '0')}`;
      a.addEventListener('click', e => {
        if (!geo || !plainClick(e)) return;
        e.preventDefault();
        open(geo, districtName(geo));
      });
      const hover = on => document.dispatchEvent(new CustomEvent('klear:hover', {detail: on ? geo : null}));
      a.addEventListener('pointerenter', e => { if (e.pointerType !== 'touch') hover(true); });
      a.addEventListener('pointerleave', () => hover(false));
      a.addEventListener('focus', () => hover(true));
      a.addEventListener('blur', () => hover(false));
      a.addEventListener('keydown', e => {
        const to = {ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: links.length - 1}[e.key];
        if (to === undefined || e.altKey || e.ctrlKey || e.metaKey) return;
        e.preventDefault();
        const next = links[Math.max(0, Math.min(links.length - 1, to))];
        for (const l of links) l.tabIndex = l === next ? 0 : -1;
        next.focus();
      });
    });
  }
}
for (const a of document.querySelectorAll('#seat-index a')) a.setAttribute('aria-describedby', 'seats-keys');
seatLinks($('seat-index'), (geo, name) => openDistrict(geo, name));
try { const saved = sessionStorage.getItem(SAVED_CHAMBER); if (saved === 'senate') showSeats('senate'); } catch { /* blocked */ }

// Questions to try, by topic, written out in full. Each is asked at once, in the agent's panel.
function starterTopics(box, ask) {
  for (const [title, questions] of homeStarters) {
    const group = node('div', '', 'topic'), head = node('h3', title, 'topic-title'), list = node('ul', '');
    head.id = `${box.id}-${title.toLowerCase()}`;
    group.setAttribute('role', 'group');
    group.setAttribute('aria-labelledby', head.id);
    for (const question of questions) {
      const li = document.createElement('li'), b = says('button', question, 'starter');
      b.type = 'button';
      b.onclick = () => ask(question);
      li.append(b);
      list.append(li);
    }
    group.append(head, list);
    box.append(group);
  }
}
starterTopics($('starters'), question => askAgain(question));
starterTopics($('home-starters'), question => openAsk(question));
// The Find page's question box asks in the agent's panel, which opens over the page.
function openAsk(question) {
  if (busy) return;
  openChat(false);
  if (!question) { $('question').focus(); return; }
  // Focus moves into the panel, so the answer takes it when it comes.
  $('chat-panel-title').focus({preventScroll: true});
  askAgain(question);
}
$('home-ask').onsubmit = e => {
  e.preventDefault();
  const question = $('home-question').value.trim();
  if (!question) { $('home-question').focus(); return; }
  if (busy) return;
  $('home-question').value = '';
  openAsk(question);
};
$('home-question').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); $('home-ask').requestSubmit(); }
});
// A district's suggested questions, written out in full, so what they say is what they ask.
for (const question of [...examples, statewideExample]) {
  const b = says('button', question);
  b.type = 'button';
  b.dataset.question = question;
  if (question === statewideExample) b.classList.add('statewide');
  b.onclick = () => askAgain(b.dataset.question);
  $('examples').append(b);
}

// Questions that compare the two districts, in place of a district's own while Compare holds two.
function pairExamples(list) {
  for (const b of $('examples').querySelectorAll('.pair')) b.remove();
  const both = list.length === 2;
  for (const b of $('examples').children) b.classList.toggle('off', both);
  if (both) {
    const [a, b] = list.map(d => d.name);
    const questions = [`How does poverty in ${a} compare with ${b}?`, `Compare the share of kūpuna in ${a} and ${b}.`, `How do housing costs in ${a} compare with ${b}?`, `What support programs could residents of ${a} explore?`];
    $('examples').prepend(...questions.map(question => {
      const q = says('button', question, 'pair');
      q.type = 'button';
      if (busy) q.setAttribute('aria-disabled', 'true');
      q.onclick = () => askAgain(question);
      return q;
    }));
  }
  foldSuggestions();
}

// Once a district is chosen, its profile fills the page under its name and its suggested questions show in the chat.
// The profile is long, so it is not a live region: a short status line says when it has loaded.
let profileSeq = 0, profileGeo = null, profileData = null;
async function loadProfile(geo, name) {
  if (geo === profileGeo) return;
  profileGeo = geo;
  profileData = null;
  const seq = ++profileSeq, panel = $('profile'), live = $('profile-live');
  live.textContent = '';
  $('suggest').hidden = false;
  $('starter').hidden = true;
  panel.hidden = false;
  districtHead(name, null);
  profileJumpResize.disconnect();
  panel.replaceChildren(profileLoading());
  try {
    const data = await profileOf(geo);
    if (seq !== profileSeq) return;
    profileData = data;
    districtHead(data.district, data);
    panel.replaceChildren(...renderProfile(data));
    fitTables();
    profileJumpResize.observe(panel);
    profileJumpResize.observe(panel.querySelector('.profile-jumps'));
    updateProfileJumps();
    districtExamples(data);
    shareBar();
    refreshSheet();
    live.textContent = `Profile for ${data.district} loaded.`;
    // An address that names a section, such as one opened from a bookmark, opens it.
    const target = location.hash.length > 1 && document.getElementById(location.hash.slice(1));
    if (target && target.matches('#profile > :is(.profile-glance,.profile-section)')) {
      if (target.matches('details')) target.open = true;
      target.scrollIntoView();
    }
  } catch (err) {
    if (seq !== profileSeq) return;
    profileGeo = null;
    const message = `The district profile could not load. ${err.message}`;
    const retry = node('button', 'Retry district profile', 'button secondary');
    retry.type = 'button';
    retry.onclick = () => {
      panel.tabIndex = -1;
      panel.focus();
      loadProfile(geo, name);
    };
    panel.replaceChildren(node('p', message, 'profile-status'), retry);
    live.textContent = message;
  }
}

// While a profile loads, gray bars where it will be: At a glance with its four figures, then the first sections.
function profileLoading() {
  const box = node('div', '', 'profile-loading');
  box.append(node('p', 'Loading the district profile…', 'profile-status visually-hidden'));
  const glance = node('div', '', 'profile-glance'), stats = node('div', '', 'skeleton-stats');
  glance.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 4; i++) stats.append(skeleton(['w-60', 'big w-45', 'w-45', 'w-80']));
  glance.append(skeleton(['title w-30']), stats);
  box.append(glance);
  for (const width of ['w-20', 'w-30', 'w-30']) box.append(skeleton([`title ${width}`], 'profile-section'));
  return box;
}

// The district's name, which heads its page beside its map: the chamber over the district's number, which is set
// large in the chamber's color. Under it, where the district is (its county and island, its largest places, and the
// districts of the other chamber it overlaps) and its legislator.
function districtHead(name, p) {
  const h = $('district-name'), m = /^(House|Senate) District (\d+)$/.exec(name);
  h.replaceChildren();
  if (m) h.append(node('span', `${m[1]} District`, 'mast-chamber'), ' ', node('span', m[2], 'mast-number'));
  else h.textContent = name;
  const about = $('district-about'), member = $('district-member');
  about.replaceChildren();
  member.replaceChildren();
  // While the profile loads, gray lines where the place and legislator go.
  if (!p) { if (name) about.append(skeleton(['w-60', 'w-80', 'w-45'])); return; }
  if (p.where) about.append(node('p', p.where, 'district-where'));
  const meta = [p.places.length ? `Includes ${placesList(p)}.` : '', p.overlaps.length ? `Overlaps ${overlapsList(p)}.` : ''];
  if (meta.some(Boolean)) about.append(node('p', meta.filter(Boolean).join(' '), 'district-meta'));
  if ((p.members || []).length) member.append(profileMembers(p.members));
}

// What a short label stands for, one tap away: a plain definition, then the official label and who the figure
// counts, and a link to the glossary. Nothing when there is no more to say than the label itself.
function meaningParts(label, m) {
  const official = m && m.official && m.official !== label ? m.official : '';
  if (!m || !(m.plain || m.universe || official)) return null;
  const parts = [];
  if (m.plain) parts.push(says('p', m.plain));
  const terms = node('dl', '');
  const term = (name, text) => { const t = node('div', ''); t.append(node('dt', name), says('dd', text)); terms.append(t); };
  if (official) term('Official label', official);
  if (m.universe) term('Who is counted', m.universe);
  if (terms.children.length) parts.push(terms);
  // A new tab, so a chat in progress stays where it is.
  const more = node('p', '', 'meaning-more'), a = node('a', 'Glossary of terms');
  a.href = '/data#glossary'; a.target = '_blank';
  a.append(hidden(' (opens in a new tab)'));
  more.append(a);
  parts.push(more);
  return parts;
}
function meaning(label, m) {
  const parts = meaningParts(label, m);
  if (!parts) return null;
  const box = node('details', '', 'meaning'), summary = node('summary', 'What this means');
  summary.append(hidden(`: ${label}`));
  box.append(summary, ...parts);
  return box;
}

const isNumber = v => typeof v === 'number' && Number.isFinite(v);

// A gap between rates is in percentage points. Other gaps are relative to the reference figure, unless it is zero.
function comparisonGap(mine, theirs) {
  const size = Math.abs(mine.num - theirs.num);
  if (!size) return '';
  if (mine.unit === 'percent') return `${size < 0.05 ? '<0.1' : size.toFixed(1)} points`;
  if (!theirs.num) return '';
  const percent = size / Math.abs(theirs.num) * 100;
  return percent < 1 ? '<1%' : `${Math.round(percent)}%`;
}
// What a place is called in a comparison line: the county or the state, or its name.
const placeWord = a => ({county: 'county', state: 'state'})[String(a.geo || '').split(':')[0]] || a.place;
const MARKS = {higher: '▲', lower: '▼', within_margin: '≈'};
const NO_CLEAR_DIFFERENCE = 'No clear difference';
const PROFILE_COMPARISON_KEY = '▲ higher or ▼ lower beyond the margins of error; ≈ no clear difference. Rate gaps are in percentage points. Percent gaps use the county or state figure as the baseline.';
const PAIR_COMPARISON_KEY = '▲ higher beyond the margins of error; ≈ no clear difference. Rate gaps are in percentage points. Percent gaps use the lower figure as the baseline.';

// The words carry the meaning for screen readers; the mark is a visual cue.
function comparisonLine(direction, words) {
  const line = node('span', '', `compare-line ${direction}`), mark = node('span', MARKS[direction], 'reading-mark');
  mark.setAttribute('aria-hidden', 'true');
  line.append(mark, ' ', words);
  return line;
}

function comparisonTo(mine, theirs, direction, place) {
  if (direction === 'within_margin') return comparisonLine(direction, `${NO_CLEAR_DIFFERENCE} (${place})`);
  const gap = comparisonGap(mine, theirs), side = direction === 'higher' ? 'Above' : 'Below';
  return comparisonLine(direction, gap ? `${gap} ${side.toLowerCase()} ${place}` : `${side} ${place}`);
}

// One line per reference place with a tested direction. Totals have no direction against the county or state.
function compareLines(r) {
  return (r.against || []).filter(a => !a.missing && MARKS[a.direction] && isNumber(r.num) && isNumber(a.num))
    .map(a => comparisonTo(r, a, a.direction, placeWord(a)));
}

// In print both districts share a Difference column, so name the higher district there.
function pairSheetLine(a, b, names) {
  const d = pairDifference(a, b);
  if (!d) return null;
  if (d === 'same') return comparisonLine('within_margin', NO_CLEAR_DIFFERENCE);
  const [higher, lower, name] = d === 'a' ? [a, b, names[0]] : [b, a, names[1]];
  const gap = comparisonGap(higher, lower);
  return comparisonLine('higher', gap ? `${gap} higher in ${name}` : `Higher in ${name}`);
}

// What the marks in a section's comparisons stand for, under its table.
function compareKey() {
  return node('p', PROFILE_COMPARISON_KEY, 'profile-key');
}

// One measure as a table row: its label, then the home place's figure and each other place's in its own column, then
// how the figure compares. A second row under it, as wide as the table, holds the notes on the figure (a caution, how
// it was estimated, a figure to read beside it) and what the label means, which stays shut until the button beside
// the label opens it. A measure with neither has no second row.
let meaningCount = 0;
function profileRow(r, home, cols, compares) {
  const row = node('tr', '', 'profile-row'), head = node('th', '', 'profile-measure');
  row.setAttribute('role', 'row');
  head.scope = 'row';
  head.setAttribute('role', 'rowheader');
  const label = says('span', r.label, 'profile-label');
  if (r.note) { const sup = node('sup', ''); sup.append(hidden(' source '), String(r.note)); label.append(sup); }
  head.append(label);
  row.append(head, figureCell(r, home, true), ...cols.map(c => figureCell(r.against.find(a => a.geo === c.geo), c.place, false)));
  if (compares) {
    const how = node('td', '', 'profile-how');
    how.setAttribute('role', 'cell');
    how.append(...compareLines(r));
    row.append(how);
  }
  const notes = [];
  if (r.caution) notes.push(cautionLine(r.caution));
  if (r.method) notes.push(methodLine(r.method));
  if (r.beside) notes.push(besideLine(r.beside));
  if (r.caveat) notes.push(node('p', r.caveat, 'profile-caveat'));
  const parts = meaningParts(r.label, r.meaning);
  if (!notes.length && !parts) return [row];
  const more = node('tr', '', 'profile-more'), cell = node('td', '');
  more.setAttribute('role', 'row');
  more.hidden = !notes.length;
  cell.colSpan = row.children.length;
  cell.setAttribute('role', 'cell');
  cell.append(...notes);
  more.append(cell);
  if (parts) {
    const about = node('div', '', 'meaning'), button = node('button', '', 'info');
    about.id = `meaning-${++meaningCount}`;
    about.hidden = true;
    about.append(...parts);
    cell.append(about);
    button.type = 'button';
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', about.id);
    button.append(icon('info'), hidden(`What this means: ${r.label}`));
    button.onclick = () => {
      about.hidden = !about.hidden;
      more.hidden = about.hidden && !notes.length;
      button.setAttribute('aria-expanded', String(!about.hidden));
    };
    head.append('\u00a0', button);  // no break here, so the button never starts a line of its own
  }
  return [row, more];
}

// A table too wide for its box, as on a phone or where a section holds long dollar amounts, stacks: each measure
// becomes a block with its places side by side under the label. The table is measured as columns each time, so it
// goes back to columns when it has room again.
function fitTable(box) {
  if (!box.clientWidth) return;  // out of sight, behind the Ask tab: nothing to measure yet
  const table = box.firstElementChild;
  box.classList.remove('stacked');
  box.classList.toggle('stacked', table.offsetWidth > box.clientWidth + 1);
}
// Every table at once, as soon as the profile is on the page or its tab opens, so no frame shows a table too wide.
function fitTables() { for (const box of $('profile').querySelectorAll('.profile-rows')) fitTable(box); }
// Checked again when the box or the table changes size: the window, the tab or the text spacing changed. The check
// waits for the next frame, so nothing changes size while the browser is still reporting sizes.
const refit = new Set();
const tableFit = new ResizeObserver(entries => {
  if (!refit.size) requestAnimationFrame(() => { for (const box of refit) fitTable(box); refit.clear(); });
  for (const e of entries) refit.add(e.target.closest('.profile-rows'));
});

// A section's figures as one table: a row per measure, a column for the home place and one for each place it is set
// against, and a last column for how each rate or amount compares. A section of totals alone has no last column.
function profileTable(sec) {
  const home = sec.place || '', cols = [];
  for (const a of sec.rows.flatMap(r => r.against)) if (a.geo && !cols.some(c => c.geo === a.geo)) cols.push({geo: a.geo, place: a.place});
  const compares = sec.rows.some(r => compareLines(r).length);
  const table = node('table', '', 'profile-table'), caption = table.createCaption(), headRow = table.createTHead().insertRow();
  table.setAttribute('role', 'table');
  caption.className = 'visually-hidden';
  caption.textContent = `${sec.title}: ${[home, ...cols.map(c => c.place)].join(', ')}`;
  table.tHead.setAttribute('role', 'rowgroup');
  headRow.setAttribute('role', 'row');
  const heading = (text, className) => { const th = node('th', text, className); th.scope = 'col'; th.setAttribute('role', 'columnheader'); headRow.append(th); };
  heading('Measure', 'profile-measure');
  heading(home, 'profile-figure home');
  for (const c of cols) heading(c.place, 'profile-figure');
  if (compares) heading('How it compares', 'profile-how');
  const body = table.createTBody();
  body.setAttribute('role', 'rowgroup');
  body.append(...sec.rows.flatMap(r => profileRow(r, home, cols, compares)));
  const box = node('div', '', 'profile-rows');
  box.append(table);
  if (compares) box.append(compareKey());
  tableFit.observe(box);
  tableFit.observe(table);
  return box;
}

// A second figure shown next to a row for comparison, or a sentence on how to read the row.
function besideLine(b) {
  const p = node('p', '', 'profile-beside');
  if (!b.value) { p.append(b.text); return p; }
  p.append('For comparison, ', node('b', b.value));
  if (b.spread) p.append(' ', node('span', b.spread.startsWith('range') ? `(${b.spread})` : b.spread, 'moe'));
  if (b.caution) p.append(' ', cautionChip(b.caution));
  p.append(` ${b.text}`);
  if (b.note) { const sup = node('sup', String(b.note)); sup.setAttribute('aria-label', `source ${b.note}`); p.append(sup); }
  return p;
}

// The section's citations, numbered when its figures come from more than one source, then any source
// that only the county and state figures come from.
function profileSources(sources, compared = []) {
  const box = node('div', '', 'profile-sources');
  const cite = (items, tag) => {
    const list = node(tag, '');
    // the year goes on its own line, so a wrapped link never leaves its comma at the start of a line
    for (const s of items) {
      const li = document.createElement('li');
      li.append(link(s.name, s.url), node('span', s.year, 'profile-source-year'));
      if (s.links && s.links.length) li.append(sourceLinks(s.links));
      if (s.match) li.append(matchLine(s.match));
      list.append(li);
    }
    return list;
  };
  box.append(node('p', sources.length > 1 ? 'Sources' : 'Source', 'profile-sources-title'), cite(sources, sources.length > 1 ? 'ol' : 'ul'));
  if (compared.length) box.append(node('p', 'County and state figures', 'profile-sources-title'), cite(compared, 'ul'));
  return box;
}

// The days a person checked these programs against their official pages, said once for the list. Stale rows say their
// own date, so they are left out.
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
function checkedLine(rows) {
  const days = [...new Set(rows.filter(r => r && !r.stale && ISO_DAY.test(r.verified_on || '')).map(r => r.verified_on))].sort();
  if (!days.length) return '';
  const [a, b] = [days[0], days[days.length - 1]].map(d => new Date(`${d}T00:00:00Z`));
  const say = (d, withYear) => d.toLocaleDateString('en-US', {timeZone: 'UTC', month: 'long', day: 'numeric', ...(withYear ? {year: 'numeric'} : {})});
  let span = say(b, true);
  if (a.getUTCFullYear() !== b.getUTCFullYear()) span = `${say(a, true)} to ${span}`;
  else if (a.getUTCMonth() !== b.getUTCMonth()) span = `${say(a, false)} to ${span}`;
  else if (a.getUTCDate() !== b.getUTCDate()) span = `${say(a, false)} to ${b.getUTCDate()}, ${b.getUTCFullYear()}`;
  return `Program details checked ${span}.`;
}
const telHref = phone => 'tel:+' + (phone.startsWith('1-') ? '' : '1') + phone.replace(/\D/g, '');
// A labeled line on a program row, such as who it is for.
function programLine(label, text, className) {
  const line = node('p', '', className);
  line.append(node('b', `${label} `), says('span', text));
  return line;
}

// One program: its name and level, what it gives, who it is for, one income limit with its date, then its official
// page and phone. A row marks when it was checked only once that is so long ago that the official page comes first.
function programRow(p) {
  const row = node('div', '', 'program');
  const name = node('p', '', 'program-name');
  name.append(says('span', p.name));
  if (p.level) name.append(' ', node('span', p.level, 'program-level'));
  row.append(name);
  if (p.description) row.append(says('p', p.description, 'program-about'));
  if (p.serves) row.append(programLine('For', p.serves, 'program-for'));
  if (p.limit && p.limit.text) row.append(programLine('Limit', `${p.limit.text}, in effect from ${p.limit.effective}.`, 'program-limit'));
  const actions = node('p', '', 'program-actions');
  if (p.url) {
    const page = link('Official page', p.url);
    // Screen readers hear which program the page is for, before the note that it opens a new tab.
    page.firstChild.after(hidden(` for ${p.name}`));
    actions.append(page);
  }
  if (p.phone) {
    const tel = node('a', `Call ${p.phone}`);
    tel.href = telHref(p.phone);
    const call = node('span', '');
    call.append(tel);
    if (p.phone_label) call.append(' ', node('span', p.phone_label, 'program-phone-label'));
    actions.append(call);
  }
  row.append(actions);
  if (p.stale && p.checked) row.append(node('p', p.checked, 'program-stale'));
  return row;
}

// Programs side by side when there is room.
function programGrid(rows) {
  const grid = node('div', '', 'program-grid');
  grid.append(...rows.map(programRow));
  return grid;
}

// Programs in need groups: each group's heading, its district figure once, then its programs. Groups whose figure sits
// above the state's stay open; the rest go in a closed disclosure that gives their count, unless no group stands out.
function programGroups(rows, groups, othersTitle, headingTag) {
  const byId = new Map(rows.map(r => [r.id, r]));
  const block = g => {
    const list = (g.programs || []).map(id => byId.get(id)).filter(Boolean);
    if (!list.length) return null;
    const box = node('section', '', 'program-group');
    box.append(says(headingTag, g.title, 'program-group-title'));
    if (g.figure && g.figure.text) {
      const figure = says('p', g.figure.text, 'program-figure');
      if (g.figure.caution) figure.append(' ', cautionChip(g.figure.caution));
      box.append(figure);
    }
    box.append(programGrid(list));
    return [box, list.length];
  };
  const above = groups.filter(g => g.above).map(block).filter(Boolean);
  const rest = groups.filter(g => !g.above).map(block).filter(Boolean);
  if (!above.length || !rest.length) return [...above, ...rest].map(([box]) => box);
  const more = node('details', '', 'program-others');
  const count = rest.reduce((n, [, size]) => n + size, 0);
  more.append(node('summary', `${othersTitle} (${count})`), ...rest.map(([box]) => box));
  return [...above.map(([box]) => box), more];
}

// A list of programs: in need groups when a district ranked them, otherwise one grid. The checked date leads it once.
function programList(rows, groups, othersTitle, headingTag) {
  const out = [], checked = checkedLine(rows);
  if (checked) out.push(node('p', checked, 'program-checked'));
  if (Array.isArray(groups) && groups.length) out.push(...programGroups(rows, groups, othersTitle, headingTag));
  else if (rows.length) out.push(programGrid(rows));
  return out;
}

// The district's matched programs, or null when the profile has none in the expected shape.
function programsOf(p) {
  const pr = p.programs;
  return pr && Array.isArray(pr.programs) && Array.isArray(pr.others) ? pr : null;
}
// Programs on the one-page summary: up to six, and at least three when the page runs long.
const SHEET_PROGRAMS = 6, SHEET_MIN_PROGRAMS = 3;
// The height a summary is fitted to: a US Letter page inside half-inch margins is 10 inches, 960 CSS pixels, less a
// little room for how the printer breaks the page.
const SHEET_HEIGHT = 930;

// The district's programs in need groups: those whose figure sits above the state's, then the others, closed. One
// line under them says that a district's figures cannot decide anyone's eligibility.
function programsPart(pr) {
  const content = programList([...pr.programs, ...pr.others], pr.groups, pr.others_title, 'h3');
  if (pr.note) content.push(node('p', pr.note, 'program-note'));
  if ((pr.sources || []).length) content.push(foldedSources(pr.sources));
  return {title: 'Programs to explore', content};
}
// Which profile sections the reader opened or closed in this tab, so the next profile opens the same ones.
const OPENED = 'klear-open';
function openedSections() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(OPENED));
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) return saved;
  } catch { /* blocked or unreadable */ }
  return {};
}
function rememberOpen(key, open) {
  const saved = openedSections();
  saved[key] = open;
  try { sessionStorage.setItem(OPENED, JSON.stringify(saved)); } catch { /* blocked or full */ }
}

// A section's number, as a printed brief numbers its sections so a reader can say "section 3". Screen readers skip it:
// the headings and links say the section's name.
function sectionNumber(n) {
  const s = node('span', String(n), 'sec-num');
  s.setAttribute('aria-hidden', 'true');
  return s;
}
// One group of the profile: a disclosure that the jump links at the top can open, holding one or more parts, each
// with its own heading when the group has more than one.
function profileSection(key, title, parts, number) {
  const d = node('details', '', 'profile-section'), summary = node('summary', '');
  summary.append(sectionNumber(number), node('span', title, 'sec-title'));
  d.id = `profile-${key}`;
  d.dataset.key = key;
  d.dataset.title = title;
  // The click comes before the disclosure changes, so the new state is the opposite of the current one.
  summary.addEventListener('click', () => rememberOpen(key, !d.open));
  d.append(summary);
  for (const part of parts) {
    const box = node('div', '', 'profile-part');
    if (parts.length > 1) box.append(says('h2', part.title, 'profile-part-title'));
    box.append(...part.content);
    d.append(box);
  }
  const back = node('p', '', 'profile-back'), up = node('a', 'Back to top of profile');
  up.href = '#district-head';
  back.append(up);
  d.append(back);
  return d;
}
// One section of the store's figures as a part of a group: what it covers when that is not the district itself, how
// its figures were estimated, its table, and its sources folded shut. Eligibility notes are left to the programs'
// one line, so a group says it once.
function dataPart(sec) {
  const rows = profileTable(sec), content = [];
  if (sec.scope) content.push(node('p', sec.scope, 'profile-scope'));
  // Which of a group's measures the section shows, such as the district's three largest industries of 13.
  if (sec.intro) content.push(node('p', sec.intro, 'profile-intro'));
  if (sec.caveat && sec.title !== 'Who may qualify') content.push(node('p', sec.caveat, 'profile-caveat lead'));
  if (sec.method) content.push(methodLine(sec.method));
  content.push(rows, foldedSources(sec.sources, sec.compared));
  return {title: sec.title, content, rows};
}

// A section's sources, shut until opened, with how many there are. Row numbers in the table point into this list.
function foldedSources(sources, compared = []) {
  const box = node('details', '', 'sources-fold'), count = sources.length + compared.length;
  box.append(node('summary', `Sources (${count})`), profileSources(sources, compared));
  return box;
}
// On wide screens the profile's first three groups open, unless the reader closed them earlier in this tab.
const OPEN_FIRST = 3;
function profileSections(p) {
  const parts = new Map(p.sections.filter(s => s.rows.length).map(s => [s.title, dataPart(s)]));
  if (programsOf(p)) parts.set('@programs', programsPart(p.programs));
  if (p.plan) parts.set('@plan', planPart(p.plan));
  const groups = PROFILE_GROUPS.map(([key, title, names]) => [key, title, names.filter(n => parts.has(n)).map(n => parts.get(n))]);
  // A section the groups do not name still shows, in a group of its own at the end.
  const named = new Set(PROFILE_GROUPS.flatMap(([, , names]) => names));
  for (const [title, part] of parts) if (!named.has(title)) groups.push([title.toLowerCase().replace(/[^a-z0-9]+/g, '-'), title, [part]]);
  // At a glance is section 1, so the groups count from 2.
  const out = groups.filter(([, , list]) => list.length).map(([key, title, list], i) => {
    const d = profileSection(key, title, list, i + 2);
    // A shut group reports no sizes, so its tables are fitted as it opens.
    d.addEventListener('toggle', () => { if (d.open) for (const part of list) if (part.rows) fitTable(part.rows); });
    if (key === 'programs') d.classList.add('profile-programs');
    return d;
  });
  const saved = openedSections();
  out.forEach((d, i) => { d.open = typeof saved[d.dataset.key] === 'boolean' ? saved[d.dataset.key] : !FLOAT.matches && i < OPEN_FIRST; });
  return out;
}
// The numbered links open their section before the browser follows the anchor.
function profileJumps(sections) {
  const nav = node('nav', '', 'profile-jumps'), list = node('ol', '');
  nav.setAttribute('aria-label', 'In this profile');
  for (const d of sections) {
    const li = document.createElement('li'), a = node('a', '');
    a.append(sectionNumber(d.querySelector('.sec-num').textContent), node('span', d.querySelector('.sec-title').textContent, 'sec-title'));
    a.href = `#${d.id}`;
    a.onclick = e => {
      if (plainClick(e) && d.matches('details') && !d.open) {
        d.open = true;
        rememberOpen(d.dataset.key, true);
      }
    };
    li.append(a);
    list.append(li);
  }
  nav.append(list);
  return nav;
}

// Follow the heading just below the header and links. At the foot of the page a short section may not be able to
// scroll that high, so keep a visible jump target current there, or mark the last section when reading down.
function updateProfileJumps() {
  if (view() !== 'district') return;
  const panel = $('profile'), nav = panel.querySelector('.profile-jumps');
  const bar = nav?.getBoundingClientRect();
  if (!bar?.height) return;
  const height = `${bar.height}px`;
  if (panel.style.getPropertyValue('--profile-jumps-h') !== height) panel.style.setProperty('--profile-jumps-h', height);
  const edge = Math.max(0, document.querySelector('.site-header').getBoundingClientRect().bottom, bar.bottom);
  const line = Math.min(edge + 16, innerHeight), links = [...nav.querySelectorAll('a')];
  let current = null, visibleTarget = null;
  for (const a of links) {
    const top = $(a.hash.slice(1)).getBoundingClientRect().top;
    if (top <= line) current = a;
    if (top >= edge && top < innerHeight && a.hash === location.hash) visibleTarget = a;
  }
  const page = document.scrollingElement;
  if (page.scrollTop > 0 && page.scrollTop + page.clientHeight >= page.scrollHeight - 1) current = visibleTarget || links.at(-1);
  const previous = nav.querySelector('[aria-current]');
  if (current !== previous) {
    previous?.removeAttribute('aria-current');
    current?.setAttribute('aria-current', 'location');
  }
}
let jumpFrame = 0;
function scheduleProfileJumps() {
  if (!jumpFrame) jumpFrame = requestAnimationFrame(() => { jumpFrame = 0; updateProfileJumps(); });
}
const profileJumpResize = new ResizeObserver(scheduleProfileJumps);
window.addEventListener('scroll', scheduleProfileJumps, {passive: true});
window.addEventListener('resize', scheduleProfileJumps);
window.addEventListener('hashchange', scheduleProfileJumps);

// The largest places in a district and the other chamber's districts it overlaps, with their shares of its residents.
function placesList(p) {
  const more = p.places.length - 4;
  return p.places.slice(0, 4).map(x => `${x.name} (${x.share})`).join(', ') + (more > 0 ? `, and ${more} more places` : '');
}
const overlapsList = p => p.overlaps.map(o => `${o.name} (${o.share})`).join(', ');

// The district's official area description and the permanent residents the Reapportionment Commission drew it on,
// each with the report page it comes from, then how that count differs from the ACS total, and the report.
function planPart(plan) {
  const box = node('div', '', 'profile-plan');
  const line = (label, text, cite) => {
    const l = node('p', '');
    l.append(node('span', label, 'profile-plan-label'), ` ${text} `, node('span', `(${cite})`, 'profile-plan-cite'));
    return l;
  };
  if (plan.description) box.append(line('Official description:', plan.description.text, plan.description.cite));
  if (plan.population) {
    box.append(line('Permanent residents in the 2022 plan:', plan.population.value, plan.population.cite));
    box.append(node('p', plan.population.note, 'profile-plan-note'));
  }
  const source = node('p', 'Source: ', 'profile-plan-note');
  source.append(link(plan.source.name, plan.source.url), `, ${plan.source.year}`);
  box.append(source);
  return {title: 'Reapportionment plan', content: [box]};
}
// Copy a link to the district. Without the clipboard API, as on a page served over plain HTTP, copy through a
// selection, and failing that show the address to copy by hand.
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fixed and transparent, so selecting it neither scrolls the page nor shows.
    const back = document.activeElement, box = node('textarea', text, 'copy-buffer');
    let copied = false;
    box.readOnly = true;
    document.body.append(box);
    box.focus({preventScroll: true});
    box.setSelectionRange(0, text.length);
    try { copied = document.execCommand('copy'); } catch { /* not supported */ }
    box.remove();
    if (back && back.focus) back.focus({preventScroll: true});
    return copied;
  }
}
async function copyLink(url, status) {
  const copied = await copyText(url);
  const text = copied ? 'Link copied.' : `Copy this link: ${url}`;
  status.replaceChildren(...(copied ? [icon('check'), text] : [text]));
  // Cleared first, so a second copy is announced again.
  $('profile-live').textContent = '';
  requestAnimationFrame(() => { $('profile-live').textContent = text; });
}

// Print, link and download as one row of actions at the right of the bar over a district's page, and print and link
// over a comparison. Print opens the choice of what to print (see openPrint).
function shareBar() {
  const bar = $('share-bar'), v = view(), geo = v === 'district' ? district : null;
  const pair = v === 'compare' && window.klearCompare ? window.klearCompare.geos() : [];
  const key = geo ? `district ${geo}` : pair.length ? `compare ${pair.join(',')}` : '';
  if (bar.dataset.key === key) {
    const print = bar.querySelector('.print-action');
    if (print) print.disabled = !printTarget();
    return;
  }
  bar.dataset.key = key;
  bar.replaceChildren();
  if (!key) return;
  // Short labels on the button, with the rest of each name kept for screen readers.
  const control = (tag, text, rest, name) => {
    const c = node(tag, '', 'action');
    c.append(icon(name), node('span', text, 'action-text'));
    if (rest) c.append(hidden(rest));
    if (tag === 'button') c.type = 'button';
    return c;
  };
  const print = control('button', 'Print', ' or save as PDF', 'print'), copy = control('button', 'Copy link', '', 'link');
  print.classList.add('print-action');
  print.disabled = !printTarget();
  const status = node('p', '', 'share-status');
  status.id = 'share-status';
  print.onclick = () => openPrint();
  copy.onclick = () => copyLink(location.origin + (geo ? districtPath(geo) : `/compare?d=${pair.join(',')}`), status);
  bar.append(print, copy);
  if (geo) {
    const csv = control('a', 'Download', ' data (CSV)', 'download');
    csv.href = '/api/profile.csv?district=' + encodeURIComponent(geo);
    csv.download = '';
    bar.append(csv);
  }
  bar.append(status);
}

// The groups where this district is in the top tier across its chamber, each linked to the outreach page that
// compares every district, or a line saying it is in none. A new tab, so a chat in progress stays where it is.
function profileOutreach(o) {
  const box = node('div', '', 'profile-outreach');
  // A level-two heading: the district's name is the page's only heading above it.
  box.append(node('h2', o.title));
  if (o.items.length) {
    const list = node('ul', '');
    for (const item of o.items) {
      const li = document.createElement('li');
      li.append(says('p', item.text));
      if (item.caution) li.append(cautionLine(item.caution));
      const more = node('p', '');
      more.append(link(item.link, item.url));
      li.append(more);
      list.append(li);
    }
    box.append(list);
  } else {
    box.append(node('p', o.none));
  }
  const foot = node('p', o.more || '', 'profile-outreach-more');
  if (!o.items.length) { foot.append(' '); foot.append(link(o.link.text, o.link.url)); }
  box.append(foot);
  return box;
}

// The district's legislator as the Legislature lists them, linked to their official page, with where and when
// the name was listed and whether that listing may predate the election results. Under the district's name the
// legislator takes one line, and the note opens beneath it, so the profile's first figures stay on the first screen.
function memberNote(m) {
  return `${m.listed.charAt(0).toUpperCase()}${m.listed.slice(1)}. ${m.timing}`;
}
function profileMembers(members) {
  const box = node('div', '', 'profile-member');
  for (const m of members) {
    const who = node('p', '', 'profile-member-name');
    who.append(node('span', m.title, 'profile-member-title'), ' ', link(m.reads || m.name, m.url));
    box.append(who);
  }
  const about = node('details', '', 'profile-member-about');
  about.append(node('summary', 'Where and when this was listed'), node('p', memberNote(members[0]), 'profile-member-note'));
  box.append(about);
  return box;
}

// Under an answer that names a member: their official page and the listing the name was read from.
function memberLinks(members) {
  const box = node('div', '', 'member-links');
  for (const m of members) {
    const p = node('p', '');
    p.append(`${m.title} ${m.reads || m.name}, ${m.district}: `, link('official page', m.url), '. Listed in ', link(m.source, m.source_url), '.');
    box.append(p);
  }
  return box;
}

function renderProfile(p) {
  // Under the district's name: the section links, At a glance, any outreach groups this district leads, and the
  // remaining sections. The share actions sit in the bar over the page.
  tableFit.disconnect();  // the tables of the profile shown before
  const glance = node('section', '', 'profile-glance'), head = node('div', '', 'glance-head'), title = node('h2', '', 'glance-title');
  glance.id = 'profile-glance';
  title.id = 'glance-title';
  glance.setAttribute('aria-labelledby', title.id);
  title.append(sectionNumber(1), node('span', 'At a glance', 'sec-title'));
  head.append(title);
  const stats = headlineStats(p.headline);
  glance.append(head, stats);
  if (stats.querySelector('.glance-strip')) glance.append(glanceKey(p));
  if (stats.querySelector('.compare-line')) glance.append(compareKey());
  glance.append(...glanceAbout(p));
  if (p.caveats && p.caveats.length) glance.append(placeNotes(p.caveats, 'profile-notes'));
  const sections = profileSections(p);
  const out = [profileJumps([glance, ...sections]), glance];
  // The outreach groups this district leads come just before the sections. A district that leads none says so at the
  // end of its programs, with the way to the outreach page.
  if (p.outreach && Array.isArray(p.outreach.items)) {
    const programs = sections.find(d => d.dataset.key === 'programs');
    if (p.outreach.items.length) out.push(profileOutreach(p.outreach));
    else if (programs && p.outreach.none) programs.querySelector('.profile-back').before(outreachNone(p.outreach));
  }
  out.push(...sections, profileEnd(p));
  return out;
}

// A district in no outreach group's top tier: one line, with the way to the outreach page.
function outreachNone(o) {
  const line = node('p', `${o.none} `, 'profile-outreach-none');
  if (o.link && o.link.url) line.append(link(o.link.text, o.link.url));
  return line;
}

// A district's headline figures with their margins. Under each, how it compares with the county and state, or for a
// total, its share of the state's.
function headlineStats(headline) {
  const stats = node('dl', '', 'profile-stats');
  // Each term comes before its value, as a description list needs; the CSS shows the value on top.
  for (const h of headline) {
    const s = node('div', ''), value = node('dd', '');
    value.append(node('span', h.value, 'num'));
    if (h.spread) value.append(' ', node('span', h.spread, 'moe'));
    if (h.caution) value.append(' ', cautionChip(h.caution));
    s.append(says('dt', h.label), value);
    const state = (h.against || []).find(a => /^state:/.test(a.geo || '')), lines = compareLines(h);
    if (lines.length) {
      const how = node('dd', '', 'profile-reading');
      for (const line of lines) how.append(line);
      if (state && state.value) how.append(node('span', `Hawaiʻi: ${state.value}`, 'reading-state'));
      s.append(how);
    } else if (h.unit === 'count' && state && isNumber(h.num) && state.num > 0) s.append(node('dd', `${shareOf(h.num / state.num)} of Hawaiʻi’s total`, 'profile-reading'));
    const strip = glanceStrip(h);
    if (strip) s.append(strip);
    stats.append(s);
  }
  return stats;
}

// Under a rate or an amount at a glance, a strip that sets the district, its county and the state on one line: the
// district's margin of error as a band in its chamber's color with a dot at its figure, the county a tick and the state
// a diamond, so the marks differ in shape as well as color. The words above it say the same, so screen readers skip it.
function glanceStrip(h) {
  const marks = ['county', 'state'].map(kind => [kind, (h.against || []).find(a => String(a.geo || '').startsWith(`${kind}:`))]).filter(([, a]) => a && isNumber(a.num));
  if (h.unit === 'count' || !isNumber(h.num) || !marks.length) return null;
  const low = isNumber(h.low) ? h.low : h.num, high = isNumber(h.high) ? h.high : h.num;
  const lo = Math.min(low, ...marks.map(([, a]) => a.num)), hi = Math.max(high, ...marks.map(([, a]) => a.num));
  if (!(hi > lo)) return null;
  const pad = (hi - lo) * 0.15, min = lo - pad, span = hi - lo + 2 * pad, at = v => `${(((v - min) / span) * 100).toFixed(2)}%`;
  const dd = node('dd', '', 'glance-strip'), band = node('span', '', 'gs-band'), here = node('span', '', 'gs-here');
  dd.setAttribute('aria-hidden', 'true');
  band.style.left = at(low);
  band.style.width = `${(((high - low) / span) * 100).toFixed(2)}%`;
  here.style.left = at(h.num);
  dd.append(band);
  for (const [kind, a] of marks) {
    const mark = node('span', '', `gs-${kind}`);
    mark.style.left = at(a.num);
    dd.append(mark);
  }
  dd.append(here);
  return dd;
}

// Where At a glance's figures come from: one short line with each source and the survey years its figures cover (not
// when KLEAR last updated its data), then, one tap away, each figure's definition, who it counts, how it was estimated
// and its source, for the district and for the county and state beside it.
function glanceAbout(p) {
  const rows = (p.headline || []).filter(h => h && h.source && h.source.name);
  if (!rows.length) return [];
  const sources = new Map();
  for (const h of rows) for (const s of [h.source, ...(h.against || []).map(a => a.source)]) if (s && s.name && !sources.has(`${s.name} ${s.year}`)) sources.set(`${s.name} ${s.year}`, s);
  const line = node('p', '', 'glance-source');
  line.append(`${sources.size > 1 ? 'Sources' : 'Source'}: `);
  [...sources.values()].forEach((s, i) => line.append(...(i ? ['; '] : []), link(s.name, s.url), `, ${s.year}`));
  line.append('.');
  const box = node('details', '', 'sources-fold glance-about'), list = node('dl', '', 'glance-defs');
  box.append(node('summary', 'About these figures'));
  for (const h of rows) {
    const item = node('div', ''), what = node('dd', '');
    item.append(says('dt', h.label));
    const m = h.meaning || {};
    if (m.plain) what.append(says('p', m.plain));
    if (m.universe) what.append(says('p', `Who is counted: ${m.universe}.`));
    const from = node('p', '');
    from.append(`${p.district}: `, link(h.source.name, h.source.url), `, ${h.source.year}${h.spread ? `, ${h.spread.startsWith('range') ? 'with its range' : 'with its 90% margin of error'}` : ''}.`);
    what.append(from);
    for (const a of (h.against || []).filter(x => x && x.source && !x.missing)) {
      const other = node('p', '');
      other.append(`${a.place}: `, link(a.source.name, a.source.url), `, ${a.source.year}.`);
      what.append(other);
    }
    if (h.method) what.append(methodLine(h.method));
    if (h.caveat) what.append(says('p', h.caveat));
    item.append(what);
    list.append(item);
  }
  box.append(list);
  return [line, box];
}

// What the strips' marks stand for, once under the headline figures.
function glanceKey(p) {
  const against = p.headline.flatMap(h => h.against || []), place = kind => (against.find(a => String(a.geo || '').startsWith(`${kind}:`)) || {}).place;
  const key = node('p', '', 'glance-key');
  key.setAttribute('aria-hidden', 'true');
  for (const [mark, text] of [['here', `${p.district || 'This district'}, with its margin of error`], ['county', place('county')], ['state', place('state')]]) {
    if (!text) continue;
    const item = node('span', '');
    item.append(node('i', '', `gk-${mark}`), text);
    key.append(item);
  }
  return key;
}
// A share to one decimal place below 10%, else whole numbers: 1.9%, 23%.
const shareOf = x => `${x < 0.1 ? (Math.round(x * 1000) / 10).toFixed(1) : Math.round(x * 100)}%`;

// The row that closes the profile: the way to compare this district, to the data and methods, and to another district.
// The first and last open in place, as the buttons over the page do.
function profileEnd(p) {
  const nav = node('nav', '', 'profile-end'), title = node('p', 'More from KLEAR', 'profile-end-title'), list = node('ul', '');
  title.id = 'profile-end-title';
  nav.setAttribute('aria-labelledby', title.id);
  for (const [text, href, go] of [['Compare with another district', `/compare?d=${p.geo}`, () => openCompare([p.geo])], ['Sources and methods', '/data'], ['Search another district', '/', () => goFind()]]) {
    const li = document.createElement('li'), a = node('a', text);
    a.href = href;
    if (go) a.addEventListener('click', e => { if (plainClick(e)) { e.preventDefault(); go(); } });
    li.append(a);
    list.append(li);
  }
  nav.append(title, list);
  return nav;
}

// The one-page summary that prints in place of the page: key figures beside the county and the state, programs to
// explore, and every source with its vintage. Paper cannot follow links, so every address is written out. A
// group's name in p.largest stands for the district's largest figures of that group, at most the number given.
const PRINT_GROUPS = [
  ['People', ['pop_total', 'pct_65_plus', 'pop_under_18']],
  ['Income and poverty', ['median_household_income', 'pct_people_poverty', 'pct_children_poverty', 'pct_65_plus_poverty']],
  ['Work and education', ['unemployment_rate', ['largest_industries', 1], 'pct_bachelors_plus']],
  ['Housing', ['pct_renter_occupied', 'median_gross_rent', 'pct_rent_burden_35_plus']],
  ['Health and assistance', ['pct_uninsured', 'pct_hh_snap']],
];
// Paper has less room than the profile for how the Commission's count differs from the ACS total.
const PLAN_SHEET_NOTE = 'It leaves out military members, families and students from other states, so it differs from the total population.';
const longDate = d => d.toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'});

// Printing. Print opens a dialog that asks what to print: a checkbox for At a glance and each of the profile's groups,
// whether to print the county's and the state's figures beside the district's, or a one-page summary instead. The
// printout is built from the choice when the reader presses Print and shows in place of the page, running as many
// pages as it needs. On Compare the same dialog prints the two districts side by side. A printout started from the
// browser's own menu prints the short brief: the district's name and legislator, At a glance, and Programs and help.
const PRINT_BRIEF = {summary: false, parts: ['glance', 'programs'], places: true};
let printChoice = null, sheetFit = null, sheetFresh = false;

// What there is to print: the district's profile on its page, or the profiles Compare has loaded.
function printTarget() {
  if (view() === 'district' && profileData && profileData.geo === district) return {kind: 'district', profiles: [profileData]};
  if (view() === 'compare' && window.klearCompare) {
    const list = window.klearCompare.loaded();
    if (list.length) return {kind: 'compare', profiles: list};
  }
  return null;
}

// The dialog's sections, numbered as the profile numbers them: At a glance, then each group a profile has.
const groupParts = (p, names) => names.some(n => (n === '@programs' ? programsOf(p) : n === '@plan' ? p.plan : p.sections.some(s => s.title === n && s.rows.length)));
function printSections(target) {
  const out = [['glance', 'At a glance']];
  for (const [key, title, names] of PROFILE_GROUPS) {
    if (target.profiles.some(p => groupParts(p, target.kind === 'compare' ? names.filter(n => !n.startsWith('@')) : names))) out.push([key, title]);
  }
  return out;
}

function openPrint() {
  const target = printTarget(), dialog = $('print-dialog');
  if (!target || typeof dialog.showModal !== 'function') { window.print(); return; }
  const compare = target.kind === 'compare', choice = printChoice || PRINT_BRIEF, chosen = new Set(choice.parts);
  $('print-list').replaceChildren(...printSections(target).map(([key, title], i) => {
    const label = node('label', '', 'print-part'), box = node('input', '');
    box.type = 'checkbox';
    box.name = 'part';
    box.value = key;
    box.checked = chosen.has(key);
    label.append(box, ' ', node('span', String(i + 1), 'print-num'), ' ', says('span', title));
    return label;
  }));
  $('print-title').textContent = compare ? `Print ${target.profiles.map(p => p.district).join(' and ')}` : `Print ${target.profiles[0].district}`;
  $('print-summary-row').hidden = compare;
  $('print-summary').checked = !compare && choice.summary;
  $('print-places').checked = choice.places;
  $('print-places-label').textContent = compare ? 'Hawaiʻi’s figures beside the districts' : 'County and state columns';
  $('print-error').textContent = '';
  syncPrint();
  dialog.showModal();
}
function syncPrint() {
  const one = $('print-summary').checked && !$('print-summary-row').hidden;
  $('print-parts').disabled = one;
  $('print-options').disabled = one;
}
$('print-summary').addEventListener('change', syncPrint);
$('print-all').onclick = () => { for (const b of $('print-list').querySelectorAll('input')) b.checked = true; $('print-error').textContent = ''; };
$('print-none').onclick = () => { for (const b of $('print-list').querySelectorAll('input')) b.checked = false; };
$('print-list').addEventListener('change', () => { $('print-error').textContent = ''; });
$('print-form').addEventListener('submit', e => {
  if (!e.submitter || e.submitter.value !== 'print') return;
  const summary = $('print-summary').checked && !$('print-summary-row').hidden;
  const parts = [...$('print-list').querySelectorAll('input:checked')].map(b => b.value);
  if (!summary && !parts.length) {
    e.preventDefault();
    $('print-error').textContent = 'Choose at least one section to print.';
    $('print-list').querySelector('input').focus();
    return;
  }
  printChoice = {summary, parts, places: $('print-places').checked};
});
$('print-dialog').addEventListener('close', () => {
  if ($('print-dialog').returnValue !== 'print') return;
  $('print-dialog').returnValue = '';
  buildSheet(printChoice);
  sheetFresh = true;
  // After the dialog has gone, so the browser's print preview opens on the page.
  setTimeout(() => window.print(), 0);
});

// Lay the chosen printout into the sheet. A printout that cannot be laid out leaves the page to print as it is.
function buildSheet(choice) {
  const sheet = $('print-sheet'), target = printTarget();
  let parts = [];
  sheetFit = null;
  sheet.style.zoom = '';
  try {
    if (target && target.kind === 'compare') parts = compareSheet(target.profiles, choice);
    else if (target && choice.summary) { parts = sheetParts(target.profiles[0]); sheetFit = target.profiles[0]; }
    else if (target) parts = districtSheet(target.profiles[0], choice);
  } catch (err) {
    console.warn('KLEAR could not lay out the printout.', err);
    parts = [];
    sheetFit = null;
  }
  sheet.replaceChildren(...parts);
  sheet.classList.toggle('one-page', Boolean(sheetFit));
  sheet.dataset.chamber = parts.length ? target.profiles[0].geo.split(':')[0] : '';
  document.body.classList.toggle('has-sheet', parts.length > 0);
  fitSheet();
}
// The printout for what shows now, so the browser's own Print prints it too: the last choice, or the short brief.
function refreshSheet() {
  buildSheet(printChoice || PRINT_BRIEF);
}
// Lay a one-page summary out unseen at its printed width and keep it to one page: list fewer programs, down to three,
// then scale the whole page down.
function fitSheet() {
  const sheet = $('print-sheet'), p = sheetFit;
  if (!p) return;
  sheet.classList.add('measure');
  sheet.style.zoom = '';
  try {
    for (let n = SHEET_PROGRAMS; ; n--) {
      sheet.replaceChildren(...sheetParts(p, n));
      if (sheet.getBoundingClientRect().height <= SHEET_HEIGHT || n <= SHEET_MIN_PROGRAMS) break;
    }
    const height = sheet.getBoundingClientRect().height;
    if (height > SHEET_HEIGHT) sheet.style.zoom = String(Math.floor((SHEET_HEIGHT / height) * 1000) / 1000);
  } catch (err) {
    console.warn('KLEAR could not fit the printed summary to one page.', err);
  } finally {
    sheet.classList.remove('measure');
  }
}
// The print date is the day it is printed, even when the page has been open since an earlier day. A printout the
// dialog did not just build is built now from the last choice, or the short brief.
window.addEventListener('beforeprint', () => {
  if (!sheetFresh) buildSheet(printChoice || PRINT_BRIEF);
  sheetFresh = false;
  for (const printed of $('print-sheet').querySelectorAll('.sheet-printed')) printed.textContent = `Printed ${longDate(new Date())}`;
});

// Sources are numbered in the order the figures cite them; each row's Source cell lists every source it uses.
function citations() {
  const sources = [];
  const cite = s => {
    if (!s || !s.name) return 0;
    const i = sources.findIndex(x => x.name === s.name && x.year === s.year);
    return i < 0 ? sources.push(s) : i + 1;
  };
  return {sources, cite};
}
const sheetUrl = text => node('span', text, 'sheet-url');
function sheetSection(title, className, ...content) {
  const s = node('section', '', `sheet-section ${className}`);
  s.append(says('h2', title), ...content);
  return s;
}
// The numbered list of every source cited, each with its year and address, and the line that closes the printout.
function sheetEnd(sources) {
  const out = [];
  if (sources.length) {
    const list = node('ol', '', 'sheet-list');
    for (const s of sources) {
      const li = node('li', [s.name, s.year].filter(Boolean).join(', '));
      if (s.url) li.append(' ', sheetUrl(s.url));
      list.append(li);
    }
    out.push(sheetSection('Sources', 'sheet-sources', list));
  }
  // A paragraph, not a footer element, so the site footer's band never reaches the page.
  out.push(node('p', 'Public, aggregated data from KLEAR, made for HAIC 2026. Not an official State of Hawaiʻi product.', 'sheet-foot'));
  return out;
}
// The printout's heading: what it is and who it covers on the left; where to find it online, and how current it is,
// on the right.
function sheetHeading(kicker, title, lines, address) {
  const head = node('header', '', 'sheet-head'), who = node('div', ''), live = node('div', '', 'sheet-live');
  who.append(node('p', kicker, 'sheet-kicker'), node('h1', title), ...lines);
  live.append(node('p', 'Full profile, sources and questions online', ''), sheetUrl(location.origin + address));
  const dates = node('p', '', 'sheet-dates');
  dates.append(node('span', `Printed ${longDate(new Date())}`, 'sheet-printed'));
  live.append(dates);
  head.append(who, live);
  return head;
}
function memberLines(p) {
  return (p.members || []).map(m => {
    const line = node('p', '', 'sheet-member');
    line.append(node('b', m.title), ` ${m.reads || m.name} `, sheetUrl(m.url));
    return line;
  });
}
const againstOf = (r, kind) => (r.against || []).find(a => !a.missing && String(a.geo || '').startsWith(`${kind}:`)) || null;

// A printed table of figures: a column pair (estimate, then margin) for each place, an optional column of words, and
// the sources each row cites. groups is a list of [title, rows], each row {label, figs, words}, with one figure (or
// null) per place and a list of comparison lines.
function sheetTable(columns, groups, cite, wordsTitle) {
  const table = node('table', '', 'sheet-figures'), headRow = table.createTHead().insertRow(), measure = node('th', 'Measure');
  measure.scope = 'col';
  headRow.append(measure);
  for (const c of columns) {
    const th = node('th', c.name, `place ${c.kind}`);
    th.colSpan = 2;
    th.scope = 'colgroup';
    headRow.append(th);
  }
  if (wordsTitle) { const th = node('th', wordsTitle, 'words'); th.scope = 'col'; headRow.append(th); }
  const sourceHead = node('th', 'Source', 'src');
  sourceHead.scope = 'col';
  headRow.append(sourceHead);
  let unreliable = false, ranged = false;
  for (const [title, rows] of groups) {
    if (!rows.length) continue;
    const body = table.createTBody();
    if (title) {
      const group = says('th', title, 'sheet-group');
      group.colSpan = 2 + columns.length * 2 + (wordsTitle ? 1 : 0);
      group.scope = 'colgroup';
      body.insertRow().append(group);
    }
    for (const r of rows) {
      const tr = body.insertRow(), label = says('th', r.label), cited = [];
      label.scope = 'row';
      tr.append(label);
      r.figs.forEach((f, i) => {
        const kind = columns[i].kind;
        if (!f || f.missing) {
          // A compared district's row says why it has no figure, as the page does.
          const none = node('td', f && f.label && typeof f.missing === 'string' ? f.missing : 'Not available', `sheet-none ${kind}`);
          none.colSpan = 2;
          tr.append(none);
          return;
        }
        const value = node('td', f.value, `num ${kind}`);
        if (f.caution) { value.append('†'); unreliable = true; }
        ranged ||= String(f.spread || '').startsWith('range');
        cited.push(cite(f.source));
        tr.append(value, node('td', f.spread || '', `moe ${kind}`));
      });
      if (wordsTitle) {
        const words = node('td', '', 'words');
        words.append(...r.words);
        tr.append(words);
      }
      tr.append(node('td', [...new Set(cited.filter(Boolean))].sort((a, b) => a - b).join(', '), 'src'));
    }
  }
  const notes = ['± is the 90% margin of error. Where two figures’ margins overlap, the difference may not be real.'];
  if (ranged) notes.push('A range gives the low and high bounds of an estimate.');
  if (unreliable) notes.push('† The margin of error is large; use with care.');
  if (wordsTitle === 'How it compares') notes.push(PROFILE_COMPARISON_KEY);
  if (wordsTitle === 'Difference') notes.push(PAIR_COMPARISON_KEY);
  return [table, node('p', notes.join(' '), 'sheet-note')];
}

// A district's printout: its heading, then each chosen section's figures with the county and the state beside them
// when asked, its programs written out with their addresses, and every source.
function districtSheet(p, choice) {
  const {sources, cite} = citations(), chosen = new Set(choice.parts);
  const county = (p.headline.concat(p.sections.flatMap(s => s.rows)).map(r => againstOf(r, 'county')).find(Boolean) || {}).place;
  const columns = [{kind: 'home', name: p.district, get: r => r}];
  if (choice.places && county) columns.push({kind: 'county', name: county, get: r => againstOf(r, 'county')});
  if (choice.places) columns.push({kind: 'state', name: 'Hawaiʻi', get: r => againstOf(r, 'state')});
  const rowsOf = rows => rows.map(r => ({label: r.label, figs: columns.map(c => c.get(r)), words: compareLines(r)}));
  const lines = [];
  if (p.where) lines.push(node('p', p.where, 'sheet-where'));
  lines.push(...memberLines(p));
  const parts = [sheetHeading('KLEAR district profile', p.district, lines, districtPath(p.geo))];
  // Where the district is goes under its heading, though its source is numbered after the figures'.
  const about = node('div', '', 'sheet-about'), line = (label, text) => { const l = node('p', ''); l.append(node('b', label), ` ${text}`); return l; };
  parts.push(about);
  let number = 1;
  if (chosen.has('glance')) parts.push(sheetSection(`${number} At a glance`, 'sheet-glance', ...sheetTable(columns, [['', rowsOf(p.headline)]], cite, choice.places ? 'How it compares' : '')));
  if (p.caveats && p.caveats.length && chosen.has('glance')) {
    const list = node('ul', '', 'sheet-list');
    for (const c of p.caveats) { const li = document.createElement('li'); li.append(node('b', `${c.title}.`), ' ', says('span', c.text)); list.append(li); }
    parts.push(sheetSection('About this district’s figures', 'sheet-notes', list));
  }
  for (const [key, title, names] of PROFILE_GROUPS) {
    if (!groupParts(p, names)) continue;
    number++;
    if (!chosen.has(key)) continue;
    // The group's programs first, then its figures in one table, each part under its own title, then the plan.
    const blocks = [], tables = [], notes = [];
    for (const name of names) {
      if (name === '@programs') { blocks.push(...programsSheet(p)); continue; }
      if (name === '@plan') continue;
      const sec = p.sections.find(s => s.title === name);
      if (!sec || !sec.rows.length) continue;
      tables.push([names.length > 1 ? sec.title : '', rowsOf(sec.rows)]);
      if (sec.scope) notes.push(says('p', `${sec.title}: ${sec.scope}`, 'sheet-note sheet-scope'));
    }
    if (tables.length) blocks.push(...notes, ...sheetTable(columns, tables, cite, choice.places ? 'How it compares' : ''));
    if (names.includes('@plan') && p.plan) blocks.push(planSheet(p.plan, cite));
    if (blocks.length) parts.push(sheetSection(`${number} ${title}`, `sheet-part sheet-part-${key}`, ...blocks));
  }
  if (p.places.length) about.append(line('Includes', placesList(p)));
  if (p.overlaps.length) about.append(line('Overlaps', overlapsList(p)));
  if (about.lastChild) {
    const n = cite(p.shares_source);
    about.lastChild.append(`. Shares of 2020 Census residents${n ? ` (source ${n})` : ''}.`);
  }
  if ((p.members || []).length) about.append(node('p', memberNote(p.members[0]), 'sheet-member-note'));
  if (!about.lastChild) parts.splice(parts.indexOf(about), 1);
  parts.push(...sheetEnd(sources));
  return parts;
}

// Every program on the profile, by need group, each with what it is, who it is for, its limit and how to reach it.
function programsSheet(p) {
  const matched = programsOf(p);
  if (!matched) return [];
  const named = [...matched.programs, ...matched.others].filter(g => g && g.name), byProgram = new Map(named.map(g => [g.id, g]));
  const groups = Array.isArray(matched.groups) && matched.groups.length ? matched.groups : [{title: '', programs: named.map(g => g.id)}];
  const out = [node('h3', 'Programs to explore', 'sheet-sub')], listed = new Set();
  const box = node('div', '', 'sheet-program-list');
  for (const g of groups) {
    const programs = (g.programs || []).map(id => byProgram.get(id)).filter(r => r && !listed.has(r.id));
    if (!programs.length) continue;
    if (g.title) {
      const head = node('p', '', 'sheet-program-group');
      head.append(says('b', `${g.title}.`));
      if (g.figure && g.figure.text) head.append(' ', says('span', g.figure.text));
      out.push(head);
    }
    const list = node('ul', '', 'sheet-list');
    for (const r of programs) {
      listed.add(r.id);
      const li = document.createElement('li');
      li.append(node('b', r.name));
      if (r.level) li.append(` (${r.level})`);
      if (r.description) li.append(': ', says('span', r.description));
      if (r.serves) li.append(' ', node('b', 'For'), ' ', says('span', r.serves));
      if (r.limit && r.limit.text) li.append(' ', node('b', 'Limit'), ` ${r.limit.text}, in effect from ${r.limit.effective}.`);
      const reach = [r.url, r.phone && `Call ${r.phone}`].filter(Boolean).join(' · ');
      if (reach) li.append(' ', sheetUrl(reach));
      if (r.stale && r.checked) li.append(` ${r.checked}`);
      list.append(li);
    }
    out.push(list);
  }
  if (!listed.size) return [];
  const checked = checkedLine([...listed].map(id => byProgram.get(id)));
  out.push(node('p', `${checked} ${matched.note || ''}`.replace(/\s+/g, ' ').trim(), 'sheet-note'));
  box.append(...out);
  return [box];
}
function planSheet(plan, cite) {
  const box = node('div', '', 'sheet-about sheet-plan-box'), n = cite(plan.source), at = c => `(${n ? `source ${n}, ` : ''}${c})`;
  const line = (label, text) => { const l = node('p', ''); l.append(node('b', label), ` ${text}`); return l; };
  box.append(node('h3', 'Reapportionment plan', 'sheet-sub'));
  if (plan.description) box.append(line('Official description', `${plan.description.text} ${at(plan.description.cite)}`));
  if (plan.population) box.append(line('Permanent residents in the 2022 plan', `${plan.population.value} ${at(plan.population.cite)}. ${plan.population.note}`));
  return box;
}

// Whether a compared part holds county-level figures, which are a county's, never a district's own.
const countyPart = sec => Boolean(sec && sec.scope && String(sec.geo || '').startsWith('county:'));
// Whether two districts' county-level figures are one county's.
const sameCounty = parts => parts.length === 2 && Boolean(parts[0] && parts[1]) && parts[0].geo === parts[1].geo;

// What county-level figures stand for beside two districts, on the page and on paper: the one county both lie in, or
// each district's own county.
function countyContext(parts, names) {
  const live = parts.filter(Boolean);
  if (sameCounty(parts)) {
    return `These figures cover all of ${live[0].place}, where most residents of both ${names[0]} and ${names[1]} live. They are not published for districts, so both districts have the same county figure, which cannot show how the two differ.`;
  }
  if (live.length === 2) {
    return `These figures are for each district’s county: ${parts[0].place} for ${names[0]}, and ${parts[1].place} for ${names[1]}. They are not published for districts, so a difference here is between the two counties, not the districts.`;
  }
  return live.length ? live[0].scope : '';
}

// The notes a compared part prints before its table: whose figures they are, what the rows are, how the figures were
// estimated, and the notes on its measures, each said once.
function partSheetNotes(parts, names, title) {
  const live = parts.filter(Boolean), out = [], seen = new Set();
  const add = text => { if (text && !seen.has(text)) { seen.add(text); out.push(says('p', `${title}: ${text}`, 'sheet-note sheet-scope')); } };
  if (countyPart(live[0])) add(countyContext(parts, names));
  for (const s of live) add(s.intro);
  for (const s of live) if (s.method) add(`How estimated: ${s.method.text} (${new URL(s.method.url, location.origin).href}).`);
  for (const s of live) add(s.caveat);
  // A note on some of the rows names the rows it applies to.
  const methods = new Map(), notes = new Map(), apply = (map, key, label) => map.set(key, [...new Set([...(map.get(key) || []), label])]);
  for (const s of live) for (const r of s.rows) {
    if (r.method) apply(methods, `${r.method.text} (${new URL(r.method.url, location.origin).href})`, r.label);
    if (r.caveat) apply(notes, r.caveat, r.label);
  }
  for (const [text, labels] of methods) add(`How estimated: ${text}. Applies to: ${labels.join('; ')}.`);
  for (const [text, labels] of notes) add(`${text} Applies to: ${labels.join('; ')}.`);
  return out;
}

// The comparison's printout: both districts in the heading, then each chosen section's figures side by side, with
// Hawaiʻi's when asked and a column saying whether the two differ beyond their margins of error. It prints the same
// aligned rows as the page: every row either district's profile shows, for both, and why a district has no figure.
// County-level rows say whose figures they are, and two districts in one county get no difference.
function compareSheet(list, choice) {
  const {sources, cite} = citations(), chosen = new Set(choice.parts), names = list.map(p => p.district);
  const columns = list.map((p, i) => ({kind: i ? 'other' : 'home', name: p.district}));
  if (choice.places) columns.push({kind: 'state', name: 'Hawaiʻi'});
  const rowsFor = parts => {
    const live = parts.filter(Boolean), county = countyPart(live[0]), shared = county && sameCounty(parts);
    const ids = [...new Set(live.flatMap(s => s.rows.map(r => r.id)))];
    return ids.map(id => {
      const mine = parts.map(s => (s ? s.rows.find(r => r.id === id) || null : null)), first = mine.find(r => r && !r.missing) || mine.find(Boolean);
      const figs = mine.slice();
      if (choice.places) figs.push(mine.map(r => r && againstOf(r, 'state')).find(Boolean) || null);
      let line = null;
      if (shared) line = node('span', `Same ${live[0].place} figure for both`, 'compare-line');
      else if (list.length === 2) line = pairSheetLine(mine[0], mine[1], county ? parts.map((s, j) => (s ? s.place : names[j])) : names);
      return {label: first.label, figs, words: line ? [line] : []};
    });
  };
  const lines = list.map(p => {
    const l = node('p', '', 'sheet-where');
    l.append(node('b', p.district), p.where ? ` · ${p.where}` : '');
    for (const m of p.members || []) l.append(` · ${m.title} ${m.reads || m.name}`);
    return l;
  });
  const parts = [sheetHeading('KLEAR comparison', names.join(' and '), lines, `/compare?d=${list.map(p => p.geo).join(',')}`)];
  const words = list.length === 2 ? 'Difference' : '';
  let number = 1;
  if (chosen.has('glance')) {
    const glance = list.map(p => ({title: 'At a glance', rows: p.headline}));
    parts.push(sheetSection(`${number} At a glance`, 'sheet-glance', ...partSheetNotes(glance, names, 'At a glance'), ...sheetTable(columns, [['', rowsFor(glance)]], cite, words)));
  }
  for (const [key, title, sectionNames] of PROFILE_GROUPS) {
    const data = sectionNames.filter(n => !n.startsWith('@'));
    if (!list.some(p => groupParts(p, data))) continue;
    number++;
    if (!chosen.has(key)) continue;
    const found = data.map(n => list.map(p => p.sections.find(s => s.title === n && s.rows.length) || null)).filter(part => part.some(Boolean));
    const named = part => (countyPart(part.find(Boolean)) ? 'County context' : part.find(Boolean).title);
    const notes = found.flatMap(part => partSheetNotes(part, names, named(part)));
    const groups = found.map(part => [data.length > 1 || countyPart(part.find(Boolean)) ? named(part) : '', rowsFor(part)]).filter(([, rows]) => rows.length);
    if (groups.length) parts.push(sheetSection(`${number} ${title}`, `sheet-part sheet-part-${key}`, ...notes, ...sheetTable(columns, groups, cite, words)));
  }
  parts.push(...sheetEnd(sources));
  return parts;
}

// The one-page summary: key figures beside the county and the state, the top programs, and every source.
function sheetParts(p, limit = SHEET_PROGRAMS) {
  // Sources are numbered in the order the figures cite them; each row's Source cell lists every source it uses.
  const sources = [];
  const cite = s => {
    if (!s || !s.name) return 0;
    const i = sources.findIndex(x => x.name === s.name && x.year === s.year);
    return i < 0 ? sources.push(s) : i + 1;
  };
  const url = text => node('span', text, 'sheet-url');
  const section = (title, className, ...content) => {
    const s = node('section', '', `sheet-section ${className}`);
    s.append(node('h2', title), ...content);
    return s;
  };
  const byId = new Map(p.sections.flatMap(s => s.rows.map(r => [r.id, r])));
  const expand = id => Array.isArray(id) ? ((p.largest || {})[id[0]] || []).slice(0, id[1]) : [id];
  const groups = PRINT_GROUPS.map(([title, ids]) => [title, ids.flatMap(expand).map(id => byId.get(id)).filter(Boolean)]).filter(([, rows]) => rows.length);
  const against = (r, kind) => (r.against || []).find(a => !a.missing && String(a.geo || '').startsWith(`${kind}:`));
  const placeOf = kind => (groups.flatMap(([, rows]) => rows).map(r => against(r, kind)).find(a => a && a.place) || {}).place;
  // A column pair (estimate, then margin) for the district, then its county and the state when the profile has them.
  const places = [['home', p.district], ['county', placeOf('county')], ['state', placeOf('state')]].filter(([, name]) => name);

  const table = node('table', '', 'sheet-figures'), headRow = table.createTHead().insertRow(), measure = node('th', 'Measure');
  measure.scope = 'col';
  headRow.append(measure);
  for (const [kind, name] of places) {
    const th = node('th', name, `place ${kind}`);
    th.colSpan = 2;
    th.scope = 'colgroup';
    headRow.append(th);
  }
  const sourceHead = node('th', 'Source', 'src');
  sourceHead.scope = 'col';
  headRow.append(sourceHead);
  let unreliable = false, ranged = false;
  for (const [title, rows] of groups) {
    const body = table.createTBody(), group = node('th', title, 'sheet-group');
    group.colSpan = 2 + places.length * 2;
    group.scope = 'colgroup';
    body.insertRow().append(group);
    for (const r of rows) {
      const tr = body.insertRow(), label = says('th', r.label), cited = [];
      label.scope = 'row';
      tr.append(label);
      for (const [kind] of places) {
        const f = kind === 'home' ? r : against(r, kind);
        if (!f) {
          const none = node('td', 'Not available', `sheet-none ${kind}`);
          none.colSpan = 2;
          tr.append(none);
          continue;
        }
        const value = node('td', f.value, `num ${kind}`);
        if (f.caution) { value.append('†'); unreliable = true; }
        ranged ||= String(f.spread || '').startsWith('range');
        cited.push(cite(f.source));
        tr.append(value, node('td', f.spread || '', `moe ${kind}`));
      }
      tr.append(node('td', [...new Set(cited.filter(Boolean))].sort((a, b) => a - b).join(', '), 'src'));
    }
  }
  const margins = ['± is the 90% margin of error. Where two figures’ margins overlap, the difference may not be real.'];
  if (ranged) margins.push('A range gives the low and high bounds of an estimate.');
  if (unreliable) margins.push('† The margin of error is large; use with care.');

  // Who the district is on the left; where to find it online, and how current it is, on the right.
  const head = node('header', '', 'sheet-head'), who = node('div', ''), live = node('div', '', 'sheet-live');
  who.append(node('p', 'KLEAR district summary', 'sheet-kicker'), node('h1', p.district));
  if (p.where) who.append(node('p', p.where, 'sheet-where'));
  for (const m of p.members || []) {
    const line = node('p', '', 'sheet-member');
    line.append(node('b', m.title), ` ${m.reads || m.name} `, url(m.url));
    who.append(line);
  }
  live.append(node('p', 'Full profile, sources and questions online', ''), url(location.origin + districtPath(p.geo)));
  const dates = node('p', '', 'sheet-dates');
  dates.append(node('span', `Printed ${longDate(new Date())}`, 'sheet-printed'));
  live.append(dates);
  head.append(who, live);

  const parts = [head];
  const about = node('div', '', 'sheet-about'), line = (label, text) => { const l = node('p', ''); l.append(node('b', label), ` ${text}`); return l; };
  if (p.places.length) about.append(line('Includes', placesList(p)));
  if (p.overlaps.length) about.append(line('Overlaps', overlapsList(p)));
  if (about.lastChild) {
    const n = cite(p.shares_source);
    about.lastChild.append(`. Shares of 2020 Census residents${n ? ` (source ${n})` : ''}.`);
  }
  if ((p.members || []).length) about.append(node('p', memberNote(p.members[0]), 'sheet-member-note'));
  if (p.plan) {
    const n = cite(p.plan.source), at = c => `(${n ? `source ${n}, ` : ''}${c})`;
    const small = l => { l.className = 'sheet-plan'; return l; };
    if (p.plan.description) about.append(small(line('Official description', `${p.plan.description.text} ${at(p.plan.description.cite)}`)));
    if (p.plan.population) about.append(small(line('Permanent residents in the 2022 plan', `${p.plan.population.value} ${at(p.plan.population.cite)}. ${PLAN_SHEET_NOTE}`)));
  }
  if (about.lastChild) parts.push(about);
  parts.push(section('Key figures', 'sheet-key', table, node('p', margins.join(' '), 'sheet-note')));
  if (p.caveats && p.caveats.length) {
    const list = node('ul', '', 'sheet-list');
    for (const c of p.caveats) { const li = document.createElement('li'); li.append(node('b', `${c.title}.`), ' ', says('span', c.text)); list.append(li); }
    parts.push(section('About this district’s figures', 'sheet-notes', list));
  }

  // The programs in the need groups whose figures here sit above the state's, or every group when none does, each
  // group's figure said once and each program as on the profile, up to the limit.
  const matched = programsOf(p), shown = matched ? (matched.programs.length ? matched.programs : matched.others) : [];
  const named = shown.filter(g => g && g.name), byProgram = new Map(named.map(g => [g.id, g]));
  const needGroups = matched && Array.isArray(matched.groups) && matched.groups.length
    ? matched.groups.filter(g => g.above === (matched.programs.length > 0))
    : [{title: '', programs: named.map(g => g.id)}];
  const blocks = [], printed = [];
  for (const g of needGroups) {
    const programs = (g.programs || []).map(id => byProgram.get(id)).filter(Boolean).slice(0, limit - printed.length);
    if (!programs.length) continue;
    printed.push(...programs);
    if (g.title) {
      const head = node('p', '', 'sheet-program-group');
      head.append(says('b', `${g.title}.`));
      if (g.figure && g.figure.text) head.append(' ', says('span', g.figure.text));
      blocks.push(head);
    }
    const list = node('ul', '', 'sheet-list');
    for (const r of programs) {
      const li = document.createElement('li');
      li.append(node('b', r.name));
      if (r.level) li.append(` (${r.level})`);
      if (r.description) li.append(': ', says('span', r.description));
      if (r.serves) li.append(' ', node('b', 'For'), ' ', says('span', r.serves));
      if (r.limit && r.limit.text) li.append(' ', node('b', 'Limit'), ` ${r.limit.text}, in effect from ${r.limit.effective}.`);
      // The page and the phone share one line, so a program takes as little of the page as it can.
      const reach = [r.url, r.phone && `Call ${r.phone}`].filter(Boolean).join(' · ');
      if (reach) li.append(' ', url(reach));
      if (r.stale && r.checked) li.append(` ${r.checked}`);
      list.append(li);
    }
    blocks.push(list);
  }
  if (printed.length) {
    const why = matched.programs.length ? 'Listed because this district’s need figures sit above the state’s, highest first.' : 'None of this district’s need figures sit clearly above the state’s, so these programs may help residents here.';
    const more = named.length > printed.length ? ' The live profile lists every program.' : '';
    const checked = checkedLine(printed);
    parts.push(section('Programs to explore', 'sheet-programs', ...blocks, node('p', `${why}${more} ${checked} ${matched.note || ''}`.replace(/\s+/g, ' ').trim(), 'sheet-note')));
  }
  const list = node('ol', '', 'sheet-list');
  for (const s of sources) {
    const li = node('li', [s.name, s.year].filter(Boolean).join(', '));
    if (s.url) li.append(' ', url(s.url));
    list.append(li);
  }
  if (sources.length) parts.push(section('Sources', 'sheet-sources', list));
  // A paragraph, not a footer element, so the site footer's dark band never reaches the page.
  parts.push(node('p', 'Public, aggregated data from KLEAR, made for HAIC 2026. Not an official State of Hawaiʻi product.', 'sheet-foot'));
  return parts;
}

function sentLine(question, chamber) {
  const sent = node('p', '', 'user');
  sent.append(hidden('You asked: '), question);
  sent.dataset.chamber = chamber;
  return sent;
}

// This tab's questions and answers, kept in session storage so a refresh does not lose them. Storage can be blocked
// or full; the chat works the same without it. Errors are not kept: some browsers fail a request as the page reloads,
// which is not an error worth showing again. The newest question without an answer is kept, to wait in the box.
const SAVED_CHAT = 'klear-chat';
const exchanges = [];
function saveChat() {
  const kept = exchanges.filter((x, i) => x.answer || i === exchanges.length - 1);
  // When storage is full, keep the newest exchanges that fit.
  for (let from = 0; from <= kept.length; from++) {
    try { sessionStorage.setItem(SAVED_CHAT, JSON.stringify(kept.slice(from))); return; } catch { /* full or blocked */ }
  }
}

function restoreChat() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(SAVED_CHAT)); } catch { /* blocked or unreadable */ }
  if (!Array.isArray(saved)) return;
  let last = null, sent = null;
  for (const x of saved) {
    // geo is null for a question asked with no district chosen.
    if (!x || (typeof x.geo !== 'string' && x.geo !== null) || typeof x.name !== 'string' || typeof x.question !== 'string') continue;
    // A question whose answer was still loading, or failed, waits in the box to ask again.
    if (!x.answer) {
      if (!$('question').value.trim()) { $('question').value = x.question; fitQuestion(); }
      continue;
    }
    let reply;
    try { reply = renderAnswer(x.answer, x.name); } catch { continue; }
    const about = answerAbout(x.answer, x.geo, x.name);
    if (about.geo) reply.dataset.chamber = about.geo.split(':')[0];
    showMode(reply, x.answer);
    askedQuestions.add(sameQuestion(x.question, x.name));
    if (last && x.geo && x.geo !== last) $('log').append(switchLine(x.name));
    sent = sentLine(x.question, x.geo ? x.geo.split(':')[0] : '');
    $('log').append(sent, reply);
    exchanges.push(x);
    last = x.geo || last;
  }
  saveChat();
  if (!sent) return;
  foldSuggestions();
  asked = last;
  $('chat-card').classList.add('started');
  if (logScrolls()) $('messages').scrollTop = sent.offsetTop - 16;
}

// Restore the conversation and show what the address names. This waits for the page's other scripts, so the map and
// the compare view hear which district opened too. /ask, the old Ask page, is the Find page with the agent's panel
// open; a question handed over after #q= in its address is taken out of the address and asked.
document.addEventListener('DOMContentLoaded', () => {
  restoreChat();
  // A panel left open stays open through a reload in the same tab.
  try { chatOpen = sessionStorage.getItem(SAVED_OPEN) === '1'; } catch { /* blocked */ }
  const geo = routeGeo();
  if (geo) {
    // An address like /district/house-07 is shown the usual way, /district/house-7.
    if (location.pathname !== districtPath(geo)) setAddress(districtPath(geo) + location.search + location.hash, true);
    choose(geo, districtName(geo), true, false);
  } else if (location.pathname.startsWith('/district/')) {
    setAddress('/', true);
    setView('home');
    $('find-status').textContent = 'That link names a district that does not exist. Hawaiʻi has 51 House and 25 Senate districts.';
  } else if (location.pathname === '/compare') {
    route(true);
  } else {
    setView('home');
    if (location.pathname === '/ask') {
      const handed = /^#q=(.+)$/.exec(location.hash);
      let question = '';
      if (handed) { try { question = decodeURIComponent(handed[1]).trim().slice(0, 2000); } catch { /* not a question */ } }
      setAddress('/', true);
      openChat(!question);
      if (question) askAgain(question);
    }
  }
});

let sentUnfocused = false;
$('question-form').onsubmit = async e => {
  e.preventDefault();
  if (busy) return;
  sentUnfocused = !document.activeElement || document.activeElement === document.body;
  const question = $('question').value.trim();
  if (!question) return;
  // The card stays compact until the first question, then grows to hold the conversation.
  const card = $('chat-card');
  if (!card.classList.contains('started')) { card.classList.add('started'); showChat(); }
  setBusy(true);
  asked = district;
  // With no district chosen, the question names its own place or asks about the whole state.
  const geo = district, name = geo ? districtLabel : '';
  askedQuestions.add(sameQuestion(question, name));
  // The conversation is not a live region, since answers are long. A short status line says what is happening,
  // and the new answer's heading takes focus.
  // Each exchange keeps the color of the chamber it was about, even after the chat switches district.
  const chamber = geo ? geo.split(':')[0] : '';
  const sent = sentLine(question, chamber), wait = waiting(name), pending = wait.box;
  pending.dataset.chamber = chamber;
  $('log').append(sent, pending);
  const earlier = earlierExchanges(), saved = {geo, name, question};
  // On Compare, the two districts go along, so "these two districts" means them.
  const pair = view() === 'compare' && window.klearCompare ? window.klearCompare.geos() : [];
  exchanges.push(saved);
  saveChat();
  reveal(sent);
  $('question').value = '';
  fitQuestion();
  $('chat-status').textContent = '';
  $('chat-live').textContent = name ? `Checking district data and sources for ${name}…` : 'Checking district data and sources…';
  let follow = true, hold = null, reply = null, done, reader = null, adopt = null, next = null;
  try {
    const answer = await askKlear(question, geo, earlier, wait, pair);
    reader = readerPlace();
    hold = holdPage(pending);
    follow = onScreen(pending);
    reply = renderAnswer(answer, name);
    const about = answerAbout(answer, geo, name), compared = comparedNames(answer);
    if (about.geo) reply.dataset.chamber = about.geo.split(':')[0];
    showMode(reply, answer);
    noteFallback(answer);
    // Only the newest answer offers next questions, and an earlier failure's Retry goes once this one is answered.
    for (const n of $('log').querySelectorAll('.followups, .retry, .switch-offer, .answer-choices')) n.remove();
    const choices = Array.isArray(answer.choices) ? choicesBox(answer.choices, question) : null;
    if (choices) reply.append(choices);
    // An answer about one district, asked with none chosen, chooses it so its profile loads. A comparison is about
    // every place it names, so it neither chooses nor offers to switch to one of them.
    if (compared) { /* its next questions name both places */ }
    else if (!geo && about.geo) adopt = about;
    else if (about.geo !== geo) reply.append(switchOffer(about));
    next = compared ? followUpGroup(pairFollowUps(question, compared), null)
      : about.geo ? followUpGroup(followUps(question, answer, about.name, counties.get(about.geo)), about) : null;
    if (next && !adopt) reply.append(next);
    pending.replaceWith(reply);
    suggestOpen = false;
    foldSuggestions();
    saved.answer = answer;
    const shown = answerPlace(answer, name);
    done = [shown ? `Answer ready for ${shown}.` : 'Answer ready.', answer.verified && answer.verified.text].filter(Boolean).join(' ');
  } catch (err) {
    reader = reader || readerPlace();
    hold = holdPage(pending);
    follow = onScreen(pending);
    for (const n of $('log').querySelectorAll('.retry')) n.remove();
    // A reply the page could not lay out reads like any other unreadable reply.
    const shown = 'retry' in err ? err : problem('KLEAR sent a reply it could not read. Press Retry to ask again.');
    pending.replaceWith(errorNote(shown, question));
    done = shown.message;
    if (!$('question').value.trim()) { $('question').value = question; fitQuestion(); $('chat-status').textContent = 'Your question is ready to retry.'; }
  } finally {
    wait.stop();
    setBusy(false);
    saveChat();
    const {elsewhere, inChat} = reader || readerPlace();
    $('chat-live').textContent = done;
    // A reader who moved to the Profile tab while waiting sees that the answer has come.
    if (reply && chatFloats() && !chatOpen) $('fab-new').hidden = false;
    let adopted = false;
    // A switch queued from Compare changes only whom the chat asks about; one from a district's address opens it.
    if (queued) { const next = queued; queued = null; if (next.compare) setChatDistrict(next.geo, next.name, true); else choose(next.geo, next.name, true, next.push); }
    // Choosing the district drops next questions that name another, so they are added once it is chosen.
    // The chosen district counts as asked about, here and in the saved conversation, like one picked first.
    else if (adopt && !district) {
      choose(adopt.geo, adopt.name, true);
      adopted = true;
      asked = adopt.geo;
      sent.dataset.chamber = adopt.geo.split(':')[0];
      Object.assign(saved, {geo: adopt.geo, name: adopt.name});
      saveChat();
      if (next) reply.append(next);
    }
    // The district's page replaces the home page, so a reader still in the chat moves to the top of it, or to its
    // chat on one column, rather than staying at the home page's scroll position.
    if (adopted && inChat) arrive('ask');
    else if (hold) hold();
    if (follow && (logScrolls() || (!elsewhere && !(hold && hold.field)))) reveal(sent);
    // A reader still in the chat moves to the new answer; after an error, back to the question to retry.
    if (inChat && reply) reply.querySelector('h3').focus({preventScroll: true});
    else if (inChat) $('question').focus({preventScroll: true});
  }
};
// Where the reader is when an answer arrives, read before the answer changes the page: working in the district search
// or another field (leave the page and focus alone), or still in the chat (focus in the chat card or its suggestions,
// or on nothing while the question box shows).
function readerPlace() {
  const focus = document.activeElement;
  const elsewhere = Boolean(focus && focus !== $('question') && focus.matches('input, select, textarea'));
  const idle = !focus || focus === document.body;
  // Safari does not focus a button it taps, so a question sent that way leaves focus on the page: it still counts as
  // sent from the chat, even when the reply has pushed the question box off the screen.
  const inChat = !elsewhere && ($('chat-card').contains(focus) || $('starters').contains(focus) || (idle && (sentUnfocused || onScreen($('question-form')))));
  return {elsewhere, inChat};
}

// The last announcement is cleared once the reader starts the next question, so it is not read again.
$('question').addEventListener('input', () => { if (!busy) $('chat-live').textContent = ''; });

// Ctrl+Enter, or Command+Enter on a Mac, sends the question. Enter alone starts a new line.
$('question').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); $('question-form').requestSubmit(); }
});
if (/Mac|iPhone|iPad/.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform)) $('send-hint').textContent = '⌘+Enter sends.';

// Ask again with a question already written out: a follow-up, a district example, or a retry.
function askAgain(question) {
  if (busy) return;
  $('question').value = question;
  fitQuestion();
  $('question-form').requestSubmit();
}

// The waiting line: elapsed seconds, a Cancel button, and a time limit. The status line notes every 15 seconds that
// the answer is still coming, since the waiting line itself is not a live region.
const ASK_LIMIT = 45;
function waiting(name) {
  const box = node('div', '', 'pending'), text = node('p', 'Checking district data and sources… '), clock = node('span', '', 'elapsed');
  const cancel = node('button', 'Cancel', 'cancel'), control = new AbortController(), started = Date.now();
  const wait = {box, signal: control.signal, why: ''};
  const stop = why => { if (!wait.why) { wait.why = why; control.abort(why); } };
  cancel.type = 'button';
  cancel.onclick = () => stop('cancel');
  text.append(clock);
  const dots = node('span', '', 'typing');
  dots.setAttribute('aria-hidden', 'true');
  dots.append(node('i', ''), node('i', ''), node('i', ''));
  box.append(dots, text, cancel);
  let note = 15;
  const tick = setInterval(() => {
    const s = Math.floor((Date.now() - started) / 1000);
    clock.textContent = s === 1 ? '1 second' : `${s} seconds`;
    const what = name ? `for ${name}` : 'district data and sources';
    if (s >= note) { $('chat-live').textContent = `Still checking ${what}: ${note} seconds so far. Press Cancel to stop.`; note += 15; }
  }, 1000);
  const limit = setTimeout(() => stop('timeout'), ASK_LIMIT * 1000);
  wait.stop = () => { clearInterval(tick); clearTimeout(limit); };
  return wait;
}

// An error that says what to do next, and whether pressing Retry could help.
const problem = (message, retry = true) => Object.assign(new Error(message), {retry});
function statusProblem(status) {
  if (status === 429) return problem('KLEAR is getting too many questions right now. Wait a minute, then press Retry.');
  if (status === 413) return problem('That question is too long for KLEAR to receive. Shorten it, then ask again.', false);
  if ([502, 503, 504].includes(status)) return problem(`KLEAR’s server is not responding right now (error ${status}). Wait a moment, then press Retry.`);
  if (status >= 500) return problem(`KLEAR ran into a server problem (error ${status}). Wait a moment, then press Retry.`);
  if (status >= 400) return problem(`KLEAR could not take this question (error ${status}). Reload the page, then ask again.`, false);
  return problem('KLEAR sent a reply it could not read. Press Retry to ask again.');
}

// Send a question. Replies that are not JSON, such as a proxy's error page, get a plain message instead of a parse error.
// The last answered questions in this tab go with the next one, so a follow-up such as "how does that compare with
// the county?" keeps its subject: each question, the place its answer was about and the measures it showed. Never
// answer text, which the server could not check.
const EARLIER = 2, EARLIER_QUESTION = 500, EARLIER_IDS = 40;
function earlierExchanges() {
  return exchanges.filter(x => x.answer && x.answer.subject).slice(-EARLIER).map(x => {
    const s = x.answer.subject;
    return {
      question: Array.from(x.question).slice(0, EARLIER_QUESTION).join(''),
      district: typeof s.geo === 'string' ? s.geo : null,
      indicators: (Array.isArray(s.indicators) ? s.indicators : []).filter(i => typeof i === 'string').slice(0, EARLIER_IDS),
    };
  });
}

async function askKlear(question, geo, earlier, wait, pair = []) {
  const stopped = () => problem(wait.why === 'timeout'
    ? `KLEAR did not answer within ${ASK_LIMIT} seconds, so the question was stopped. Press Retry to ask again.`
    : 'You cancelled this question. Press Retry to ask it again.');
  let r, data = null;
  try {
    r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question, district: geo, ...(earlier.length ? {earlier} : {}), ...(pair.length === 2 ? {compare: pair} : {})}), signal: wait.signal});
  } catch {
    throw wait.why ? stopped() : problem('KLEAR could not be reached. Check your internet connection, then press Retry.');
  }
  try { data = await r.json(); } catch { if (wait.why) throw stopped(); }
  if (r.ok && data && typeof data === 'object') return data;
  if (data && typeof data.error === 'string') throw problem(data.error, r.status >= 500 || r.status === 429);
  throw statusProblem(r.ok ? 200 : r.status);
}

function errorNote(err, question) {
  const box = node('div', '', 'error');
  box.append(node('p', err.message));
  if (err.retry) {
    const retry = node('button', 'Retry', 'retry');
    retry.type = 'button';
    retry.onclick = () => askAgain(question);
    box.append(retry);
  }
  return box;
}

// A small badge after the answer heading that says who answered: the assistant just now, the assistant ahead of time
// (a prepared answer, served with no new model call), or the basic lookup, which uses no model.
const modeNames = {assistant: ['Assistant', 'Written by the KLEAR assistant from the data store'], prepared: ['Prepared answer', 'Written ahead of time by the KLEAR assistant for this data update. No new AI call was made.'], basic: ['Basic lookup', 'Figures matched by keyword in the data store, without the assistant']};
function showMode(article, answer) {
  const mode = answer.prepared ? 'prepared' : answer.mode;
  if (!modeNames[mode]) return;
  const [label, about] = modeNames[mode], h = article.querySelector('h3'), head = node('div', '', 'answer-head'), badge = node('p', '', 'mode-badge');
  badge.title = about;
  badge.dataset.mode = mode;
  badge.append(hidden('Answer type: '), label);
  h.replaceWith(head);
  head.append(h, badge);
}

// The places a comparison answer sets side by side, by name, or null for an answer about one place.
function comparedNames(answer) {
  const list = Array.isArray(answer.compared) ? answer.compared.filter(c => c && typeof c.name === 'string' && c.name) : [];
  return list.length > 1 ? list.map(c => c.name) : null;
}

// Next questions after a comparison: the same places on related measures, each naming both, so the basic lookup
// keeps both too.
function pairFollowUps(question, names) {
  const out = [], [a, ...rest] = names, others = rest.join(' and ');
  const t = topicOf(question);
  for (const key of [...(t ? t.next : []), 'poverty', 'housing', 'income', 'kupuna', 'health']) {
    const c = topic(key);
    if (!c || !c.compare || (t && key === t.key)) continue;
    const q = `Compare ${c.compare} in ${a} and ${others}.`;
    if (!askedQuestions.has(sameQuestion(q, '')) && !out.includes(q)) out.push(q);
  }
  return out.slice(0, 3);
}

// Links an answer offers as the next step: the comparison on the Compare page, or a measure on the Rankings page.
// Both are pages of this site, so they open in place; Compare opens without leaving the page.
function answerActions(actions) {
  const list = (Array.isArray(actions) ? actions : []).filter(a => a && typeof a.text === 'string' && typeof a.url === 'string' && /^\/(?!\/)/.test(a.url));
  const shown = list.filter(a => !(view() === 'compare' && a.url.startsWith('/compare?') && window.klearCompare && new URL(a.url, location.href).searchParams.get('d') === window.klearCompare.geos().join(',')));
  if (!shown.length) return null;
  const box = node('p', '', 'answer-actions');
  for (const a of shown) {
    const l = node('a', a.text, 'answer-action');
    l.href = a.url;
    if (a.url.startsWith('/compare')) {
      l.addEventListener('click', e => {
        if (!plainClick(e)) return;
        e.preventDefault();
        const d = new URL(a.url, location.href).searchParams.get('d');
        if (chatFloats()) closeChat(false);
        openCompare(d ? d.split(',') : []);
      });
    }
    box.append(l);
  }
  return box;
}

// When the paid assistant falls back to the basic lookup, the notes under the question box say so, until it answers
// again. A prepared answer says nothing either way, since it needed no model.
function noteFallback(answer) {
  const was = capability.fellBack;
  if (answer.mode === 'basic' && ['capped', 'failed'].includes(answer.fallback)) capability.fellBack = answer.fallback;
  else if (answer.mode === 'assistant' && !answer.prepared) capability.fellBack = '';
  if (capability.fellBack !== was) showCapability();
}

// Topics for next questions, matched in the order the basic lookup matches them, so every question written here is
// one the basic lookup answers too. Each question names its district, so it stands on its own.
const TOPICS = [
  {key: 'programs', match: /program|benefit|assistance|support|help available/, ask: d => `What support programs could residents of ${d} explore?`, next: ['poverty', 'housing']},
  {key: 'profile', match: /\b(profile|socioeconomic|overview)/, ask: d => `Give me a socioeconomic profile of ${d}.`, next: ['kupuna', 'housing', 'programs']},
  {key: 'industries', match: /\b(industr|tourism|tourist|hotel|construction|public administration|government job)/, compare: 'the largest industries', ask: d => `What industries do residents of ${d} work in?`, more: d => `What kinds of jobs do residents of ${d} have?`, next: ['education', 'income']},
  {key: 'jobs', match: /\b(occupation|kinds? of jobs?|types? of jobs?|what jobs|which jobs)/, ask: d => `What kinds of jobs do residents of ${d} have?`, next: ['industries', 'education']},
  {key: 'education', match: /\b(degree|college|educat|diploma|high school|graduat)/, compare: 'college graduates', ask: d => `What share of adults in ${d} have a college degree?`, next: ['industries', 'income']},
  {key: 'kupuna', match: /\b(kūpuna|kupuna|senior|older adult|65)/, compare: 'kūpuna', ask: d => `How many kūpuna live in ${d}?`, more: d => `How many kūpuna in ${d} live below the poverty level?`, next: ['health', 'programs']},
  {key: 'poverty', match: /\b(poverty|low income)/, compare: 'poverty', ask: d => `How many people in ${d} live below the poverty level?`, more: d => `How many keiki in ${d} live below the poverty level?`, next: ['income', 'programs']},
  {key: 'housing', match: /\b(housing|rent|cost burden)/, compare: 'housing costs', ask: d => `What does the data say about housing needs in ${d}?`, more: d => `How many renters in ${d} pay 35% or more of their income on rent?`, next: ['income', 'programs']},
  {key: 'income', match: /\b(income|wage|earn)/, compare: 'household income', ask: d => `What is the median household income in ${d}?`, more: d => `What is the per capita income in ${d}?`, next: ['poverty', 'housing']},
  {key: 'work', match: /\b(unemploy|jobs|work)/, compare: 'unemployment', ask: d => `How many people in ${d} are unemployed?`, more: d => `How many people in ${d} work from home?`, next: ['industries', 'income']},
  {key: 'health', match: /\b(health|uninsured|disabil)/, compare: 'health insurance coverage', ask: d => `How many people in ${d} have no health insurance?`, more: d => `How many people in ${d} have a disability?`, next: ['kupuna', 'programs']},
  {key: 'keiki', match: /\b(child|keiki|young)/, compare: 'keiki', ask: d => `How many keiki live in ${d}?`, more: d => `How many households in ${d} have children?`, next: ['poverty', 'programs']},
  {key: 'people', match: /\b(population|people|residents)/, ask: d => `How many people live in ${d}?`, next: ['kupuna', 'keiki', 'income']},
];
const topic = key => TOPICS.find(t => t.key === key);
const topicOf = text => TOPICS.find(t => t.match.test(text.toLowerCase()));
const compareQuestion = (what, name, county) => `Compare ${what} in ${name} with ${county || 'its county'} and the state.`;
// Questions already asked, written the same way whether they said "this district" or named it.
const askedQuestions = new Set();
const sameQuestion = (q, name) => q.trim().toLowerCase().replaceAll('this district', name.toLowerCase()).replace(/\s+/g, ' ');
// Main county of each district whose profile has loaded.
const counties = new Map();

// Up to three next questions: the same measure against the county and state, a closer look, then related topics.
function followUps(question, answer, name, county) {
  const out = [], add = q => { if (!askedQuestions.has(sameQuestion(q, name)) && !out.includes(q)) out.push(q); };
  const declined = /^decline/.test(answer.refusal || '');
  const words = [...(answer.lead || []), ...(answer.tiles || []).map(t => t.label), ...(answer.charts || []).map(c => c.label), ...(answer.facts || []).map(f => f.label)].join(' ');
  const t = declined ? null : topicOf(question) || topicOf(words);
  if (t && t.compare && !/county/i.test(question)) add(compareQuestion(t.compare, name, county));
  if (t && t.more) add(t.more(name));
  for (const key of [...(t ? t.next : []), 'profile', 'kupuna', 'housing', 'poverty', 'programs']) add(topic(key).ask(name));
  return out.slice(0, 3);
}

// The place an answer's figures are for: the district it chose, else its subject, such as Hawaiʻi for a statewide
// question asked with a district chosen, else the district asked about.
function answerPlace(answer, name) {
  const named = x => x && typeof x.name === 'string' && x.name, compared = comparedNames(answer);
  return (compared && compared.join(' and ')) || named(answer.district) || named(answer.subject) || name || '';
}

// The district an answer is about: the one the server names when it differs from the selected one, as for a
// street address in another district, else the selected one.
function answerAbout(answer, geo, name) {
  const d = answer.district;
  return d && typeof d.geo === 'string' && typeof d.name === 'string' ? d : {geo, name};
}

// A button that moves the chat and profile to the district an answer turned out to be about.
function switchOffer(about) {
  const box = node('p', '', 'switch-offer'), b = node('button', `Switch to ${about.name}`, 'followup');
  b.type = 'button';
  b.onclick = () => { if (!busy) choose(about.geo, about.name); };
  box.append(b);
  return box;
}

// District buttons for a question whose place spans several districts, as the search shows them. Picking one chooses
// it and asks the question again.
function choicesBox(matches, question) {
  const groups = matchGroups(matches);
  if (!groups.length) return null;
  const box = node('div', '', 'answer-choices');
  showMatches(groups, box, (geo, name) => { if (busy) return; choose(geo, name, true); askAgain(question); });
  return box;
}

let followSeq = 0;
// Next questions; for an answer about another district, a question switches the chat there before it is asked.
function followUpGroup(questions, about) {
  if (!questions.length) return null;
  const box = node('div', '', 'followups'), title = node('p', 'Ask next', 'followups-title');
  title.id = `followups-${++followSeq}`;
  box.setAttribute('role', 'group');
  box.setAttribute('aria-labelledby', title.id);
  box.append(title);
  for (const q of questions) {
    const b = says('button', q, 'followup');
    b.type = 'button';
    b.onclick = () => {
      if (busy) return;
      if (about && about.geo !== district) choose(about.geo, about.name, true);
      askAgain(q);
    };
    box.append(b);
  }
  return box;
}

// Measures that make a district example when the district's rate stands well above the state's or its county's.
const STANDOUTS = [['pct_65_plus', 'Kūpuna share', 'kūpuna'], ['pct_people_poverty', 'Poverty', 'poverty'], ['pct_children_poverty', 'Keiki poverty', 'keiki poverty'], ['pct_65_plus_poverty', 'Kūpuna poverty', 'poverty among kūpuna'], ['pct_rent_burden_35_plus', 'Rent burden', 'rent burden'], ['pct_uninsured', 'Uninsured rate', 'health insurance coverage'], ['pct_disability', 'Disability rate', 'disability']];
// Places burned in the August 2023 Maui wildfires, whose survey figures mostly predate the fires.
const WILDFIRE_PLACES = ['Lahaina', 'Kula'];
// How many times the other place's rate the district's is, to one decimal place, or 0 when the district's is not
// higher beyond the margins of error.
function timesAbove(r, a) {
  if (!a || a.direction !== 'higher' || !isNumber(r.num) || !(a.num > 0)) return 0;
  return Math.round((r.num / a.num) * 10) / 10;
}

// Examples that fit the chosen district, from its profile: a wildfire note where the survey years matter, and the
// measure that stands out most against the state, or else the county. They follow the prepared questions.
function districtExamples(p) {
  counties.set(p.geo, p.county);
  for (const b of $('examples').querySelectorAll('.local')) b.remove();
  const statewide = $('examples').querySelector('.statewide'), across = statewideFor(String(p.geo || '').split(':')[0]);
  if (statewide && statewide.dataset.question !== across) {
    statewide.replaceChildren(...says('span', across).childNodes);
    statewide.dataset.question = across;
  }
  const rows = new Map(p.sections.flatMap(s => s.rows).map(r => [r.id, r]));
  let best = null;
  for (const [id, label, what] of STANDOUTS) {
    const r = rows.get(id);
    if (!r || !r.against.length) continue;
    const state = timesAbove(r, r.against.at(-1)), county = r.against.length > 1 ? timesAbove(r, r.against[0]) : 0;
    const score = state >= 1.2 ? 10 + state : county >= 1.2 ? county : 0;
    if (score && (!best || score > best.score)) best = {score, label: `${label} higher than ${state >= 1.2 ? 'statewide' : 'countywide'}`, what};
  }
  const questions = [];
  if (p.places.some(x => WILDFIRE_PLACES.includes(x.name))) questions.push(`Which survey years do the housing figures for ${p.district} cover, given the 2023 Maui wildfires?`);
  if (best) questions.push(compareQuestion(best.what, p.district, p.county));
  statewide.before(...questions.map(question => {
    const b = says('button', question, 'local');
    b.type = 'button';
    if (busy) b.setAttribute('aria-disabled', 'true');
    b.onclick = () => askAgain(question);
    return b;
  }));
  foldSuggestions();
}

// Next questions, retries and district examples name a district, so they go when the chat switches to another.
function dropDistrictActions() {
  for (const n of document.querySelectorAll('#log .followups, #log .retry, #log .switch-offer, #examples .local')) n.remove();
}
document.addEventListener('klear:district', dropDistrictActions);

// Answer heading that names the district the question was asked about. It takes focus when the answer arrives.
function heading(answer, name) {
  const h = node('h3', refusalLabels[answer.refusal] || 'KLEAR');
  h.tabIndex = -1;
  if (name) h.append(hidden(', '), node('span', name, 'answer-for'));
  return h;
}

// A labelled figure for the basic lookup, with its source.
function factCard(f) {
  const card = node('div', '', 'fact');
  const uncertainty = uncertaintyLabels[f.uncertainty] || uncertaintyLabels.unavailable;
  const value = node('p', f.value);
  if (f.caution) value.append(' ', cautionChip(f.caution));
  card.append(says('b', f.label), value, node('small', `${f.year}, ${f.geography}. Uncertainty: ${uncertainty}`), link(f.source, f.url));
  if (f.links && f.links.length) card.append(sourceLinks(f.links));
  return card;
}

// A figure card with what its label means at the end.
function explainedCard(f) {
  const card = factCard(f), about = meaning(f.label, f.meaning);
  if (about) card.append(about);
  return card;
}

const answerSentences = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('en', {granularity: 'sentence'}) : null;
function answerLine(tag, text, opening) {
  const className = tag === 'p' ? 'lead' : undefined;
  if (!opening) return says(tag, text, className);
  const first = (answerSentences?.segment(text).containing(0)?.segment ?? text.match(/^.*?[.!?](?=\s|$)/s)?.[0] ?? text).trimEnd();
  const line = node(tag, '', className);
  line.append(says('strong', first, 'answer-opening'), ...says('span', text.slice(first.length)).childNodes);
  return line;
}

function basicAnswer(answer, name) {
  const article = document.createElement('article');
  article.append(heading(answer, name));
  let opening = true;
  for (const p of answer.text.split('\n\n')) {
    if (!p.trim()) continue;
    const lines = p.split('\n'), items = lines.slice(1).map(l => l.match(/^\s*[*-]\s+(.*)/));
    if (lines.length > 1 && items.every(Boolean)) {
      article.append(answerLine('p', lines[0], opening));
      const list = node('ul', '', 'lead-list');
      list.append(...items.map(m => says('li', m[1])));
      article.append(list);
    } else article.append(answerLine('p', p, opening));
    opening = false;
  }
  const actions = answerActions(answer.actions);
  if (actions) article.append(actions);
  if ((answer.members || []).length) article.append(memberLinks(answer.members));
  if (answer.elsewhere) article.append(elsewhereLine(answer.elsewhere));
  if (answer.verified) article.append(node('p', answer.verified.text, 'verified'));
  if (answer.facts.length) {
    const details = document.createElement('details');
    details.open = true;
    details.append(node('summary', 'Figures & sources'), ...answer.facts.map(explainedCard));
    article.append(details);
  }
  if (answer.place_caveats && answer.place_caveats.length) article.append(placeNotes(answer.place_caveats, 'answer-notes'));
  if (answer.programs && answer.programs.length) article.append(answerPrograms(answer.programs, answer.program_sources, answer.program_groups));
  article.append(copyAnswer(article, answer, name));
  return article;
}

// Where to look for Hawaiʻi data KLEAR does not hold, after an answer with no figures.
function elsewhereLine(e) {
  const p = node('p', `${e.text} `, 'elsewhere');
  p.append(link(e.name, e.url), '.');
  return p;
}

// Where a source's figures can be checked: each ACS table for the place on data.census.gov, or the methods page
// for figures KLEAR moved to districts or CDC modeled. On a line of its own under the source.
function sourceLinks(links) {
  const box = node('span', '', 'source-links');
  links.forEach((l, i) => box.append(i ? ', ' : '', link(l.text, l.url)));
  return box;
}

// A source named and linked to its publisher's page, with its years and where its figures can be checked.
function citation(s) {
  const li = document.createElement('li');
  li.append(link(s.name, s.url), `, ${s.year}`);
  if (s.links && s.links.length) li.append(sourceLinks(s.links));
  if (s.match) li.append(matchLine(s.match));
  return li;
}

// Where another publisher prints the same figures, such as DBEDT's district profiles, linked for each place.
function matchLine(m) {
  const box = node('span', `${m.text}: `, 'source-links');
  m.places.forEach((p, i) => box.append(i ? ', ' : '', link(p.text, p.url)));
  return box;
}

// Bars for sighted readers, with a sentence that says what they show and the same figures as a table.
// The bars repeat the table, so screen readers skip them.
function chartFigure(c) {
  const fig = node('figure', '', 'chart');
  if (c.bars.some(b => b.text.length > 22)) fig.classList.add('wide');
  const head = node('figcaption', '', 'chart-head');
  head.append(says('span', c.label));
  if (c.tag) head.append(node('span', c.tag, c.lower ? 'chip strong' : 'chip'));
  fig.append(head);
  const about = meaning(c.label, c.meaning);
  if (about) fig.append(about);
  if (c.summary) fig.append(says('p', c.summary, 'chart-summary'));
  const bars = node('div', '', 'chart-bars');
  bars.setAttribute('aria-hidden', 'true');
  const max = Math.max(...c.bars.map(b => b.high)) || 1;
  c.bars.forEach((b, i) => {
    const row = node('div', '', 'bar-row');
    // The district asked about is first, darker, and named in bold, so the bars do not rely on color alone.
    row.append(node('span', b.geography, i ? 'bar-place' : 'bar-place home'));
    const track = node('div', '', 'bar-track');
    const bar = node('div', '', i ? 'bar other' : 'bar home');
    bar.style.width = (b.value / max * 100) + '%';
    bar.title = `${b.geography}: ${b.text}`;
    track.append(bar);
    if (b.high > b.low) {
      const whisker = node('div', '', 'whisker');
      whisker.style.left = (b.low / max * 100) + '%';
      whisker.style.width = ((b.high - b.low) / max * 100) + '%';
      track.append(whisker);
    }
    const [num, ...spread] = b.text.split(' '), value = node('span', num, 'bar-value');
    if (spread.length) value.append(' ', node('span', spread.join(' '), 'bar-spread'));
    if (b.caution) value.append(cautionChip(b.caution));
    row.append(track, value);
    bars.append(row);
  });
  fig.append(bars);
  if (c.note) fig.append(node('p', c.note, 'chart-note'));
  if (c.method) fig.append(methodLine(c.method));
  fig.append(chartTable(c));
  return fig;
}

// The chart's figures as a table, collapsed under the bars.
function chartTable(c) {
  const details = node('details', '', 'chart-data');
  details.append(node('summary', 'Show the data table'));
  const spreads = c.bars.map(b => b.spread || '').filter(Boolean);
  const ranges = spreads.filter(s => s.startsWith('range')).length;
  const spreadHead = !spreads.length ? '' : !ranges ? '90% margin of error' : ranges === spreads.length ? 'Range' : 'Margin of error or range';
  const table = document.createElement('table'), caption = says('caption', c.label), head = document.createElement('tr');
  for (const h of ['Place', 'Estimate', spreadHead].filter(Boolean)) { const th = node('th', h); th.scope = 'col'; head.append(th); }
  const body = document.createElement('tbody');
  for (const b of c.bars) {
    const row = document.createElement('tr'), place = node('th', b.geography);
    place.scope = 'row';
    const estimate = node('td', b.number ?? b.text);
    if (b.caution) estimate.append(' ', cautionChip(b.caution));
    row.append(place, estimate);
    if (spreadHead) row.append(node('td', b.spread || 'None published'));
    body.append(row);
  }
  table.append(caption, node('thead', ''), body);
  table.tHead.append(head);
  const scroll = node('div', '', 'table-scroll');
  scroll.append(table);
  details.append(scroll);
  return details;
}

// The same measure in the other places the answer compares, listed under the tile's figure.
function tilePlaces(places) {
  const list = node('ul', '', 'tile-places');
  for (const p of places) {
    const item = node('li', '', 'tile-place');
    const value = node('span', p.value, 'tile-place-value');
    if (p.spread) value.append(node('span', p.spread, 'tile-place-spread'));
    item.append(node('span', p.geography, 'tile-place-name'), value);
    if (p.caution) item.append(cautionChip(p.caution));
    list.append(item);
  }
  return list;
}

// Figures past the tiles, as a table collapsed under them, so the answer never drops one. Rows are grouped by
// place, and each estimate has its margin or range under it, which keeps the table narrow enough for a phone.
function moreFigures(rows) {
  const details = node('details', '', 'chart-data more-figures');
  details.append(node('summary', rows.length === 1 ? 'Show 1 more figure' : `Show ${rows.length} more figures`));
  const places = new Map();
  for (const r of rows) places.set(r.geography, [...(places.get(r.geography) || []), r]);
  const only = places.size === 1 ? rows[0].geography : '';
  const table = document.createElement('table'), head = document.createElement('tr');
  for (const h of ['Measure', 'Estimate']) { const th = node('th', h); th.scope = 'col'; head.append(th); }
  table.append(node('caption', only ? `More figures for ${only}` : 'More figures'), node('thead', ''));
  table.tHead.append(head);
  for (const [place, group] of places) {
    const body = document.createElement('tbody');
    if (!only) {
      const row = document.createElement('tr'), th = node('th', place, 'more-place');
      th.colSpan = 2;
      th.scope = 'colgroup';
      row.append(th);
      body.append(row);
    }
    for (const r of group) {
      const row = document.createElement('tr'), label = says('th', r.label);
      label.scope = 'row';
      const estimate = node('td', r.value);
      if (r.spread) estimate.append(node('span', r.spread, 'more-spread'));
      if (r.caution) estimate.append(' ', cautionChip(r.caution));
      row.append(label, estimate);
      body.append(row);
    }
    table.append(body);
  }
  const scroll = node('div', '', 'table-scroll');
  scroll.append(table);
  details.append(scroll);
  return details;
}

const ruler = document.createElement('canvas').getContext('2d');
function ems(text, weight) { ruler.font = `${weight} 100px "Public Sans", system-ui, sans-serif`; return ruler.measureText(text).width / 100; }

// Big tile number after a small 'about' for estimates. --ems and --about are their widths in em, so the CSS can shrink long numbers to fit.
function tileValue(text) {
  const [, about, figure] = text.match(/^(about )?([\s\S]*)$/), value = node('div', '', 'tile-value'), num = node('span', figure, 'tile-num');
  if (about) {
    value.append(node('span', 'about ', 'tile-about'));
    value.style.setProperty('--about', ems('about ', 600).toFixed(3));
  }
  value.append(num);
  if (/\d/.test(figure)) num.style.setProperty('--ems', (ems(figure, 400) - 0.02 * figure.length).toFixed(3));
  else value.classList.add('none');
  return value;
}

function renderAnswer(answer, name) {
  name = answerPlace(answer, name);
  if (!answer.lead) return basicAnswer(answer, name);
  const article = document.createElement('article');
  article.className = 'answer';
  article.append(heading(answer, name));
  let list = null, opening = true;
  for (const line of answer.lead.flatMap(p => p.split('\n'))) {
    const item = line.match(/^\s*[*-]\s+(.*)/);
    if (item) {
      if (!list) article.append(list = node('ul', '', 'lead-list'));
      list.append(answerLine('li', item[1], opening));
    } else {
      list = null;
      if (!line.trim()) continue;
      article.append(answerLine('p', line, opening));
    }
    opening = false;
  }
  const actions = answerActions(answer.actions);
  if (actions) article.append(actions);
  if (answer.tiles.length) {
    const grid = node('div', '', 'tiles');
    for (const t of answer.tiles) {
      const tile = node('div', '', 'tile');
      tile.append(tileValue(t.value), says('div', t.label, 'tile-label'));
      const meta = [t.geography, t.spread, t.share].filter(Boolean).join(', ');
      if (meta) tile.append(node('div', meta, 'tile-meta'));
      if (t.caution) { const flag = node('div', '', 'tile-caution'); flag.append(cautionChip(t.caution)); tile.append(flag); }
      if (t.places && t.places.length) tile.append(tilePlaces(t.places));
      if (t.method) tile.append(methodLine(t.method));
      const about = meaning(t.label, t.meaning);
      if (about) tile.append(about);
      grid.append(tile);
    }
    article.append(grid);
  }
  if (answer.more && answer.more.length) article.append(moreFigures(answer.more));
  for (const c of answer.charts) article.append(chartFigure(c));
  for (const c of answer.caveats) article.append(says('p', c, 'caveat'));
  if ((answer.members || []).length) article.append(memberLinks(answer.members));
  if (answer.place_caveats && answer.place_caveats.length) article.append(placeNotes(answer.place_caveats, 'answer-notes'));
  if (answer.elsewhere) article.append(elsewhereLine(answer.elsewhere));
  if (answer.sources.length) {
    const years = [...new Set(answer.sources.map(s => s.year))].join(', ');
    const figures = [...answer.tiles, ...answer.tiles.flatMap(t => t.places || []), ...(answer.more || [])];
    const shown = [...figures.map(t => t.spread), ...answer.charts.flatMap(c => c.bars.map(b => b.text))].join(' ');
    const notes = [`${years}.`];
    if (shown.includes('±')) notes.push('± is the 90% margin of error.');
    if (shown.includes('range ')) notes.push('A range shows the low and high bounds of the estimate.');
    if (figures.some(t => t.caution) || answer.charts.some(c => c.bars.some(b => b.caution))) notes.push('Use with caution marks an estimate with a large margin of error.');
    article.append(node('p', notes.join(' '), 'source-line'));
    if (answer.verified) article.append(node('p', answer.verified.text, 'verified'));
    const details = document.createElement('details');
    details.append(node('summary', 'Sources'));
    const list = document.createElement('ul');
    list.append(...answer.sources.map(citation));
    details.append(list);
    article.append(details);
  }
  if (answer.programs && answer.programs.length) article.append(answerPrograms(answer.programs, answer.program_sources, answer.program_groups));
  if (answer.prepared) article.append(node('p', answer.prepared, 'prepared'));
  article.append(copyAnswer(article, answer, name));
  return article;
}

// A button under an answer that copies it as plain text for an email or a memo: the question, the answer, each figure
// with its margin, each chart's sentence, the notes, and the sources with their addresses.
function copyAnswer(article, answer, name) {
  const box = node('p', '', 'copy-answer'), button = node('button', 'Copy answer', 'textbutton'), status = node('span', '', 'copy-status');
  button.type = 'button';
  button.append(hidden(name ? ` for ${name}` : ''));
  button.onclick = async () => {
    const sent = article.previousElementSibling;
    const asked = sent && sent.matches('.user') ? sent.textContent.replace(/^You asked:\s*/, '') : '';
    const copied = await copyText(answerText(answer, name, asked));
    status.textContent = copied ? 'Copied.' : 'Your browser did not allow copying.';
    $('chat-live').textContent = '';
    requestAnimationFrame(() => { $('chat-live').textContent = copied ? 'Answer copied.' : 'The answer could not be copied.'; });
  };
  box.append(button, ' ', status);
  return box;
}
function answerText(answer, name, asked) {
  const out = [];
  if (asked) out.push(`Question: ${asked}`);
  out.push(`KLEAR's answer${name ? ` for ${name}` : ''}:`);
  if (answer.lead) out.push(...answer.lead.flatMap(p => p.split('\n')).filter(l => l.trim()).map(l => l.replace(/^\s*[*-]\s+/, '- ')));
  else if (answer.text) out.push(answer.text);
  const figure = t => `- ${t.label}: ${t.value}${t.spread ? ` (${t.spread})` : ''}${t.geography ? `, ${t.geography}` : ''}`;
  const figures = [...(answer.tiles || []), ...(answer.more || []), ...(answer.facts || [])].filter(t => t && t.label && t.value);
  if (figures.length) out.push('', 'Figures:', ...figures.map(figure));
  const charts = (answer.charts || []).filter(c => c.summary);
  if (charts.length) out.push('', ...charts.map(c => `- ${c.summary}`));
  for (const c of answer.caveats || []) out.push('', c);
  const actions = (Array.isArray(answer.actions) ? answer.actions : []).filter(a => a && a.text && typeof a.url === 'string');
  if (actions.length) out.push('', ...actions.map(a => `- ${a.text}: ${new URL(a.url, location.origin).href}`));
  const sources = (answer.sources || []).length ? answer.sources : (answer.facts || []).map(f => ({name: f.source, year: f.year, url: f.url})).filter(s => s.name);
  const seen = new Set(), listed = sources.filter(s => { const key = `${s.name} ${s.year}`; if (seen.has(key)) return false; seen.add(key); return true; });
  if (listed.length) out.push('', 'Sources:', ...listed.map(s => `- ${s.name}${s.year ? `, ${s.year}` : ''}${s.url ? ` ${s.url}` : ''}`));
  out.push('', `From KLEAR, ${location.origin}. Public, aggregated data; not an official State of Hawaiʻi product.`);
  return out.join('\n');
}

// The home page's last strip: the data store's counts, each number set large over its label. Without a store it stays
// hidden.
async function loadAbout() {
  let data;
  try { data = await api('/api/about'); } catch { return; }
  const stats = data.stats || [];
  if (stats.length) {
    $('facts').replaceChildren(...stats.map(s => {
      const li = node('li', '');
      li.append(node('strong', s.value, 'stat-num'), ' ', node('span', s.label, 'stat-label'));
      return li;
    }));
    $('facts').hidden = false;
  }
}
loadAbout();

// Programs named in an answer; a district lookup marks which ones its figures point to.
function answerPrograms(programs, sources, groups) {
  const details = node('details', '', 'answer-programs');
  details.open = true;
  const ranked = programs.some(p => 'match' in p);
  details.append(node('summary', ranked ? 'Programs to explore here' : 'Program pages'), ...programList(programs, groups, 'Other programs', 'h4'));
  if (sources && sources.length) details.append(profileSources(sources));
  return details;
}
