// Reflow, zoom, text size, text spacing, links, and language, checked on a page with every part showing.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chatFloats, chooseDistrict, offline, openAll, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

// The page with a district chosen, its profile, an answer, and every disclosure open. Below 1200 px the chat is a
// panel over the page, so each check runs with it open, then shut.
async function fullPage(page) {
  await page.goto('/');
  await chooseDistrict(page);
  await ask(page);
  await openAll(page);
}
async function onEachTab(page, check) {
  await check();
  if (await chatFloats(page)) {
    await showTab(page, 'Profile');
    await check();
  }
}

async function sidewaysScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

// 320 px is a 1280 px window at 400% zoom (WCAG 1.4.10); 640 px is the same window at 200% (WCAG 1.4.4).
for (const width of [320, 640]) {
  test.describe(`${width} px wide`, () => {
    test.use({viewport: {width, height: 720}});
    test('the page fits without scrolling sideways', async ({page}) => {
      await fullPage(page);
      await expect(page.getByLabel('Your question')).toBeVisible();
      const ask = page.locator('#send');
      await expect(ask).toBeVisible();
      const box = await ask.boundingBox();
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      await onEachTab(page, async () => expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0));
      // The share buttons stay inside the screen too.
      for (const button of await page.locator('#share-bar .action').all()) {
        const b = await button.boundingBox();
        expect(b.x + b.width).toBeLessThanOrEqual(width);
      }
    });
  });
}

test('text is at least 12 px', async ({page}) => {
  await fullPage(page);
  await onEachTab(page, async () => expect(await smallText(page)).toEqual([]));
});

async function smallText(page) {
  return page.evaluate(() => {
    const out = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!n.textContent.trim() || el.closest('svg, .visually-hidden, script, style')) continue;
      if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') continue;
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < 12) out.push(`${size}px: ${n.textContent.trim().slice(0, 40)}`);
    }
    return out;
  });
}

test.describe('text spacing', () => {
  // The site's content security policy allows no inline styles, so this test lifts it to add the spacing.
  test.use({bypassCSP: true});
  test('can be increased without losing content (WCAG 1.4.12)', async ({page}) => {
  await fullPage(page);
  await page.addStyleTag({content: '* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } p { margin-bottom: 2em !important; }'});
  await onEachTab(page, async () => {
    expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
    // Buttons and labels still show all of their text.
    const clipped = await page.evaluate(() => [...document.querySelectorAll('button, label, summary, .match, .chip, .action')]
      .filter(el => el.getClientRects().length && !el.matches('.visually-hidden') && getComputedStyle(el).overflow === 'hidden' && el.scrollWidth > el.clientWidth + 1)
      .map(el => el.textContent.trim().slice(0, 40)));
    expect(clipped).toEqual([]);
  });
  });
});

test('links that leave the site say so, and in-page links have no arrow', async ({page}) => {
  await fullPage(page);
  // The home page with every part showing, then each other page, header and footer included.
  for (const address of [null, '/outreach.html', '/methods.html', '/glossary.html', '/checks.html', '/accessibility.html']) {
    if (address) await page.goto(address);
    const links = await page.locator('a[href]').evaluateAll(all => all.map(a => ({
      text: a.textContent, ext: a.classList.contains('ext'), blank: a.target === '_blank',
      // Phone (tel:) and email links open an app, not another site.
      external: /^https?:$/.test(new URL(a.href).protocol) && new URL(a.href).origin !== location.origin,
    })));
    for (const l of links) {
      expect(l.ext, `arrow only on links that leave the site: ${l.text} (${address || '/'})`).toBe(l.external);
      if (l.blank) expect(l.text).toContain('(opens in a new tab)');
      else if (l.external) expect(l.text).toContain('(external site)');
    }
  }
});

test('Hawaiian words are marked for pronunciation, place names are not', async ({page}) => {
  await page.goto('/');
  await expect(page.locator('#starters [lang=haw]').first()).toHaveText(/^kūpuna$/i);
  await chooseDistrict(page);
  await openAll(page);
  await expect(page.locator('#profile .profile-label [lang=haw]').first()).toHaveText('Kūpuna');
  const marked = await page.locator('[lang=haw]').allTextContents();
  expect(marked.every(w => /^(kūpuna|keiki|ʻohana)$/i.test(w))).toBe(true);
});

test('motion stops when the device asks for reduced motion', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto('/');
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe('auto');
  // The spinner on the line that waits for an answer holds still.
  const spinner = await page.locator('#log').evaluate(log => {
    const wait = document.createElement('div');
    wait.className = 'pending';
    log.append(wait);
    return getComputedStyle(wait, '::before').animationName;
  });
  expect(spinner).toBe('none');
});

// How many of these show whole on the screen, without scrolling or opening anything.
const onScreen = (page, selector) => page.locator(selector).evaluateAll(all => all.filter(el => {
  const r = el.getBoundingClientRect();
  return el.checkVisibility() && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight;
}).length);

// A profile the size of a real one: the fixture district has one headline figure and a few rows, so they repeat. The
// fixture district reaches into Kalawao County, which no real one beside it does, so its place note is left out: most
// districts carry none, and one that does gives up a row of figures on the first screen to it.
async function fullProfile(page) {
  await page.route('**/api/profile?*', async route => {
    const response = await route.fetch(), p = await response.json();
    p.caveats = [];
    const [first] = p.headline;
    p.headline = ['Total population', 'Median household income', first.label, 'Share who are kūpuna'].map(label => ({...first, label}));
    const people = p.sections.find(s => s.title === 'People');
    people.rows = Array.from({length: 9}, (_, i) => ({...people.rows[i % people.rows.length], id: `row_${i}`}));
    await route.fulfill({response, json: p});
  });
}

