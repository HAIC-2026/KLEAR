// Reflow, zoom, text size, text spacing, links, and language, checked on a page with every part showing.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, offline, openAll} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

// The page with a district chosen, its profile, an answer, and every disclosure open.
async function fullPage(page) {
  await page.goto('/');
  await chooseDistrict(page);
  await ask(page);
  await openAll(page);
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
      expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
      await expect(page.getByLabel('Your question')).toBeVisible();
      const ask = page.getByRole('button', {name: 'Ask KLEAR'});
      await expect(ask).toBeVisible();
      const box = await ask.boundingBox();
      expect(box.x + box.width).toBeLessThanOrEqual(width);
    });
  });
}

test('text is at least 12 px', async ({page}) => {
  await fullPage(page);
  const small = await page.evaluate(() => {
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
  expect(small).toEqual([]);
});

test.describe('text spacing', () => {
  // The site's content security policy allows no inline styles, so this test lifts it to add the spacing.
  test.use({bypassCSP: true});
  test('can be increased without losing content (WCAG 1.4.12)', async ({page}) => {
  await fullPage(page);
  await page.addStyleTag({content: '* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } p { margin-bottom: 2em !important; }'});
  expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
  // Buttons and labels still show all of their text.
  const clipped = await page.evaluate(() => [...document.querySelectorAll('button, label, summary, .match, .chip')]
    .filter(el => el.getClientRects().length && !el.matches('.visually-hidden') && getComputedStyle(el).overflow === 'hidden' && el.scrollWidth > el.clientWidth + 1)
    .map(el => el.textContent.trim().slice(0, 40)));
  expect(clipped).toEqual([]);
  });
});

test('links that leave the site say so, and in-page links have no arrow', async ({page}) => {
  await fullPage(page);
  const links = await page.locator('a[href]').evaluateAll(all => all.map(a => ({
    text: a.textContent, ext: a.classList.contains('ext'), blank: a.target === '_blank',
    external: new URL(a.href).origin !== location.origin,
  })));
  for (const l of links) {
    expect(l.ext, `arrow only on links that leave the site: ${l.text}`).toBe(l.external);
    if (l.blank) expect(l.text).toContain('(opens in a new tab)');
    else if (l.external) expect(l.text).toContain('(external site)');
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
  await page.getByLabel('Your question').fill('How many kūpuna?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#district-bar')).toHaveClass(/nudge/);
  expect(await page.locator('#district-bar').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
});
