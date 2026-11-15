// The Find page (/): a band with the page's title and a button down to the search and the map, the House or Senate
// switch over them, every district by number, then a question box that asks in the agent's panel, then the store's
// counts. Picking a district zooms the map to it and turns the page into the district's.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('the Find page opens with its band, the switch, the map and the search, and no agent box until asked', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Find your district');
  await expect(page.locator('#find-place')).toBeVisible();
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
  const titles = await page.locator('main h2:visible:not(.visually-hidden)').allTextContents();
  expect(titles).toEqual(['Search, or pick a number', 'Every district by number', 'Ask KLEAR']);
  await expect(page.getByRole('group', {name: 'Show House or Senate districts'})).toBeVisible();
  await expect(page.getByLabel('Ask about a district, a town or ZIP code, or all of Hawaiʻi', {exact: false})).toBeVisible();
  // The agent's own question box waits in its panel, and Compare's searches are not on this page.
  await expect(page.getByLabel('Your question')).toBeHidden();
  await expect(page.locator('#chat-fab')).toBeVisible();
  await expect(page.locator('#cmp-a-place')).toHaveCount(0);
  // The header marks Districts as the tab you are on, and the map links to the map colored by a measure.
  await expect(page.getByRole('navigation', {name: 'Main navigation'}).locator('[aria-current=page]')).toHaveText('Districts');
  await expect(page.getByRole('link', {name: /colored by poverty, income, rent and more/})).toHaveAttribute('href', '/rankings');
});

test('the / key jumps to the search from anywhere on the Find page, but not while typing', async ({page, isMobile}) => {
  test.skip(isMobile, 'a keyboard shortcut');
  await page.goto('/');
  await expect(page.locator('#find-place')).toHaveAttribute('aria-keyshortcuts', '/');
  await expect(page.locator('.key-hint')).toBeVisible();
  await page.locator('#home-question').focus();
  await page.keyboard.type('a/b');
  await expect(page.locator('#home-question')).toHaveValue('a/b');
  await page.locator('#home-question').blur();
  await page.keyboard.press('/');
  await expect(page.locator('#find-place')).toBeFocused();
  await expect(page.locator('#find-place')).toHaveValue('');
  await expect(page.locator('.key-hint')).toBeHidden();
});

test('the band is short, so the search shows on the first screen with no button to reach it', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('link', {name: 'Find your district'})).toHaveCount(0);
  await expect(page.locator('#find-place')).toBeInViewport();
});

test('the switch shows one chamber at a time, on the map and in the numbers', async ({page}) => {
  await page.goto('/');
  const index = page.getByRole('navigation', {name: 'Every district by number'});
  const sw = page.getByRole('group', {name: 'Show House or Senate districts'});
  await expect(sw.getByRole('button', {name: 'House'})).toHaveAttribute('aria-pressed', 'true');
  await expect(index.getByRole('link')).toHaveCount(51);
  await expect(page.locator('#map [data-geo^="house:"].district').first()).toBeAttached();
  await sw.getByRole('button', {name: 'Senate'}).click();
  await expect(sw.getByRole('button', {name: 'Senate'})).toHaveAttribute('aria-pressed', 'true');
  await expect(index.getByRole('link')).toHaveCount(25);
  await expect(index.getByRole('link', {name: 'Senate District 12', exact: true})).toHaveAttribute('href', '/district/senate-12');
  await expect(page.locator('#map [data-geo^="senate:"].district').first()).toBeAttached();
  await expect(page.locator('#map [data-geo^="house:"]')).toHaveCount(0);
  // The choice holds through a reload in the same tab.
  await page.reload();
  await expect(page.getByRole('group', {name: 'Show House or Senate districts'}).getByRole('button', {name: 'Senate'})).toHaveAttribute('aria-pressed', 'true');
  await expect(index.getByRole('link')).toHaveCount(25);
});

