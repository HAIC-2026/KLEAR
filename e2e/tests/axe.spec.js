// axe checks of the main views: landing page, district profile, answers, and the map.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, expectAccessible, offline, openAll, showTab} from './helpers.js';

test.beforeEach(async ({context, page}) => {
  // A full-page axe run can take over ten seconds on a busy machine.
  test.slow();
  await offline(context);
  await page.goto('/');
});

test('landing page', async ({page}, testInfo) => {
  await expect(page.locator('#source-wrap')).toBeVisible();
  await expectAccessible(page, testInfo, 'landing page');
});

test('methods page', async ({page}, testInfo) => {
  await page.goto('/methods.html');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('How KLEAR estimates district figures');
  await expectAccessible(page, testInfo, 'methods page');
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
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('Which candidate should I vote for?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log article h3')).toContainText('Partisan request declined');
  await page.route('**/api/chat', route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error": "KLEAR could not answer. Please try again."}'}));
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .error')).toBeVisible();
  await expectAccessible(page, testInfo, 'declined and failed answers');
});

test('waiting line with its Cancel button', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  await page.route('**/api/chat', () => {});
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .pending .elapsed')).not.toBeEmpty();
  await expectAccessible(page, testInfo, 'waiting line');
});

test('answers with next questions, then a non-JSON error with Retry', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('Which candidate should I vote for?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log article')).toHaveCount(1);
  await page.locator('#log .followup').first().click();
  await expect(page.locator('#log article')).toHaveCount(2);
  await page.route('**/api/chat', route => route.fulfill({status: 502, contentType: 'text/html', body: '<h1>Bad Gateway</h1>'}));
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .retry')).toBeVisible();
  await expect(page.locator('#log .followups')).toBeVisible();
  await openAll(page);
  await expectAccessible(page, testInfo, 'answers with next questions and a retry');
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

test.describe('district page at one column', () => {
  test.use({viewport: {width: 900, height: 800}});
  test('both tabs, with a new answer marked', async ({page}, testInfo) => {
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    await openAll(page);
    await expectAccessible(page, testInfo, 'district page, Profile tab');
    await ask(page);
    await page.getByRole('tab', {name: 'Profile'}).click();
    await page.locator('#tab-new').evaluate(n => { n.hidden = false; });
    await expectAccessible(page, testInfo, 'district page, new answer');
    await page.getByRole('tab', {name: 'Ask'}).click();
    await openAll(page);
    await expectAccessible(page, testInfo, 'district page, Ask tab');
  });
});

test('glossary page', async ({page}, testInfo) => {
  await page.goto('/glossary.html');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Glossary');
  await expectAccessible(page, testInfo, 'glossary page');
});
