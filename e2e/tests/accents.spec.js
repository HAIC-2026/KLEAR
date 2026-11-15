// The drawings that add color to figures say nothing a screen reader needs, since the words beside them say the same,
// and the labels they add leave names as they were.
import {test, expect} from '@playwright/test';
import {offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('At a glance sets each rate beside the county and state on a strip that screen readers skip', async ({page}) => {
  await page.goto('/district/house-22');
  const glance = page.locator('#profile-glance');
  const strips = glance.locator('.glance-strip');
  await expect(strips.first()).toBeVisible();
  for (const strip of await strips.all()) {
    await expect(strip).toHaveAttribute('aria-hidden', 'true');
    // The district's band and dot, and at least one of the county's tick and the state's diamond.
    await expect(strip.locator('.gs-band, .gs-here')).toHaveCount(2);
    expect(await strip.locator('.gs-county, .gs-state').count()).toBeGreaterThan(0);
  }
  const key = glance.locator('.glance-key');
  await expect(key).toHaveAttribute('aria-hidden', 'true');
  await expect(key).toContainText('House District 22, with its margin of error');
  await expect(key).toContainText('Honolulu County');
});

test('beside a Senate district the statewide question asks about Senate districts', async ({page}) => {
  await page.goto('/district/senate-12');
  await expect(page.locator('#examples button', {hasText: 'Which Senate districts have the highest share of kūpuna?'})).toHaveCount(1);
  await expect(page.locator('#examples button', {hasText: 'Which House districts'})).toHaveCount(0);
  await page.goto('/district/house-22');
  await expect(page.locator('#examples button', {hasText: 'Which House districts have the highest share of kūpuna?'})).toHaveCount(1);
});

test('each figure in the ranking has a bar in its shade that screen readers skip', async ({page}) => {
  await page.goto('/rankings');
  const rows = page.locator('#map-table-body tr[data-geo]');
  await expect(rows.first()).toBeVisible();
  const bars = page.locator('#map-table-body .rank-bar');
  await expect(bars.first()).toBeVisible();
  for (const bar of await bars.all()) {
    await expect(bar).toHaveAttribute('aria-hidden', 'true');
    await expect(bar.locator('.rank-fill')).toHaveClass(/\bq[1-5]\b/);
  }
  // The bar adds nothing to the figure's cell for screen readers.
  await expect(rows.first().getByRole('cell').nth(1)).toHaveText(/^\S+%( !)?$/);
});

test('Compare marks its two districts A and B without changing their names', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  await expect(page.locator('.cmp-card .cmp-badge')).toHaveText(['A', 'B']);
  await expect(page.locator('.cmp-sticky .cmp-badge')).toHaveText(['A', 'B']);
  for (const badge of await page.locator('.cmp-badge').all()) await expect(badge).toHaveAttribute('aria-hidden', 'true');
  await expect(page.getByRole('heading', {level: 2, name: 'House District 22', exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {level: 2, name: 'House District 23', exact: true})).toBeVisible();
});