test('every district is one click away by number, and the numbers work as links', async ({page}) => {
  await page.goto('/');
  const index = page.getByRole('navigation', {name: 'Every district by number'});
  await expect(index.getByRole('link', {name: 'House District 22', exact: true})).toHaveAttribute('href', '/district/house-22');
  // A plain click zooms the map to the district and opens its page in place, at its profile.
  await index.getByRole('link', {name: 'House District 22', exact: true}).click();
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText(DISTRICT);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.locator('#map .district.picked:not(.copy)')).toHaveAttribute('data-geo', 'house:022');
  await page.goBack();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#find-place')).toBeVisible();
});

test('a district opens with the map beside its name, and Search another district comes back to the map', async ({page, isMobile}) => {
  await page.goto('/');
  await page.getByRole('navigation', {name: 'Every district by number'}).getByRole('link', {name: 'House District 22', exact: true}).click();
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  const map = await page.locator('#district-map').boundingBox(), head = await page.locator('#district-head').boundingBox();
  if (isMobile) expect(map.y + map.height).toBeLessThanOrEqual(head.y + 1);  // on a phone the map sits over the name
  else {
    expect(map.x + map.width).toBeLessThanOrEqual(head.x);  // the map at the left, the name beside it
    expect(Math.abs(map.y - head.y)).toBeLessThan(4);
  }
  // The map is zoomed in to the district alone: its outline fills a good part of the small map.
  const share = () => page.evaluate(() => {
    const svg = document.getElementById('map'), vb = svg.viewBox.baseVal, b = svg.querySelector('.district.picked').getBBox();
    return (b.width * b.height) / (vb.width * vb.height);
  });
  await expect.poll(share).toBeGreaterThan(0.08);
  await page.locator('#search-another').click();
  // Back on Find, the whole chain again, with nothing picked.
  await expect(page.locator('#map .district.picked')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.getElementById('map').viewBox.baseVal.width)).toBeGreaterThan(900);
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#find-place')).toBeVisible();
  await expect(page.locator('#find-section')).toBeInViewport();
  if (!isMobile) await expect(page.locator('#find-place')).toBeFocused();
  await expect(page.locator('#chat-panel-title')).toHaveText('Ask KLEAR');
});

test('the map shows every island where it lies, and its tabs zoom to each island and to urban Honolulu', async ({page, isMobile}) => {
  await page.goto('/');
  await expect(page.locator('#map .district')).toHaveCount(51);
  const width = () => page.evaluate(() => document.getElementById('map').viewBox.baseVal.width);
  await expect.poll(width).toBeGreaterThan(900);
  // Each island group's caption sits beside it, on a map wide enough to hold them.
  const captions = page.locator('#map .frame-caption.island');
  await expect(captions).toHaveText(['Kauaʻi · 3 House', 'Oʻahu · 34 House', 'Maui County · 6 House', 'Hawaiʻi Island · 8 House']);
  if (!isMobile) await expect(captions.first()).toBeVisible();
  await page.getByRole('group', {name: 'Zoom to island'}).getByRole('button', {name: 'Honolulu'}).click();
  await expect.poll(width).toBeLessThan(100);
});

test('each district on Compare has a small map zoomed in to it, with its number', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  const map = page.locator('.cmp-col-a .locator-map');
  await expect(map.locator('path.here')).toHaveCount(1);
  await expect(map.locator('.here-label')).toHaveText('22');
  // Close in: much narrower than the whole chain.
  expect(await map.evaluate(svg => svg.viewBox.baseVal.width)).toBeLessThan(100);
});

