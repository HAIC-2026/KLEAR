// axe checks of the main views: the Find page, a district's page, Compare, answers, the print dialog and the maps.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, expectAccessible, offline, openAll, showTab} from './helpers.js';

test.beforeEach(async ({context, page}) => {
  // A full-page axe run can take over ten seconds on a busy machine.
  test.slow();
  await offline(context);
  await page.goto('/');
});

test('landing page', async ({page}, testInfo) => {
  await expect(page.locator('#facts')).toBeVisible();
  await expectAccessible(page, testInfo, 'landing page');
});

test('compare with two districts searched for side by side', async ({page}, testInfo) => {
  await page.goto('/compare');
  await expectAccessible(page, testInfo, 'compare with two searches');
  for (const [slot, query] of [['a', 'House 22'], ['b', 'House 23']]) {
    await page.locator(`#cmp-${slot}-place`).fill(query);
    await page.locator(`#cmp-${slot}-find`).getByRole('button', {name: 'Find district'}).click();
  }
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  await expectAccessible(page, testInfo, 'compare side by side');
});

test('the agent’s panel on the Find page, before and after an answer', async ({page}, testInfo) => {
  await page.goto('/ask');
  await expect(page.locator('#chat')).toBeVisible();
  await expectAccessible(page, testInfo, 'agent panel');
  await page.locator('#starters').getByRole('button', {name: 'How many kūpuna live in Kāneʻohe?'}).click();
  await expect(page.locator('#log article')).toHaveCount(1);
  await expectAccessible(page, testInfo, 'agent panel with an answer');
});

test('data and methods page, with its sources, methods, glossary and checks', async ({page}, testInfo) => {
  await page.goto('/data');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Data and methods');
  await expect(page.locator('#source-wrap')).toBeVisible();
  await expect(page.locator('#build')).not.toContainText('Loading the latest build');
  await openAll(page);
  await expectAccessible(page, testInfo, 'data and methods page');
});

test('district search results', async ({page}, testInfo) => {
  await page.getByLabel('Town, ZIP code, street address, district or legislator').first().fill('Kāneʻohe');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#find-matches .match')).toHaveCount(3);
  await expectAccessible(page, testInfo, 'search results');
});

test('district profile', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await openAll(page);
  await expectAccessible(page, testInfo, 'district profile');
});

test.describe('district page with the agent as a panel', () => {
  test.use({viewport: {width: 900, height: 800}});
  test('the panel shut, open, and shut with a new answer marked', async ({page}, testInfo) => {
    test.slow();
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    await openAll(page);
    await expectAccessible(page, testInfo, 'district page, panel shut');
    await ask(page);
    await openAll(page);
    await expectAccessible(page, testInfo, 'district page, panel open');
    await page.locator('#chat-min').click();
    await page.locator('#fab-new').evaluate(n => { n.hidden = false; });
    await expectAccessible(page, testInfo, 'district page, new answer');
  });
});

test('the print dialog', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await page.getByRole('button', {name: 'Print or save as PDF'}).click();
  await expect(page.getByRole('dialog', {name: `Print ${DISTRICT}`})).toBeVisible();
  await expectAccessible(page, testInfo, 'print dialog');
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
  await page.locator('#send').click();
  await expect(page.locator('#log article h3')).toContainText('Partisan request declined');
  await page.route('**/api/chat', route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error": "KLEAR could not answer. Please try again."}'}));
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.locator('#send').click();
  await expect(page.locator('#log .error')).toBeVisible();
  await expectAccessible(page, testInfo, 'declined and failed answers');
});

test('waiting line with its Cancel button', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  await page.route('**/api/chat', () => {});
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.locator('#send').click();
  await expect(page.locator('#log .pending .elapsed')).not.toBeEmpty();
  await expectAccessible(page, testInfo, 'waiting line');
});

test('answers with next questions, then a non-JSON error with Retry', async ({page}, testInfo) => {
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('Which candidate should I vote for?');
  await page.locator('#send').click();
  await expect(page.locator('#log article')).toHaveCount(1);
  await page.locator('#log .followup').first().click();
  await expect(page.locator('#log article')).toHaveCount(2);
  await page.route('**/api/chat', route => route.fulfill({status: 502, contentType: 'text/html', body: '<h1>Bad Gateway</h1>'}));
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.locator('#send').click();
  await expect(page.locator('#log .retry')).toBeVisible();
  await expect(page.locator('#log .followups')).toBeVisible();
  await openAll(page);
  await expectAccessible(page, testInfo, 'answers with next questions and a retry');
});

test('map with a searched ZIP code', async ({page}, testInfo) => {
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
  await page.getByLabel('Town, ZIP code, street address, district or legislator').first().fill('96744');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#zip-status')).not.toBeEmpty();
  await expectAccessible(page, testInfo, 'map');
});

test('compare: districts side by side, every group open', async ({page}, testInfo) => {
  await page.goto('/compare?d=house:022,house:023');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Compare');
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  await openAll(page);
  await expectAccessible(page, testInfo, 'compare districts');
});

test('rankings page: outreach with every group open', async ({page}, testInfo) => {
  await page.goto('/rankings?view=outreach');
  await expect(page.locator('#out-result')).toBeVisible();
  await openAll(page);
  await expectAccessible(page, testInfo, 'outreach');
});

test('rankings page: map colored by a measure, with a picked district and its table', async ({page}, testInfo) => {
  await page.goto('/rankings');
  const map = page.locator('#district-map');
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
  await expect(page.locator('#map-legend')).toBeVisible();
  await map.getByLabel('Or choose from a list').selectOption({label: 'House District 22'});
  await expect(page.locator('#pick-value')).toContainText('12.3%');
  await openAll(page);
  await expectAccessible(page, testInfo, 'shaded map');
  // The Senate map, with an unreliable estimate in the legend.
  await map.getByRole('button', {name: 'Senate'}).click();
  await expect(page.locator('#map .marks .unreliable')).toHaveCount(1);
  await expectAccessible(page, testInfo, 'shaded Senate map');
});

test('accessibility statement page', async ({page}, testInfo) => {
  await page.goto('/accessibility.html');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('KLEAR accessibility statement');
  await expectAccessible(page, testInfo, 'accessibility statement');
});
