// The home page holds the first three steps of the flow, one section each: find a district (the search, then the map),
// ask about a place, and compare districts side by side. Then the store's counts.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('the home page finds, asks and compares, in that order', async ({page}) => {
  await page.goto('/');
  const titles = await page.locator('main h2.section-title').allTextContents();
  expect(titles).toEqual(['Or pick it on the map', 'Ask about a place', 'Compare districts']);
  await expect(page.locator('#find-place')).toBeVisible();
  await expect(page.locator('#map .district').first()).toBeAttached();
  await expect(page.getByLabel('Your question')).toBeVisible();
  await expect(page.locator('#cmp-place')).toBeVisible();
  await expect(page.locator('#compare').getByRole('link', {name: 'Rankings and outreach'})).toHaveAttribute('href', '/compare?view=rankings');
});

test('two districts compared on the home page sit side by side, and the address stays the home page', async ({page}) => {
  await page.goto('/');
  const compare = page.locator('#compare');
  await expect(compare.locator('.cmp-empty')).toBeVisible();
  for (const query of ['House 22', 'House 23']) {
    await page.locator('#cmp-place').fill(query);
    await compare.getByRole('button', {name: 'Add'}).click();
  }
  await expect(page.locator('#cmp-place')).toHaveValue('');
  await expect(compare.locator('.cmp-chip a')).toHaveText([DISTRICT, 'House District 23']);
  const table = compare.locator('.cmp-table');
  await expect(table.locator('thead th')).toHaveText(['Measure', /^House District 22/, /^House District 23/, 'Hawaiʻi']);
  // Each figure keeps its margin of error.
  const poverty = table.getByRole('row', {name: /People below the poverty level/});
  await expect(poverty.locator('td').first()).toContainText('12.3%');
  await expect(poverty.locator('td').first()).toContainText('± 2.1 points');
  await expect(page).toHaveURL(/:\d+\/$/);
  // Removing one leaves the other.
  await compare.getByRole('button', {name: 'Remove House District 23'}).click();
  await expect(compare.locator('.cmp-chip')).toHaveCount(1);
});

test('the home page ends with the store’s counts and a link to the data and methods', async ({page}) => {
  await page.goto('/');
  const lines = page.locator('#facts li');
  await expect(lines).toHaveCount(2);  // the test store has no build record, so only the two counts
  await expect(lines.first()).toHaveText('2 House and 1 Senate districts.');
  await expect(page.locator('#source-table')).toHaveCount(0);
  await page.getByRole('link', {name: 'Sources, methods and checks'}).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.locator('#source-table tbody tr').first()).toBeVisible();
});

test('one district search finds a district, a district page has none, and the home page is short', async ({page, isMobile}) => {
  await page.goto('/');
  const find = page.getByRole('combobox', {name: 'Town, ZIP code, street address or district', exact: true});
  await expect(find).toHaveCount(1);
  await expect(page.locator('#find-place')).toBeVisible();
  await page.goto('/district/house-22');
  await expect(find).toHaveCount(0);
  await expect(page.locator('#compare')).toBeHidden();
  test.skip(isMobile, 'the height limit is for a 1280 px wide screen');
  await page.goto('/');
  await expect(page.locator('#facts')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(3500);
});
