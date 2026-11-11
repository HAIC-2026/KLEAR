// axe checks of the main views: landing page, district profile, answers, and the map.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, expectAccessible, offline, openAll} from './helpers.js';

test.beforeEach(async ({context, page}) => {
  await offline(context);
  await page.goto('/');
});

test('landing page', async ({page}, testInfo) => {
  await expect(page.locator('#source-wrap')).toBeVisible();
  await expectAccessible(page, testInfo, 'landing page');
});

test('district search results', async ({page}, testInfo) => {
  await page.getByLabel('District, place, ZIP code, or street address').first().fill('Kāneʻohe');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#find-matches .match')).toHaveCount(3);
  await expectAccessible(page, testInfo, 'search results');
});

test('district profile', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await openAll(page);
  await expectAccessible(page, testInfo, 'district profile');
});

test('chat answer with charts and sources', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await ask(page);
  await openAll(page);
  await expectAccessible(page, testInfo, 'chat answer');
});

test('declined question and a failed question', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await page.getByLabel('Your question').fill('Which candidate should I vote for?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log article h3')).toContainText('Partisan request declined');
  await page.route('**/api/chat', route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error": "KLEAR could not answer. Please try again."}'}));
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .error')).toBeVisible();
  await expectAccessible(page, testInfo, 'declined and failed answers');
});

test('map with a ZIP code and a picked district', async ({page}, testInfo) => {
  const map = page.locator('#district-map');
  await map.scrollIntoViewIfNeeded();
  await expect(page.locator('#map .district').first()).toBeAttached();
  await map.getByLabel('Highlight a ZIP code').fill('96744');
  await map.getByRole('button', {name: 'Show on map'}).click();
  await expect(page.locator('#zip-status')).not.toBeEmpty();
  await map.getByLabel('Or choose from a list').selectOption({label: 'House District 22'});
  await expect(page.locator('#pick-name')).toHaveText('House District 22');
  await expectAccessible(page, testInfo, 'map');
});
