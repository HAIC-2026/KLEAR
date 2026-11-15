// The home page gives each screen one job: the search, the question box, the map, one worked example drawn from a real
// district's profile, then a short sources summary. A visitor sees real figures without typing anything.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('the home page shows a real district’s figures and programs without any typing', async ({page}) => {
  await page.goto('/');
  const example = page.locator('#example');
  await expect(example).toBeVisible();
  // The test store has no House 49, so the example is the House district it holds the most figures for.
  await expect(example.getByRole('heading', {name: DISTRICT})).toBeVisible();
  const profile = await (await page.request.get('/api/profile?district=house:022')).json();
  const figures = example.locator('.profile-stats > div');
  await expect(figures).toHaveCount(profile.headline.length);
  for (const [i, h] of profile.headline.entries()) {
    await expect(figures.nth(i).locator('dt')).toHaveText(h.label);
    await expect(figures.nth(i).locator('.num')).toHaveText(h.value);
    if (h.reading) await expect(figures.nth(i).locator('.profile-reading')).toHaveText(h.reading);
  }
  const shown = profile.programs.programs.length ? profile.programs.programs : profile.programs.others;
  await expect(example.locator('.program-name > span:first-child')).toHaveText(shown.slice(0, 3).map(p => p.name));
  await example.getByRole('link', {name: `Open ${DISTRICT}’s full page`}).click();
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
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

test('one district search shows at a time, and the home page is short', async ({page, isMobile}) => {
  await page.goto('/');
  await expect(page.locator('#example')).toBeVisible();
  const searches = page.getByRole('combobox', {name: 'Town, ZIP code, street address or district'});
  await expect(searches).toHaveCount(1);
  await expect(page.locator('#find-place')).toBeVisible();
  await page.goto('/district/house-22');
  await expect(searches).toHaveCount(0);
  test.skip(isMobile, 'the height limit is for a 1280 px wide screen');
  await page.goto('/');
  await expect(page.locator('#example')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(3500);
});
