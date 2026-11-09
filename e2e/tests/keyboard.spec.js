// Keyboard only: every step of the main flow, and the map, without a mouse.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

// Phones have no Tab key; WebKit's Tab skips links unless the reader turns that on, as Safari does.
test.skip(({isMobile}) => isMobile, 'keyboard checks run at desktop size');

test.beforeEach(async ({context, page}) => {
  await offline(context);
  await page.goto('/');
});

test('skip link, search, ask, and read the answer with the keyboard', async ({page}) => {
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', {name: 'Skip to content'});
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('District, town, or ZIP code').first()).toBeFocused();

  // A search with one match picks it and moves to the question box.
  await page.keyboard.type('House 22');
  await page.keyboard.press('Enter');
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.getByLabel('Your question')).toBeFocused();

  await page.keyboard.type('How does poverty here compare?');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', {name: 'Ask KLEAR'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}.`);
  const answer = page.locator('#log article').last();
  await expect(answer.getByRole('heading', {level: 3})).toBeFocused();

  // From the answer, Tab reaches the first chart's data table, and Enter opens it.
  await page.keyboard.press('Tab');
  const table = answer.locator('.chart-data').first();
  await expect(table.locator('summary')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(table).toHaveAttribute('open', '');
  await expect(table.getByRole('table')).toBeVisible();

  // A suggested question can be asked from the keyboard too.
  await page.getByRole('button', {name: /^Kūpuna\s*: How many kūpuna/}).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#log article')).toHaveCount(2);
  await expect(page.locator('#log article').last().getByRole('heading', {level: 3})).toBeFocused();
});

test('the map: Tab in, arrows to move, Enter to pick, Enter again to ask', async ({page}) => {
  await expect(page.locator('#map .district').first()).toBeAttached();
  await page.locator('#island-tabs button').last().focus();
  await page.keyboard.press('Tab');
  const focused = page.locator('#map .district:focus');
  await expect(focused).toHaveCount(1);
  // The focus ring is drawn over the district, above its neighbors.
  await expect(page.locator('#map .ring-line')).toHaveAttribute('d', /\S/);

  await page.keyboard.press('Home');
  await expect(focused).toHaveAccessibleName(/^House District 1, /);
  await page.keyboard.press('ArrowRight');
  await expect(focused).toHaveAccessibleName(/^House District 2, /);
  await page.keyboard.press('Enter');
  await expect(focused).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#pick-name')).toHaveText('House District 2');

  await page.keyboard.press('Enter');
  await expect(page.locator('#district-title')).toHaveText('House District 2');
  await expect(page.getByLabel('Your question')).toBeFocused();
  await expect(page.locator('#map .ring-line')).toHaveAttribute('d', '');
});

test('the visible focus outline is never hidden', async ({page}) => {
  // Tab through the first stops on the page and check each has a visible outline or the map's ring.
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    const shown = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return true;
      const s = getComputedStyle(el);
      return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2) || el.matches('#map .district');
    });
    expect(shown, `focus outline on stop ${i + 1}`).toBe(true);
  }
});

test('a district under the mouse is outlined above its neighbors, and the outline goes when the mouse leaves', async ({page}) => {
  const map = page.locator('#district-map');
  await map.getByRole('button', {name: 'Oʻahu', exact: true}).click();
  const district = page.locator('#map .district[data-geo="house:022"]');
  const fill = () => district.evaluate(el => getComputedStyle(el).fill);
  const before = await fill();
  await district.hover();
  await expect(page.locator('#map .hover-line')).toHaveAttribute('d', await district.getAttribute('d'));
  await expect.poll(fill).not.toBe(before);
  // The hover outline is not the keyboard focus ring.
  await expect(page.locator('#map .ring-line')).toHaveAttribute('d', '');
  await page.mouse.move(1, 1);
  await expect(page.locator('#map .hover-line')).toHaveAttribute('d', '');
  await expect.poll(fill).toBe(before);
});

test('the Ask button keeps focus while the answer loads, then focus moves to the answer', async ({page}) => {
  await page.getByLabel('District, town, or ZIP code').first().fill('House 22');
  await page.keyboard.press('Enter');
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/chat', async route => { await held; await route.continue(); });
  await page.keyboard.type('How does poverty here compare?');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  const send = page.getByRole('button', {name: 'Ask KLEAR'});
  await expect(send).toHaveAttribute('aria-disabled', 'true');
  await expect(send).toBeFocused();
  // Pressing it again while busy does nothing.
  await page.keyboard.press('Enter');
  await expect(page.locator('#log .user')).toHaveCount(1);
  release();
  await expect(page.locator('#log article h3')).toBeFocused();
  await expect(send).not.toHaveAttribute('aria-disabled');
});
