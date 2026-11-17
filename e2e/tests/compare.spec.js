// The figures and comparison marks on profiles and Compare.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

async function openProfile(page) {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.locator('#profile details.profile-section').evaluateAll(all => all.forEach(d => { d.open = true; }));
}

const measure = (table, label) => table.locator('.profile-row').filter({hasText: label});

test('a rate shows the district, county and state on one row, and how far apart they are', async ({page}) => {
  await openProfile(page);
  const table = page.getByRole('table', {name: `Income: ${DISTRICT}, Honolulu County, Hawaiʻi`});
  await expect(table.getByRole('columnheader')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi', 'How it compares']);
  const row = measure(table, 'People below the poverty level');
  await expect(row.getByRole('rowheader')).toContainText('People below the poverty level');
  // Only the county's range clears the district's. Its gap is 12.3% minus 8.9%, in points.
  await expect(row.getByRole('cell')).toHaveText([/12\.3%\s*± 2\.1 points$/, /8\.9%\s*± 0\.5 points$/, /9\.8%\s*± 0\.4 points$/, /^▲ 3\.4 points above county\s*≈ No clear difference \(state\)$/]);
  const lines = row.locator('.compare-line');
  for (const line of await lines.all()) await expect(line.locator('.reading-mark')).toHaveAttribute('aria-hidden', 'true');
  await expect(table.locator('..').locator('.profile-key')).toHaveText(/≈ no clear difference\. Rate gaps are in percentage points\./);
});

test('a total sits beside the county and state totals, with no comparison and no share sentence', async ({page}) => {
  await openProfile(page);
  const table = page.getByRole('table', {name: /^People:/});
  const row = measure(table, 'Kūpuna (65 and over)');
  // Median age in the same table is compared, so the last column is there and this row leaves it empty.
  await expect(row.getByRole('cell')).toHaveText([/1,000\s*± 600$/, /5,000\s*± 50$/, /9,000$/, '']);
  await expect(row.locator('.compare-line')).toHaveCount(0);
  await expect(measure(table, 'Median age').getByRole('cell').last()).toHaveText(/^▲ 10% above county\s*▲ 9% above state$/);
  await expect(page.locator('#profile')).not.toContainText(/of the total in|percent higher than|percent of the|about the same as the (rate|figure|amount)/);
});

test('a place with no figure keeps its column and says why', async ({page}) => {
  await openProfile(page);
  const table = page.getByRole('table', {name: /^Help sites:/});
  // Nothing in this section is compared, so it has no last column.
  await expect(table.getByRole('columnheader')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi']);
  await expect(measure(table, 'Low-income housing tax credit projects').getByRole('cell')).toHaveText([
    /2$/, /No figure\s*not published for county geographies$/, /No figure\s*not published for state geographies$/]);
  // County-level figures are the county's own, set against the state alone.
  const county = page.getByRole('table', {name: 'County-level figures: Honolulu County, Hawaiʻi'});
  await expect(measure(county, 'Unemployment rate').getByRole('cell')).toHaveText([/3\.0%$/, /3\.2%$/, /^▼ 0\.2 points below state$/]);
});

test('the button beside a label opens what it means, by mouse or keyboard', async ({page}) => {
  await openProfile(page);
  const row = page.locator('#profile .profile-row').filter({hasText: 'Median age'});
  const button = row.getByRole('button', {name: 'What this means: Median age'});
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  // Big enough to tap.
  const box = await button.boundingBox();
  expect(Math.min(box.width, box.height)).toBeGreaterThanOrEqual(24);
  const about = page.locator(`#${await button.getAttribute('aria-controls')}`);
  await expect(about).toBeHidden();
  await button.click();
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(about.getByText('Half of residents are younger than this age and half are older.')).toBeVisible();
  await expect(about.getByRole('link', {name: 'Glossary of terms (opens in a new tab)'})).toHaveAttribute('href', '/data#glossary');
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(about).toBeHidden();
  // A row with a note keeps the note in view while its meaning is shut.
  const snap = page.locator('#profile .profile-row:has-text("Households in poverty without SNAP") + .profile-more');
  await expect(snap.locator('.profile-beside')).toBeVisible();
  await expect(snap.locator('.meaning')).toBeHidden();
});

test('a table that fits shows columns, one that does not stacks, and the page never scrolls sideways', async ({page}) => {
  await openProfile(page);
  const wide = page.viewportSize().width >= 1200;
  const box = page.locator('#profile-income-work .profile-rows').first();
  await expect(box).toHaveClass(wide ? 'profile-rows' : 'profile-rows stacked');
  // Stacked, each figure names its place, since the column headings are out of sight.
  const places = measure(box, 'People below the poverty level').locator('.profile-place');
  await expect(places).toHaveText([DISTRICT, 'Honolulu County', 'Hawaiʻi']);
  for (const place of await places.all()) await (wide ? expect(place).toBeHidden() : expect(place).toBeVisible());
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

  // A box too narrow for the columns stacks, and goes back to columns when it has room again.
  if (wide) {
    await box.evaluate(el => { el.style.width = '320px'; });
    await expect(box).toHaveClass('profile-rows stacked');
    await box.evaluate(el => { el.style.width = ''; });
    await expect(box).toHaveClass('profile-rows');
  }
});

const pairMeasure = (page, section, label) => page.getByRole('table', {name: new RegExp(`^${section}: .*side by side$`)})
  .locator('tbody').filter({hasText: label});

test('Compare shows a rate or count gap once, under the higher figure', async ({page}) => {
  await page.goto('/compare?d=house:023,house:022');
  const rate = pairMeasure(page, 'At a glance', 'People below the poverty level');
  await expect(rate.locator('.num')).toHaveText(['7.9%', '12.3%']);
  await expect(rate.locator('.compare-line')).toHaveText(['▲ 4.4 points above House 23']);
  await expect(rate.locator('.cmp-figure').nth(1)).toContainText('▲ 4.4 points above House 23');
  const count = pairMeasure(page, 'People', 'Kūpuna (65 and over)');
  await expect(count.locator('.num')).toHaveText(['100', '1,000']);
  await expect(count.locator('.compare-line')).toHaveText(['▲ 900% above House 23']);
  await expect(page.locator('.cmp-table + .profile-key').first()).toContainText('Percent gaps use the lower figure as the baseline.');
});

test('Compare shows one reading and no gap when rates overlap', async ({page}) => {
  // Widen House 23's range so it overlaps House 22's while the estimates still differ by 4.4 points.
  await page.route(url => url.pathname === '/api/profile' && url.searchParams.get('district') === 'house:023', async route => {
    const response = await route.fetch(), profile = await response.json();
    for (const r of [...profile.headline, ...profile.sections.flatMap(s => s.rows)]) {
      if (r.id === 'pct_people_poverty') Object.assign(r, {low: 5.4, high: 10.4, spread: '± 2.5 points'});
    }
    await route.fulfill({response, json: profile});
  });
  await page.goto('/compare?d=house:022,house:023');
  const rate = pairMeasure(page, 'At a glance', 'People below the poverty level');
  await expect(rate.locator('.num')).toHaveText(['12.3%', '7.9%']);
  await expect(rate.locator('.compare-line')).toHaveText(['≈ No clear difference']);
  await expect(rate.getByRole('cell', {name: 'No clear difference', exact: true})).toHaveAttribute('colspan', '2');
});
