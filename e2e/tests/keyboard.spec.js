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
  await expect(page.getByLabel('District, place, ZIP code, or street address').first()).toBeFocused();

  // A search with one match opens the district's page with focus on its name. The next stop, a link shown only while
  // it has focus, skips past the profile to the question box beside it.
  await page.keyboard.type('House 22');
  await page.keyboard.press('Enter');
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.getByRole('heading', {level: 1, name: DISTRICT})).toBeFocused();
  await page.keyboard.press('Tab');
  const skipAsk = page.getByRole('link', {name: 'Skip to the question box'});
  await expect(skipAsk).toBeFocused();
  await expect(skipAsk).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Your question')).toBeFocused();

  await page.keyboard.type('How does poverty here compare?');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', {name: 'Ask KLEAR'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}.`);
  const answer = page.locator('#log article').last();
  await expect(answer.getByRole('heading', {level: 3})).toBeFocused();

  // From the answer, Tab reaches what the first figure's label means, and Enter opens it.
  await page.keyboard.press('Tab');
  const about = answer.locator('details.meaning').first();
  await expect(about.locator('summary')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(about).toHaveAttribute('open', '');
  await expect(about.getByRole('link', {name: 'Glossary of terms (opens in a new tab)'})).toBeVisible();

  // Tab goes on, past the glossary link and the next labels' meanings, to the first chart's data table.
  const table = answer.locator('.chart-data').first();
  const onTable = () => table.locator('summary').evaluate(s => s === document.activeElement);
  for (let i = 0; i < 6 && !(await onTable()); i++) await page.keyboard.press('Tab');
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

// One column: the profile and the chat are two tabs. The arrow keys move between them, and Tab moves into the one shown.
test.describe('one column', () => {
  test.use({viewport: {width: 900, height: 800}});
  test('Profile and Ask are tabs that the arrow keys switch', async ({page}) => {
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    const tabs = page.getByRole('tablist', {name: 'District page'});
    const profileTab = tabs.getByRole('tab', {name: 'Profile'}), askTab = tabs.getByRole('tab', {name: 'Ask'});
    await expect(profileTab).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tabpanel', {name: 'Profile'})).toBeVisible();
    await expect(page.getByLabel('Your question')).toBeHidden();

    await profileTab.focus();
    await page.keyboard.press('ArrowRight');
    await expect(askTab).toBeFocused();
    await expect(askTab).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tabpanel', {name: 'Ask'})).toBeVisible();
    await expect(page.locator('#profile')).toBeHidden();
    // Only the chosen tab is a Tab stop; the next stop is in its panel.
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', {name: 'Change district'})).toBeFocused();
    await askTab.focus();
    await page.keyboard.press('Home');
    await expect(profileTab).toBeFocused();
    await expect(page.locator('#profile')).toBeVisible();

    // An answer that arrives while the profile shows is marked on the Ask tab until it is opened.
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/api/chat', async route => { await held; await route.continue(); });
    await askTab.click();
    await page.getByLabel('Your question').fill('How does poverty here compare?');
    await page.getByRole('button', {name: 'Ask KLEAR'}).click();
    await profileTab.click();
    release();
    await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}.`);
    await expect(tabs.getByRole('tab', {name: 'Ask New answer'})).toBeVisible();
    await askTab.click();
    await expect(page.locator('#tab-new')).toBeHidden();
  });
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
  // Tab through the first stops on the home page, then on a district's page, and check each has a visible outline or
  // the map's ring.
  for (const address of ['/', '/district/house-22']) {
    await page.goto(address);
    if (address !== '/') await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press('Tab');
      const shown = await page.evaluate(() => {
        const el = document.activeElement;
        if (!el || el === document.body) return true;
        const s = getComputedStyle(el);
        return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2) || el.matches('#map .district');
      });
      expect(shown, `focus outline on stop ${i + 1} of ${address}`).toBe(true);
    }
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
  await page.getByLabel('District, place, ZIP code, or street address').first().fill('House 22');
  await page.keyboard.press('Enter');
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/chat', async route => { await held; await route.continue(); });
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
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

test('the shaded map: choose a measure, move between districts, hear each figure and open the table', async ({page}) => {
  await expect(page.locator('#map .district').first()).toBeAttached();
  const select = page.getByLabel('Color the map by');
  await select.focus();
  await expect(select).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(select).toHaveValue('pct_people_poverty');
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);

  // Each district's name carries its figure, and Enter shows it with the state comparison under the map.
  await page.locator('#island-tabs button').last().focus();
  await page.keyboard.press('Tab');
  const focused = page.locator('#map .district:focus');
  await page.keyboard.press('Home');
  await expect(focused).toHaveAccessibleName('House District 1, Hawaiʻi: no figure');
  for (let i = 1; i < 22; i++) await page.keyboard.press('ArrowRight');
  await expect(focused).toHaveAccessibleName('House District 22, Oʻahu: 12.3% ± 2.1 points');
  await expect(page.locator('#map .ring-line')).toHaveAttribute('d', /\S/);
  await page.keyboard.press('Enter');
  await expect(focused).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#pick-name')).toHaveText('House District 22');
  await expect(page.locator('#pick-value')).toContainText('People below the poverty level: 12.3% ± 2.1 points.');

  // Tab on to the table and open it.
  const summary = page.locator('#map-table summary');
  for (let i = 0; i < 8 && !(await summary.evaluate(el => el === document.activeElement)); i++) await page.keyboard.press('Tab');
  await expect(summary).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#map-table')).toHaveAttribute('open', '');
  await expect(page.locator('#map-table tbody tr').first().locator('th')).toHaveText('House District 22');
});