// A district's link on a laptop: the first screen shows its map, its name and its four headline figures. The chat
// beside the profile stays in view as the profile scrolls, and an answer with a chart has 600 px or more to be read in.
test.describe('a district at 1440 by 900', () => {
  test.skip(({isMobile}) => isMobile, 'a laptop screen');
  test.use({viewport: {width: 1440, height: 900}});
  test('opens at its map, name and figures, with the chat beside it', async ({page}) => {
    await fullProfile(page);
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    await expect(page.getByRole('heading', {level: 1, name: DISTRICT})).toBeInViewport({ratio: 1});
    await expect(page.locator('#district-map')).toBeInViewport({ratio: 1});
    expect(await onScreen(page, '.profile-stats dd:not(.profile-reading, .glance-strip)')).toBe(4);
    // The chat takes the right third of the page.
    const chat = await page.locator('#chat').boundingBox(), profile = await page.locator('#profile').boundingBox();
    expect(chat.x).toBeGreaterThan(profile.x + profile.width);
    expect(chat.width).toBeGreaterThan(1440 * 0.25);
    expect(chat.width).toBeLessThan(1440 * 0.36);

    await ask(page);
    const messages = page.locator('#messages');
    await expect(messages.locator('article .chart').first()).toBeVisible();
    expect((await messages.boundingBox()).height).toBeGreaterThanOrEqual(600);
    await page.mouse.move(400, 600);
    await page.mouse.wheel(0, 2000);
    await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(1000);
    await expect(page.getByLabel('Your question')).toBeInViewport();
    expect((await messages.boundingBox()).height).toBeGreaterThanOrEqual(600);
  });
});

// A district's page ends where the district ends: its profile, then the footer. The Find page's search, number list and
// counts are left out, the map beside the name is zoomed to the district, and the page stays under 7,000 px at 1280
// wide with the default groups open.
test.describe('a district at 1280 wide', () => {
  test.skip(({isMobile}) => isMobile, 'a laptop screen');
  test.use({viewport: {width: 1280, height: 800}});
  test('ends with the profile, with the map of the district by its name', async ({page}) => {
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    for (const id of ['find-col', 'seat-index', 'sources', 'hero', 'island-tabs']) await expect(page.locator(`#${id}`), id).toBeHidden();
    await expect(page.locator('#district-map')).toBeVisible();
    await expect(page.locator('#map .district.picked:not(.copy)')).toHaveAttribute('data-geo', 'house:022');
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(7000);
  });
});

// Beside the profile on a laptop, the agent is a panel as tall as the window, its question box always in view, and the
// conversation scrolls inside it. It has no district search or width toggle of its own, and the note under its
// question box is three lines at most.
test.describe('a district at 1280 by 800', () => {
  test.skip(({isMobile}) => isMobile, 'a laptop screen');
  test.use({viewport: {width: 1280, height: 800}});
  test('the agent beside the profile keeps its question box in view as the conversation grows', async ({page}) => {
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    const card = page.locator('#chat-card');
    await expect(card.getByRole('heading', {level: 2})).toHaveText(`Ask about ${DISTRICT}`);
    await expect(card.getByRole('combobox')).toHaveCount(0);
    await expect(card.getByRole('button', {name: 'Wider'})).toHaveCount(0);
    await expect(page.locator('#chat-fab')).toBeHidden();
    await expect(page.locator('#chat-min')).toBeHidden();
    expect((await card.boundingBox()).height).toBeLessThanOrEqual(800 - 64);
    await expect(page.getByLabel('Your question')).toBeInViewport();
    const note = page.locator('#chat-privacy');
    const lines = await note.evaluate(p => Math.round(p.getBoundingClientRect().height / parseFloat(getComputedStyle(p).lineHeight)));
    expect(lines).toBeLessThanOrEqual(3);

    await ask(page);
    await ask(page, 'How many kūpuna live in this district?');
    expect((await page.locator('#messages').boundingBox()).height).toBeGreaterThanOrEqual(400);
    await expect(page.getByLabel('Your question')).toBeInViewport();
    expect(await page.locator('#messages').evaluate(m => m.scrollHeight > m.clientHeight)).toBe(true);
  });
});

// On a phone, the agent is a round button at the bottom of the screen that opens it over the whole page, and the
// minimize button at its top puts it away again.
test.describe('a district at 390 by 844', () => {
  test.use({viewport: {width: 390, height: 844}});
  test('the agent is a button that opens over the page and minimizes back to it', async ({page, isMobile}) => {
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    const fab = page.locator('#chat-fab');
    await expect(fab).toBeInViewport({ratio: 1});
    await expect(fab).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByLabel('Your question')).toBeHidden();
    // The map and the district's name come first, with the way back and on above them.
    await expect(page.locator('#search-another')).toBeInViewport({ratio: 1});
    await expect(page.getByRole('heading', {level: 1, name: DISTRICT})).toBeInViewport();
    await fab.click();
    const panel = page.locator('#chat');
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(389);
    expect(box.height).toBeGreaterThanOrEqual(843);
    // The questions to try fold to the first, so the question box is on the screen.
    await expect(page.getByLabel('Your question')).toBeInViewport();
    await expect(page.getByRole('button', {name: 'More questions'})).toBeVisible();
    // On a touch screen focus goes to the panel's title, so the keyboard does not cover the panel before it is read.
    await expect(page.locator(isMobile ? '#chat-panel-title' : '#question')).toBeFocused();
    await page.locator('#chat-min').click();
    await expect(panel).toBeHidden();
    await expect(fab).toBeFocused();
    expect(await page.evaluate(() => scrollY)).toBe(0);
  });
});
