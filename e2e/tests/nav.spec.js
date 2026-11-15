// The site header on every page: the wordmark and four tabs (Districts, which leads home to Find as the wordmark does,
// Compare, Rankings, and the data and methods), each opening a page of its own, the page you are on marked, and a
// header that stays on screen as the page scrolls without covering what has focus or what a link scrolls to.
import {test, expect} from '@playwright/test';
import {offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));
// Links scroll at once, so where they land can be measured.
test.use({reducedMotion: 'reduce'});

const PAGES = ['/', '/district/house-22', '/compare', '/rankings', '/data', '/accessibility.html'];
const LINKS = ['Districts', 'Compare', 'Rankings', 'Data & methods'];
// The tab each address belongs to; the statement is in none.
const CURRENT = {'/': 'Districts', '/district/house-22': 'Districts', '/compare': 'Compare', '/rankings': 'Rankings', '/data': 'Data & methods'};

const nav = page => page.getByRole('navigation', {name: 'Main navigation'});

// How far below the part of the header that stays on screen an element starts. Negative means the header covers it.
function belowHeader(page, target) {
  return target.evaluate(el => el.getBoundingClientRect().top - document.querySelector('.site-header').getBoundingClientRect().bottom);
}
async function toBottom(page) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
}

test('every page has the same four tabs and marks the one you are on', async ({page}) => {
  for (const address of PAGES) {
    await page.goto(address);
    const names = await nav(page).locator('a').evaluateAll(all => all.map(a => a.textContent.trim()));
    expect(names, address).toEqual(LINKS);
    await expect(nav(page).locator('details'), address).toHaveCount(0);
    const current = nav(page).locator('[aria-current=page]');
    if (!CURRENT[address]) {
      await expect(current, address).toHaveCount(0);
      continue;
    }
    await expect(current).toHaveText(CURRENT[address]);
    // Sighted readers see it too: the tab is bold, with a thick gold bar under it.
    expect(await current.evaluate(el => {
      const s = getComputedStyle(el);
      return Number(s.fontWeight) >= 700 && s.boxShadow.includes('-4px');
    }), address).toBe(true);
  }
});

test('from the bottom of any page, the header is on screen and every part is one click away', async ({page}) => {
  for (const address of PAGES) {
    await page.goto(address);
    await toBottom(page);
    await expect(nav(page), address).toBeInViewport();
    const next = address === '/compare' ? 'Data & methods' : 'Compare';
    await nav(page).getByRole('link', {name: new RegExp(`^${next.split(' ')[0]}`)}).click();
    await expect(page).toHaveURL(new RegExp(next === 'Compare' ? '/compare$' : '/data$'));
  }
});

test('the wordmark and the Districts tab lead home to Find, and an old glossary link lands below the header', async ({page}) => {
  await page.goto('/data');
  await toBottom(page);
  await page.locator('.site-header .brand').click();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Find your district');
  await page.goto('/rankings');
  await nav(page).getByRole('link', {name: 'Districts'}).click();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(nav(page).locator('[aria-current=page]')).toHaveText('Districts');
  // An old link to a glossary term lands on that term in the data page, below the header.
  await page.goto('/glossary.html#cv');
  await expect(page).toHaveURL(/\/data\?section=glossary#cv$/);
  await expect(page.locator('#cv')).toBeInViewport();
  expect(await belowHeader(page, page.locator('#cv'))).toBeGreaterThanOrEqual(0);
});

// A district's page leaves out the Find page's search and map, so a link on it never points at one of them: every
// link to a part of the same page leads to something that shows.
test('no link on a district page leads to a hidden section', async ({page}) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile .profile-end')).toBeVisible();
  const dead = await page.locator('a[href]').evaluateAll(all => all.filter(a => {
    const url = new URL(a.href);
    if (!url.hash || url.origin !== location.origin || url.pathname !== location.pathname || !a.checkVisibility()) return false;
    const target = document.getElementById(decodeURIComponent(url.hash.slice(1)));
    return !target || !target.checkVisibility();
  }).map(a => `${a.textContent.trim()} → ${a.getAttribute('href')}`));
  expect(dead).toEqual([]);
  // The header's Compare opens Compare with two searches.
  await nav(page).getByRole('link', {name: 'Compare'}).click();
  await expect(page).toHaveURL(/\/compare$/);
  await expect(page.locator('#cmp-a-place')).toBeVisible();
  await expect(page.locator('#cmp-b-place')).toBeVisible();
});

