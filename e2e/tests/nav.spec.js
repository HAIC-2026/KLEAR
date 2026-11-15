// The site header on every page: four links in the order a visitor uses the site (find a district, ask about it,
// compare districts, the data and methods), the page you are on marked, and a header that stays on screen as the page
// scrolls without covering what has focus or what a link scrolls to.
import {test, expect} from '@playwright/test';
import {offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));
// Links scroll at once, so where they land can be measured.
test.use({reducedMotion: 'reduce'});

const PAGES = ['/', '/district/house-22', '/compare', '/data', '/accessibility.html'];
const LINKS = ['Find a district', 'Ask', 'Compare', 'Data & methods'];
// The page each address is, as its header names it; the home page, a district's page and the statement are not in it.
const CURRENT = {'/compare': 'Compare', '/data': 'Data & methods'};

const nav = page => page.getByRole('navigation', {name: 'Main navigation'});

// How far below the part of the header that stays on screen an element starts. Negative means the header covers it.
function belowHeader(page, target) {
  return target.evaluate(el => el.getBoundingClientRect().top - document.querySelector('.site-header').getBoundingClientRect().bottom);
}
async function toBottom(page) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
}

test('every page has the same four links in task order and marks the page you are on', async ({page}) => {
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
    // Sighted readers see it too: a thick underline on the link.
    expect(await current.evaluate(el => {
      const s = getComputedStyle(el);
      return s.textDecorationLine.includes('underline') && s.textDecorationThickness === '3px';
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

test('the home page parts the header opens start below it', async ({page}) => {
  await page.goto('/');
  await toBottom(page);
  await nav(page).getByRole('link', {name: /^Find/}).click();
  await expect(page).toHaveURL(/#find$/);
  await expect(page.locator('#find')).toBeInViewport();
  expect(await belowHeader(page, page.locator('#find'))).toBeGreaterThanOrEqual(0);
  // Ask moves to the question box, ready to type in.
  await toBottom(page);
  await nav(page).getByRole('link', {name: 'Ask'}).click();
  await expect(page.locator('#question')).toBeFocused();
  await expect(page.locator('#question')).toBeInViewport();
  expect(await belowHeader(page, page.locator('#question'))).toBeGreaterThanOrEqual(0);
  // An old link to a glossary term lands on that term in the data page, below the header.
  await page.goto('/glossary.html#cv');
  await expect(page).toHaveURL(/\/data\?section=glossary#cv$/);
  await expect(page.locator('#cv')).toBeInViewport();
  expect(await belowHeader(page, page.locator('#cv'))).toBeGreaterThanOrEqual(0);
});

// A district's page leaves out the home page's search, map and example, so a link on it never points at one of them:
// every link to a part of the same page leads to something that shows. The header's Ask opens the question box there.
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
  await nav(page).getByRole('link', {name: 'Ask'}).click();
  await expect(page.locator('#question')).toBeFocused();
  await expect(page.locator('#question')).toBeInViewport();
  await expect(page).toHaveURL(/\/district\/house-22$/);
});

// The profile closes with a row that leads on: to comparing districts, to the data and methods, and to another district.
test('a district profile ends with links to compare, the data and methods, and another district', async ({page}) => {
  await page.goto('/district/house-22');
  const end = page.getByRole('navigation', {name: 'More from KLEAR'});
  await expect(end.getByRole('link')).toHaveText(['Compare districts', 'Sources and methods', 'Find another district']);
  await end.getByRole('link', {name: 'Sources and methods'}).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Data and methods');
  await page.goBack();
  await page.getByRole('navigation', {name: 'More from KLEAR'}).getByRole('link', {name: 'Find another district'}).click();
  await expect(page).toHaveURL(/\/#find$/);
  await expect(page.getByLabel('Town, ZIP code, street address or district').first()).toBeInViewport();
  await page.goBack();
  await page.getByRole('navigation', {name: 'More from KLEAR'}).getByRole('link', {name: 'Compare districts'}).click();
  await expect(page).toHaveURL(/\/compare$/);
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