test('Tab reaches the numbers once, and the arrow keys move along them', async ({page, isMobile}) => {
  test.skip(isMobile, 'keys run at desktop size');
  await page.goto('/');
  const index = page.getByRole('navigation', {name: 'Every district by number'});
  const first = index.getByRole('link', {name: 'House District 1', exact: true});
  await first.focus();
  await page.keyboard.press('ArrowRight');
  await expect(index.getByRole('link', {name: 'House District 2', exact: true})).toBeFocused();
  await page.keyboard.press('End');
  await expect(index.getByRole('link', {name: 'House District 51', exact: true})).toBeFocused();
  // Tab leaves the numbers for the map's card beside them, which starts with the House or Senate switch.
  await page.keyboard.press('Tab');
  await expect(page.locator('#chamber-switch [data-chamber=house]')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(index.getByRole('link', {name: 'House District 51', exact: true})).toBeFocused();
});

test('a starter question on the Find page is asked in the agent’s panel', async ({page}) => {
  await page.goto('/');
  const question = 'How many kūpuna live in Kāneʻohe?';
  await page.locator('#home-starters').getByRole('button', {name: question}).click();
  await expect(page.locator('#chat')).toBeVisible();
  await expect(page.locator('#log .user')).toContainText(question);
  await expect(page.locator('#log article')).toHaveCount(1);
  await expect(page).toHaveURL(/:\d+\/$/);
  // Minimizing the panel keeps the conversation for the next time it opens.
  await page.locator('#chat-min').click();
  await expect(page.locator('#chat')).toBeHidden();
  await expect(page.locator('#chat-fab')).toBeFocused();
  await page.locator('#chat-fab').click();
  await expect(page.locator('#log article')).toHaveCount(1);
});

test('a question typed on the Find page is asked in the agent’s panel', async ({page}) => {
  await page.goto('/');
  await page.locator('#home-question').fill('How many kūpuna live in Kāneʻohe?');
  await page.locator('#home-ask').getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#chat')).toBeVisible();
  await expect(page.locator('#log .user')).toContainText('Kāneʻohe');
});

test('the old Ask page opens the Find page with the panel open, and asks a question handed to it', async ({page}) => {
  await page.goto('/ask');
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#chat')).toBeVisible();
  await page.goto(`/ask#q=${encodeURIComponent('How many kūpuna live in Kāneʻohe?')}`);
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#log .user')).toContainText('Kāneʻohe');
});

test('the Find page ends with the store’s counts and a link to the data and methods', async ({page}) => {
  await page.goto('/');
  const lines = page.locator('#facts li');
  await expect(lines).toHaveCount(2);  // the test store has no build record, so only the two counts
  await expect(lines.first()).toHaveText('2 House and 1 Senate districts.');
  await expect(page.locator('#source-table')).toHaveCount(0);
  await page.getByRole('link', {name: 'Sources, methods and checks'}).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.locator('#source-table tbody tr').first()).toBeVisible();
});

test('one district search finds a district, a district page has none, and the Find page is short', async ({page, isMobile}) => {
  await page.goto('/');
  const find = page.getByRole('combobox', {name: 'Town, ZIP code, street address, district or legislator', exact: true});
  await expect(find).toHaveCount(1);
  await expect(find).toBeVisible();
  await page.goto('/district/house-22');
  await expect(find).toBeHidden();
  await expect(page.locator('#seat-index')).toBeHidden();
  test.skip(isMobile, 'the height limit is for a 1280 px wide screen');
  await page.goto('/');
  await expect(page.locator('#facts')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(2600);
});

test('a legislator’s name finds the district they hold', async ({page}) => {
  await page.goto('/');
  const find = page.getByLabel('Town, ZIP code, street address, district or legislator');
  // The suggestions name the member in reading order and the seat they hold.
  await find.pressSequentially('pala');
  await expect(page.getByRole('option', {name: /Kawika Palakiko.*Representative · House District 23/})).toBeVisible();
  await find.fill('Rep. Palakiko');
  await page.getByRole('button', {name: 'Find district'}).click();
  await expect(page).toHaveURL(/\/district\/house-23$/);
  await expect(page.locator('#district-member')).toContainText('Kawika Palakiko');
});
