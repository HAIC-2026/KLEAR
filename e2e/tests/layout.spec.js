// Reflow, zoom, text size, text spacing, links, and language, checked on a page with every part showing.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, openAll} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

// The page with a district chosen, its profile, an answer, and every disclosure open. Below 1200 px the profile and the
// chat are two tabs, so each check runs on both: first Ask, then Profile.
async function fullPage(page) {
  await page.goto('/');
  await chooseDistrict(page);
  await ask(page);
  await openAll(page);
}
async function onEachTab(page, check) {
  await check();
  const tab = page.getByRole('tab', {name: 'Profile'});
  if (await tab.isVisible()) {
    await tab.click();
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
      const ask = page.getByRole('button', {name: 'Ask KLEAR'});
      await expect(ask).toBeVisible();
      const box = await ask.boundingBox();
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      await onEachTab(page, async () => expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0));
      // The share buttons stay inside the screen too.
      for (const button of await page.getByRole('group', {name: 'Share this profile'}).locator('.action').all()) {
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
  await expect(page.locator('#examples [lang=haw]').first()).toHaveText(/^kūpuna$/i);
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
  // A starter about this district asks for one first, with a nudge on the district bar.
  await page.locator('#examples').getByRole('button', {name: /kūpuna live in this district/}).click();
  await expect(page.locator('#district-bar')).toHaveClass(/nudge/);
  expect(await page.locator('#district-bar').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
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

// A district's link on a laptop: the first screen shows its name, its four headline figures and at least six more
// figures or programs. The chat beside the profile stays in view as the profile scrolls, and an answer with a chart has
// 600 px or more to be read in.
test.describe('a district at 1440 by 900', () => {
  test.skip(({isMobile}) => isMobile, 'a laptop screen');
  test.use({viewport: {width: 1440, height: 900}});
  test('opens at its name and figures, with the chat beside it', async ({page}) => {
    await fullProfile(page);
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    await expect(page.getByRole('heading', {level: 1, name: DISTRICT})).toBeInViewport({ratio: 1});
    expect(await onScreen(page, '.profile-stats dd')).toBe(4);
    expect(await onScreen(page, '.profile-row .profile-figure') + await onScreen(page, '.program .program-name')).toBeGreaterThanOrEqual(6);

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

// On a phone, Profile and Ask are each one tap from the top of a district's page.
test.describe('a district at 390 by 844', () => {
  test.use({viewport: {width: 390, height: 844}});
  test('Profile and Ask are tabs at the top', async ({page}) => {
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    const tabs = page.getByRole('tablist', {name: 'District page'});
    await expect(tabs).toBeInViewport({ratio: 1});
    await expect(page.locator('#profile .profile-stats')).toBeInViewport();
    await tabs.getByRole('tab', {name: 'Ask'}).click();
    await expect(page.getByLabel('Your question')).toBeInViewport();
    await tabs.getByRole('tab', {name: 'Profile'}).click();
    await expect(page.locator('#profile .profile-stats')).toBeInViewport();
    // The district's page starts at its heading; the map follows the profile.
    expect(await page.evaluate(() => scrollY)).toBe(0);
  });
});
