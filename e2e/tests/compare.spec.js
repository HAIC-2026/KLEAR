// The profile's tables: each measure with the district, its county and the state side by side. A rate or an amount
// also gets a range strip and a reading. What a label means opens from a button beside it.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context, page}) => {
  await offline(context);
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.locator('#profile details.profile-section').evaluateAll(all => all.forEach(d => { d.open = true; }));
});

const measure = (table, label) => table.locator('.profile-row').filter({hasText: label});

test('a rate shows the district, county and state on one row, with a strip and a reading', async ({page}) => {
  const table = page.getByRole('table', {name: `Income: ${DISTRICT}, Honolulu County, Hawaiʻi`});
  await expect(table.getByRole('columnheader')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi', 'How it compares']);
  const row = measure(table, 'People below the poverty level');
  await expect(row.getByRole('rowheader')).toContainText('People below the poverty level');
  // The fixture's district range, 10.2% to 14.4%, clears the county's and touches the state's, so only the county
  // differs. The reading comes from those tested ranges, never from the gap between the estimates.
  await expect(row.getByRole('cell')).toHaveText([/12\.3%\s*± 2\.1 points$/, /8\.9%\s*± 0\.5 points$/, /9\.8%\s*± 0\.4 points$/, 'Higher than the county only']);

  // The strip draws the same figures, so screen readers skip it and a mouse gets them as a tip. Its scale starts at
  // 0 and ends at a round number past the largest value, here 20%.
  const strip = row.locator('.strip');
  await expect(strip).toHaveAttribute('aria-hidden', 'true');
  await expect(strip).toHaveAttribute('title', `${DISTRICT}: 12.3% ± 2.1 points. Honolulu County: 8.9% ± 0.5 points. Hawaiʻi: 9.8% ± 0.4 points.`);
  const style = (selector, property) => strip.locator(selector).evaluate((el, p) => parseFloat(el.style[p]), property);
  expect(await style('.strip-dot', 'left')).toBeCloseTo(61.5, 1);
  expect(await style('.strip-range.home', 'left')).toBeCloseTo(51, 1);
  expect(await style('.strip-range.home', 'width')).toBeCloseTo(21, 1);
  expect(await style('.strip-mark.above', 'left')).toBeCloseTo(44.5, 1);
  expect(await style('.strip-mark.below', 'left')).toBeCloseTo(49, 1);
  // The key under the table names each mark.
  await expect(page.locator('#profile-income .profile-key')).toHaveText(new RegExp(`^${DISTRICT}, with a line for its margin of error or range\\s*Honolulu County\\s*Hawaiʻi\\s*Each strip starts at 0 and has its own scale\\.$`));
});

test('a total sits beside the county and state totals, with no strip and no share sentence', async ({page}) => {
  const table = page.getByRole('table', {name: /^People:/});
  const row = measure(table, 'Kūpuna (65 and over)');
  // Median age in the same table is compared, so the last column is there and this row leaves it empty.
  await expect(row.getByRole('cell')).toHaveText([/1,000\s*± 600$/, /5,000\s*± 50$/, /9,000$/, '']);
  await expect(row.locator('.strip')).toHaveCount(0);
  await expect(measure(table, 'Median age').getByRole('cell').last()).toHaveText('Higher than county and state');
  await expect(page.locator('#profile')).not.toContainText(/of the total in|percent higher than|percent of the|about the same as the (rate|figure|amount)/);
});

test('a place with no figure keeps its column and says why', async ({page}) => {
  const table = page.getByRole('table', {name: /^Help sites:/});
  // Nothing in this section is compared, so it has no last column.
  await expect(table.getByRole('columnheader')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi']);
  await expect(measure(table, 'Low-income housing tax credit projects').getByRole('cell')).toHaveText([
    /2$/, /No figure\s*not published for county geographies$/, /No figure\s*not published for state geographies$/]);
  // County-level figures are the county's own, set against the state alone.
  const county = page.getByRole('table', {name: 'County-level figures: Honolulu County, Hawaiʻi'});
  await expect(measure(county, 'Unemployment rate').getByRole('cell')).toHaveText([/3\.0%$/, /3\.2%$/, 'Lower than the state']);
});

test('the button beside a label opens what it means, by mouse or keyboard', async ({page}) => {
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
  await expect(about.getByRole('link', {name: 'Glossary of terms (opens in a new tab)'})).toHaveAttribute('href', '/glossary.html');
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
  const wide = page.viewportSize().width >= 1200;
  const box = page.locator('#profile-income .profile-rows');
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
