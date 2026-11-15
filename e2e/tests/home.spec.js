// The Find page (/): the search, every district by number and the island map, then a question box that opens the Ask
// page, then the store's counts. Comparing districts has its own page.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('the Find page finds, then asks, and leaves comparing to its own page', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Find your district');
  await expect(page.locator('#find-place')).toBeVisible();
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
  const titles = await page.locator('main h2:visible:not(.visually-hidden)').allTextContents();
  expect(titles).toEqual(['Every district by number', 'Ask KLEAR']);
  await expect(page.getByLabel('Ask about a district, a town or ZIP code, or all of Hawaiʻi')).toBeVisible();
  // The agent's own question box and the compare tool are not on this page.
  await expect(page.getByLabel('Your question')).toBeHidden();
  await expect(page.locator('#cmp-place')).toHaveCount(0);
  // The header marks Find as the page you are on.
  await expect(page.getByRole('navigation', {name: 'Main navigation'}).locator('[aria-current=page]')).toHaveText('Find a district');
});

test('every district is one click away by number, and the numbers work as links', async ({page}) => {
  await page.goto('/');
  const index = page.getByRole('navigation', {name: 'Every district by number'});
  await expect(index.getByRole('link')).toHaveCount(76);
  await expect(index.getByRole('link', {name: 'House District 22', exact: true})).toHaveAttribute('href', '/district/house-22');
  await expect(index.getByRole('link', {name: 'Senate District 12', exact: true})).toHaveAttribute('href', '/district/senate-12');
  // A plain click opens the district's page in place, at its profile.
  await index.getByRole('link', {name: 'House District 22', exact: true}).click();
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText(DISTRICT);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.goBack();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#find-place')).toBeVisible();
});

test('Tab reaches each chamber’s numbers once, and the arrow keys move along them', async ({page, isMobile}) => {
  test.skip(isMobile, 'keys run at desktop size');
  await page.goto('/');
  const index = page.getByRole('navigation', {name: 'Every district by number'});
  const first = index.getByRole('link', {name: 'House District 1', exact: true});
  await first.focus();
  await page.keyboard.press('ArrowRight');
  await expect(index.getByRole('link', {name: 'House District 2', exact: true})).toBeFocused();
  await page.keyboard.press('End');
  await expect(index.getByRole('link', {name: 'House District 51', exact: true})).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(index.getByRole('link', {name: 'Senate District 1', exact: true})).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(index.getByRole('link', {name: 'House District 51', exact: true})).toBeFocused();
});

test('a question on the Find page opens the Ask page and is asked there', async ({page}) => {
  await page.goto('/');
  const question = 'How many kūpuna live in Kāneʻohe?';
  await page.getByRole('group', {name: 'Questions to try'}).getByRole('button', {name: question}).click();
  // The question travels after the # and leaves the address once it is asked.
  await expect(page).toHaveURL(/\/ask$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Ask KLEAR');
  await expect(page.locator('#log .user')).toContainText(question);
  await expect(page.locator('#log article')).toHaveCount(1);
  await expect(page.getByRole('navigation', {name: 'Main navigation'}).locator('[aria-current=page]')).toHaveText('Ask');
});

test('a question typed on the Find page is asked on the Ask page', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('Ask about a district, a town or ZIP code, or all of Hawaiʻi').fill('How many kūpuna live in Kāneʻohe?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page).toHaveURL(/\/ask$/);
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
