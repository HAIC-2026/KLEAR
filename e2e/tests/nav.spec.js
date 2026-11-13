// The site header on every page: the same links in task order, the page you are on marked, a header that stays on
// screen as the page scrolls without covering what has focus or what a link scrolls to, and a menu that needs no script.
import {test, expect} from '@playwright/test';
import {offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));
// Links scroll at once, so where they land can be measured.
test.use({reducedMotion: 'reduce'});

const PAGES = ['/', '/outreach.html', '/methods.html', '/glossary.html', '/checks.html', '/accessibility.html'];
const LINKS = ['Find a district', 'Ask', 'Map', 'Outreach', 'Sources', 'Methods', 'Glossary', 'How we check KLEAR', 'About'];
// The page each address is, as its header names it; the home page and the statement are not in the header.
const CURRENT = {'/outreach.html': 'Outreach', '/methods.html': 'Methods', '/glossary.html': 'Glossary', '/checks.html': 'How we check KLEAR'};

const nav = page => page.getByRole('navigation', {name: 'Main navigation'});
const menuButton = page => nav(page).locator('summary');
const menu = page => nav(page).locator('.nav-menu ul');

// How far below the part of the header that stays on screen an element starts. Negative means the header covers it.
function belowHeader(page, target) {
  return target.evaluate(el => el.getBoundingClientRect().top - document.querySelector('.site-header').getBoundingClientRect().bottom);
}
async function toBottom(page) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
}

test('every page has the same links in task order and marks the page you are on', async ({page}) => {
  for (const address of PAGES) {
    await page.goto(address);
    const names = await nav(page).locator('a').evaluateAll(all => all.map(a => a.textContent.trim()));
    expect(names, address).toEqual(LINKS);
    await expect(menuButton(page)).toHaveText(/^Data( and methods)?$/);
    const current = nav(page).locator('[aria-current=page]');
    if (!CURRENT[address]) {
      await expect(current, address).toHaveCount(0);
      continue;
    }
    await expect(current).toHaveText(CURRENT[address]);
    // Sighted readers see it too: a thick underline on the link, or on the menu's button when the page is in the menu.
    const shown = (await current.isVisible()) ? current : menuButton(page);
    await expect(shown).toBeVisible();
    expect(await shown.evaluate(el => {
      const s = getComputedStyle(el);
      return s.textDecorationLine.includes('underline') && s.textDecorationThickness === '3px';
    }), address).toBe(true);
  }
});

test('from the bottom of any page, the header is on screen and every part is one or two clicks away', async ({page}) => {
  for (const address of PAGES) {
    await page.goto(address);
    await toBottom(page);
    await expect(nav(page), address).toBeInViewport();
    // Two clicks: the menu, then a page in it.
    const next = address === '/methods.html' ? 'Glossary' : 'Methods';
    await menuButton(page).click();
    await menu(page).getByRole('link', {name: next}).click();
    await expect(page).toHaveURL(new RegExp(`/${next.toLowerCase()}\\.html$`));
  }
});

test('the home page sections a link opens start below the header', async ({page}) => {
  await page.goto('/');
  for (const [name, target] of [['Map', '#district-map'], ['Ask', '#chat'], ['Find', '#find'], ['About', '#about'], ['Sources', '#sources']]) {
    await toBottom(page);
    if (name === 'Sources') await menuButton(page).click();
    await nav(page).getByRole('link', {name: new RegExp(`^${name}`)}).click();
    await expect(page).toHaveURL(new RegExp(`${target}$`));
    const section = page.locator(target);
    await expect(section).toBeInViewport();
    expect(await belowHeader(page, section), name).toBeGreaterThanOrEqual(0);
  }
  // A link to a glossary term from another page lands below the header too.
  await page.goto('/glossary.html#cv');
  expect(await belowHeader(page, page.locator('#cv'))).toBeGreaterThanOrEqual(0);
});

test.describe('the menu', () => {
  test('closes on Escape, after a link on the same page, and on a click elsewhere', async ({page}) => {
    await page.goto('/');
    await menuButton(page).click();
    await expect(menu(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu(page)).toBeHidden();
    await menuButton(page).click();
    await menu(page).getByRole('link', {name: 'Sources'}).click();
    await expect(page).toHaveURL(/#sources$/);
    await expect(menu(page)).toBeHidden();
    await menuButton(page).click();
    await page.getByRole('heading', {name: 'Data and sources'}).click();
    await expect(menu(page)).toBeHidden();
  });

  test('Escape returns focus to the menu button', async ({page, isMobile}) => {
    test.skip(isMobile, 'a keyboard check');
    await page.goto('/glossary.html');
    await menuButton(page).focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Tab');
    await expect(menu(page).getByRole('link', {name: 'Sources'})).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(menu(page)).toBeHidden();
    await expect(menuButton(page)).toBeFocused();
    // Moving on past the menu closes it.
    await page.keyboard.press('Enter');
    for (let i = 0; i < 5; i++) await page.keyboard.press('Tab');
    await expect(nav(page).getByRole('link', {name: 'About'})).toBeFocused();
    await expect(menu(page)).toBeHidden();
  });

  test.describe('with no script', () => {
    test.use({javaScriptEnabled: false});
    test('still opens and leads to each page', async ({page}) => {
      await page.goto('/glossary.html');
      await menuButton(page).click();
      await menu(page).getByRole('link', {name: 'How we check KLEAR'}).click();
      await expect(page).toHaveURL(/\/checks\.html$/);
      await expect(page.getByRole('heading', {level: 1})).toHaveText('How we check KLEAR');
    });
  });
});

// Tab forward down the page and back up it: focus is never under the header, at desktop size and with the phone
// layout, where only the row of links stays on screen.
for (const width of [1280, 700]) {
  test.describe(`keyboard focus at ${width} px`, () => {
    test.skip(({isMobile}) => isMobile, 'Tab runs at desktop size');
    test.use({viewport: {width, height: 800}});
    test('is never covered by the header', async ({page}) => {
      for (const address of ['/', '/methods.html']) {
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
  for (const width of [320, 390]) {
    await page.setViewportSize({width, height: 640});
    for (const address of PAGES) {
      await page.goto(address);
      const items = await nav(page).locator('.nav-links > li > a, .nav-links summary').evaluateAll(all => all.map(el => el.getBoundingClientRect()));
      expect(new Set(items.map(r => Math.round(r.top))).size, `${address} at ${width}`).toBe(1);
      for (const r of items) expect(r.height, `${address} at ${width}`).toBeGreaterThanOrEqual(44);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `${address} at ${width}`).toBeLessThanOrEqual(0);
      // The open menu stays on the screen too.
      await menuButton(page).click();
      const box = await menu(page).boundingBox();
      expect(box.x, address).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, address).toBeLessThanOrEqual(width);
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
  await expect(page.getByRole('contentinfo').getByRole('link', {name: 'Methods'})).toHaveAttribute('href', '/methods.html');
});
