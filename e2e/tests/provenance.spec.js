// Where figures come from, on a district's At a glance and its sections, and whose checks the Checks section shows.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('At a glance names its source and survey years, with each figure’s definition one keypress away', async ({page}) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  const glance = page.locator('#profile-glance');
  const line = glance.locator('.glance-source');
  await expect(line).toBeVisible();
  await expect(line).toHaveText(/^Sources?: American Community Survey 5-year( \(opens in a new tab\))?, 2020-2024/);
  await expect(line.getByRole('link', {name: /American Community Survey 5-year/}).first()).toBeVisible();
  // Survey years only: never when KLEAR last updated its data.
  await expect(line).not.toContainText(/updated|built/i);
  const about = glance.locator('details.glance-about');
  await about.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(about).toHaveAttribute('open', '');
  const terms = about.locator('dt');
  await expect(terms.first()).toHaveText('People below the poverty level');
  await expect(about).toContainText('Who is counted:');
  await expect(about).toContainText(/House District 22: American Community Survey 5-year( \(opens in a new tab\))?, 2020-2024, with its 90% margin of error\./);
  await expect(about).toContainText(/Honolulu County: American Community Survey 5-year( \(opens in a new tab\))?, 2020-2024\./);
});

test('a section that shows a district’s largest industries says so', async ({page}) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.locator('#profile-income-work').evaluate(d => { d.open = true; });
  await expect(page.locator('#profile-income-work .profile-intro')).toHaveText(/^The industry rows are the district's three largest of 13 industry groups\./);
});

test.describe('the House or Senate switch on a phone', () => {
  for (const width of [320, 390]) {
    test(`at ${width} px it comes before the numbers, and the map follows it`, async ({page}) => {
      await page.setViewportSize({width, height: 844});
      await page.goto('/');
      const index = page.getByRole('navigation', {name: 'Every district by number'});
      const near = page.locator('#find-col .find-switch');
      await expect(near).toBeVisible();
      // One switch shows at a time, so the keyboard meets it once.
      await expect(page.locator('#chamber-switch')).toBeHidden();
      const sw = await near.boundingBox(), numbers = await index.boundingBox();
      expect(sw.y + sw.height).toBeLessThanOrEqual(numbers.y);
      await near.getByRole('button', {name: 'Senate'}).click();
      await expect(near.getByRole('button', {name: 'Senate'})).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('#seats-live')).toHaveText('Showing the 25 Senate districts.');
      await expect(index.getByRole('link', {name: 'Senate District 12', exact: true})).toBeVisible();
      await expect(index.getByRole('link', {name: 'House District 22', exact: true})).toBeHidden();
      // The map, further down, shows the Senate's districts, and its own switch says so.
      await expect(page.locator('#chamber-switch [data-chamber=senate]')).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('#map .district')).toHaveCount(25);
    });
  }
});

test('the checks say when the results on file are not for the data the site serves', async ({page}) => {
  await page.route('**/api/checks', route => route.fulfill({json: {status: 'stale', attempt: null, checks: [], eval: {}, caveats: []}}));
  await page.goto('/data#checks');
  const identity = page.locator('#checks-identity');
  await expect(identity).toContainText('come from a different build than the data this site serves');
  await expect(page.locator('#cks-list')).toBeHidden();
  await expect(identity).not.toContainText(/\d{4}-\d{2}-\d{2}/);
});

test('the checks say when the last build attempt failed and the site still serves the checked data', async ({page}) => {
  await page.route('**/api/checks', route => route.fulfill({json: {
    status: 'current', verdict: 'All gated checks passed.', store: {replaced: true},
    attempt: {served: false, replaced: false, reason: 'gated checks failed: source_fidelity', verdict: 'Gated checks failed: source_fidelity.'},
    checks: [{name: 'boundaries_house', title: 'House district boundaries', gate: true, result: 'PASS', summary: '51 districts, 0 problems', about: '', notes: [], tables: []}],
    eval: {}, caveats: [],
  }}));
  await page.goto('/data#checks');
  const identity = page.locator('#checks-identity');
  await expect(identity).toContainText('These results are from the build that made the data this site serves. All gated checks passed.');
  await expect(identity).toContainText('The latest build attempt did not replace the data (gated checks failed: source_fidelity), so the site still serves the data these results check.');
  await expect(page.locator('#cks-list')).toBeVisible();
});