// The profile closes with a row that leads on: to comparing this district, to the data and methods, and to another.
test('a district profile ends with links to compare, the data and methods, and another district', async ({page}) => {
  await page.goto('/district/house-22');
  const end = page.getByRole('navigation', {name: 'More from KLEAR'});
  await expect(end.getByRole('link')).toHaveText(['Compare with another district', 'Sources and methods', 'Search another district']);
  await end.getByRole('link', {name: 'Sources and methods'}).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Data and methods');
  await page.goBack();
  await page.getByRole('navigation', {name: 'More from KLEAR'}).getByRole('link', {name: 'Search another district'}).click();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.getByLabel('Town, ZIP code, street address, district or legislator')).toBeInViewport();
  await page.goBack();
  await page.getByRole('navigation', {name: 'More from KLEAR'}).getByRole('link', {name: 'Compare with another district'}).click();
  await expect(page).toHaveURL(/\/compare\?d=house:022$/);
});

test('the data page lists its sections beside them, and each opens below the header', async ({page}) => {
  await page.goto('/data');
  const sections = page.getByRole('navigation', {name: 'On this page'});
  await expect(sections.getByRole('link')).toHaveText(['Sources', 'Methods', 'Glossary', 'Checks', 'Privacy']);
  for (const [name, id] of [['Glossary', 'glossary'], ['Checks', 'checks'], ['Sources', 'sources']]) {
    await sections.getByRole('link', {name}).click();
    await expect(page).toHaveURL(new RegExp(`#${id}$`));
    await expect(page.locator(`#${id} h2`).first()).toBeInViewport();
    expect(await belowHeader(page, page.locator(`#${id}`)), name).toBeGreaterThanOrEqual(0);
  }
  // An old address with no #part opens at its section.
  await page.goto('/methods.html');
  await expect(page).toHaveURL(/\/data#methods$/);
  await expect(page.locator('#methods h2')).toBeInViewport();
});

// Tab forward down the page and back up it: focus is never under the header, at desktop size and with the phone
// layout, where only the row of links stays on screen.
for (const width of [1280, 700]) {
  test.describe(`keyboard focus at ${width} px`, () => {
    test.skip(({isMobile}) => isMobile, 'Tab runs at desktop size');
    test.use({viewport: {width, height: 800}});
    test('is never covered by the header', async ({page}) => {
      test.slow();
      for (const address of ['/', '/data']) {
        await page.goto(address);
        await page.locator('#main').getByRole('link').first().waitFor({state: 'attached'});
        await page.getByRole('contentinfo').getByRole('link').last().focus();
        for (const key of [...Array(40).fill('Shift+Tab'), ...Array(40).fill('Tab')]) {
          await page.keyboard.press(key);
          const covered = await page.evaluate(() => {
            const el = document.activeElement, header = document.querySelector('.site-header');
            if (!el || el === document.body || header.contains(el) || el.matches('.skip')) return null;
            const r = el.getBoundingClientRect();
            return r.top < header.getBoundingClientRect().bottom - 1 ? `${el.tagName} ${el.textContent.trim().slice(0, 40)} at ${Math.round(r.top)}` : null;
          });
          expect(covered, `${address} after ${key}`).toBeNull();
        }
      }
    });
  });
}

test('on phones the links keep to one row of 44 px targets, and nothing scrolls sideways at 320 px', async ({page}) => {
  test.slow();
  for (const width of [320, 390]) {
    await page.setViewportSize({width, height: 640});
    for (const address of PAGES) {
      await page.goto(address);
      const items = await nav(page).locator('.nav-links > li > a').evaluateAll(all => all.map(el => el.getBoundingClientRect()));
      expect(new Set(items.map(r => Math.round(r.top))).size, `${address} at ${width}`).toBe(1);
      for (const r of items) expect(r.height, `${address} at ${width}`).toBeGreaterThanOrEqual(44);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `${address} at ${width}`).toBeLessThanOrEqual(0);
    }
  }
});

test('the accessibility statement is a page of the site', async ({page}) => {
  await page.goto('/');
  await page.getByRole('contentinfo').getByRole('link', {name: 'Accessibility statement'}).click();
  await expect(page).toHaveURL(/\/accessibility\.html$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText('KLEAR accessibility statement');
  await expect(page.getByRole('heading', {level: 2, name: 'Report a problem'})).toBeVisible();
  await expect(page.getByRole('contentinfo').getByRole('link', {name: 'Accessibility statement'})).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('contentinfo').getByRole('link', {name: 'Data & methods'})).toHaveAttribute('href', '/data');
});
