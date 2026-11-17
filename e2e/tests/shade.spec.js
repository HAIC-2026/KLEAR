// The rankings page's Rankings and map tab: the map colored by a measure, in shades of the chamber color with a legend,
// stripes on unreliable estimates and dots on districts with no figure, the picked district's figure over the map,
// and every district in a table, highest first or in another order.
// The fixture store has a poverty rate for House 22 and 23 and an unreliable one for Senate 12; other districts have none.
import {test, expect} from '@playwright/test';
import {offline} from './helpers.js';

const MEASURE = 'People below the poverty level';

test.beforeEach(async ({context, page}) => {
  await offline(context);
  await page.goto('/rankings');
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
});

test('the House map opens colored by poverty, with its legend, the picked district and the ranked table', async ({page}) => {
  const map = page.locator('#district-map'), legend = page.locator('#map-legend');
  await expect(page.getByRole('tab', {name: 'Rankings and map'})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#map-shade')).toHaveValue('pct_people_poverty');
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);
  await expect(page.locator('#shade-live')).toHaveText(`${MEASURE}, by House district, now shown on the map.`);

  // Every House district has a shade, or the no-figure fill with dots, set by class rather than a style attribute.
  await expect(page.locator('#map .district:not(.copy)')).toHaveCount(51);
  await expect(page.locator('#map .district:not(.copy):is(.q1,.q2,.q3,.q4,.q5,.none)')).toHaveCount(51);
  await expect(page.locator('#map .district:not(.copy)[data-geo="house:022"]')).toHaveClass(/\bq5\b/);
  await expect(page.locator('#map .district:not(.copy)[data-geo="house:023"]')).toHaveClass(/\bq1\b/);
  await expect(page.locator('#map .district:not(.copy).none')).toHaveCount(49);
  await expect(page.locator('#map .marks .none')).toHaveCount(49);
  await expect(page.locator('#map .marks .unreliable')).toHaveCount(0);
  await expect(page.locator('#map-legend [style], #map .district[style], #map .marks [style]')).toHaveCount(0);
  await expect(page.locator('#map .district:not(.copy)[data-geo="house:022"]')).toHaveAccessibleName('House District 22, Oʻahu: 12.3% ± 2.1 points');
  await expect(page.locator('#map .district:not(.copy)[data-geo="house:001"]')).toHaveAccessibleName('House District 1, Hawaiʻi: no figure');

  await expect(legend.locator('.legend-title')).toHaveText(`${MEASURE}, by House district`);
  await expect(legend.locator('.legend-keys li')).toHaveText(['Lightest shade: 7.9%', 'Darkest shade: 12.3%', 'No figure']);
  await expect(legend).toContainText('Darker shades are higher. Each shade holds about the same number of districts. Districts whose margins of error overlap may not differ.');
  await expect(legend.locator('.legend-source')).toHaveText('Source: American Community Survey 5-year (opens in a new tab), 2020-2024. Hawaiʻi as a whole: 9.8% ± 0.4 points.');
  await expect(legend.getByRole('link', {name: 'American Community Survey 5-year (opens in a new tab)'})).toHaveAttribute('target', '_blank');

  // The district ranking lists every district, highest first with its rank, then the ones with no figure. It is a card
  // of its own, always open.
  await expect(page.getByRole('heading', {name: 'District ranking'})).toBeVisible();
  const profile = await (await page.request.get('/api/profile?district=house:022')).json();
  const row = profile.sections.flatMap(s => s.rows).find(r => r.id === 'pct_people_poverty');
  await expect(page.locator('#map-table caption')).toHaveText(`${MEASURE}, by House district, highest first`);
  const rows = page.locator('#map-table tbody tr');
  await expect(rows).toHaveCount(51);
  await expect(page.locator('#map-table thead th')).toHaveText(['Rank', 'District', 'Estimate', '90% margin of error']);
  await expect(rows.nth(0).locator('th, td')).toHaveText(['1', 'House District 22', row.value, row.spread]);
  await expect(rows.nth(1).locator('th, td')).toHaveText(['2', 'House District 23', '7.9%', '± 1.8 points']);
  await expect(rows.nth(2).locator('th, td')).toHaveText(['–no rank', 'House District 1', 'Not available', '']);
  // No estimate here is unreliable, so there is no red mark and no key for it.
  await expect(page.locator('#map-table tbody .unreliable-mark')).toHaveCount(0);
  await expect(page.locator('#map-table-key')).toBeHidden();

  // On the statewide map, a click on Oʻahu zooms in without picking a district.
  await expect(page.getByText('Select an island to zoom in.')).toBeVisible();
  await expect(page.locator('#map .label[data-geo="house:005"]')).toBeVisible();
  await page.locator('#map .district[data-geo="house:047"]').click();
  await expect(map.getByRole('button', {name: 'Oʻahu', exact: true})).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#map .district.picked')).toHaveCount(0);
  // The next click picks a district: it keeps its shade, gets the picked outline and is marked in the ranking. Screen
  // readers hear the same figures as in its profile: its own with its margin, then the state's. The fixture's two
  // ranges overlap, so that is said in words.
  await page.locator('#map .district[data-geo="house:022"]').click();
  await expect(page.locator('#map .district:not(.copy)[data-geo="house:022"]')).toHaveClass(/\bq5\b/);
  await expect(page.locator('#map .pick-line')).toHaveAttribute('d', /\S/);
  await expect(page.locator('#map-table tbody tr.picked')).toHaveAttribute('data-geo', 'house:022');
  const value = page.locator('#pick-value');
  await expect(value).toHaveText(/^House District 22\. People below the poverty level: 12\.3% ± 2\.1 points\. .+ the rate in Hawaiʻi: 9\.8%\. Press Enter again to open its page\.$/);
  await expect(value).toContainText(`${row.value} ${row.spread}`);
  const state = row.against.at(-1);
  expect([state.place, state.direction]).toEqual(['Hawaiʻi', 'within_margin']);
  await expect(value).toContainText(`About the same as the rate in ${state.place}: ${state.value}.`);

  // A second click opens the picked district's page.
  await page.locator('#map .district[data-geo="house:022"]').click();
  await expect(page).toHaveURL(/\/district\/house-22$/);
});

test('the table runs in the order picked, and marks the district picked', async ({page}) => {
  const map = page.locator('#district-map'), rows = page.locator('#map-table tbody tr[data-geo]'), name = n => rows.nth(n).locator('th');
  await expect(rows).toHaveCount(51);
  await expect(name(0)).toHaveText('House District 22');
  const order = page.getByLabel('Order');
  await order.selectOption('low');
  await expect(page.locator('#map-table caption')).toHaveText(`${MEASURE}, by House district, lowest first`);
  await expect(name(0)).toHaveText('House District 23');
  await expect(name(1)).toHaveText('House District 22');
  // Each keeps its rank, 1 for the highest figure.
  await expect(rows.nth(0).locator('.rank-num')).toHaveText('2');
  await expect(rows.nth(1).locator('.rank-num')).toHaveText('1');
  // Districts with no figure stay last.
  await expect(name(2)).toHaveText('House District 1');
  await order.selectOption('number');
  await expect(name(0)).toHaveText('House District 1');
  await expect(name(21)).toHaveText('House District 22');
  // By island: each island's name over its districts, the highest first on each.
  await order.selectOption('island');
  await expect(page.locator('#map-table .rank-group th')).toHaveText(['Kauaʻi', 'Oʻahu', 'Maui County', 'Hawaiʻi Island']);
  await expect(rows).toHaveCount(51);

  // A district picked on the map is marked in the ranking, and each district's name there opens its page.
  await map.getByRole('button', {name: 'Honolulu', exact: true}).click();
  await page.locator('#map .district[data-geo="house:023"]').click();
  const picked = page.locator('#map-table tbody tr.picked');
  await expect(picked).toHaveCount(1);
  await expect(picked).toHaveAttribute('data-geo', 'house:023');
  await expect(picked).toHaveAttribute('aria-current', 'true');
  await page.locator('#map .district[data-geo="house:022"]').click();
  await expect(picked).toHaveAttribute('data-geo', 'house:022');
  await expect(page.locator('#map-table').getByRole('link', {name: 'House District 22', exact: true})).toHaveAttribute('href', '/district/house-22');
});

test('the Senate map marks an unreliable estimate with stripes', async ({page}) => {
  const map = page.locator('#district-map'), legend = page.locator('#map-legend');
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);
  await map.getByRole('button', {name: 'Senate'}).click();
  await expect(page.locator('#map .district:not(.copy)')).toHaveCount(25);
  await expect(page.locator('#map .district:not(.copy):is(.q1,.q2,.q3,.q4,.q5,.none)')).toHaveCount(25);
  await expect(page.locator('#map .district:not(.copy)[data-geo="senate:012"]')).toHaveClass(/\bq3\b/);
  await expect(page.locator('#map .marks .unreliable')).toHaveCount(1);
  await expect(page.locator('#map .marks .none')).toHaveCount(24);
  await expect(legend.locator('.legend-title')).toHaveText(`${MEASURE}, by Senate district`);
  await expect(legend.locator('.legend-keys li')).toHaveText(['Shade 1: 3.0%', 'Unreliable estimate', 'No figure']);
  await expect(legend).toContainText('The only estimate is unreliable because its margin of error is large. It is striped.');
  await expect(page.locator('#map .district:not(.copy)[data-geo="senate:012"]')).toHaveAccessibleName('Senate District 12, Oʻahu: 3.0% ± 2.5 points, unreliable estimate');
  await map.getByRole('button', {name: 'Oʻahu', exact: true}).click();
  await page.locator('#map .district[data-geo="senate:012"]').click();
  await expect(page.locator('#pick-value')).toContainText('Unreliable estimate: the margin of error is large.');
  await expect(page.locator('#map-table tbody tr')).toHaveCount(25);
  // The unreliable estimate carries a red mark beside its figure, and the key under the table says what it means.
  const mark = page.locator('#map-table tbody tr').first().locator('.unreliable-mark');
  await expect(mark).toHaveAccessibleName('Unreliable estimate');
  await expect(mark).toHaveAttribute('title', 'Unreliable estimate: the margin of error is large');
  await expect(page.locator('#map-table-key')).toHaveText(/Unreliable estimate: its margin of error is large\.$/);
});

test('a measure the store lacks says so and leaves the map plain', async ({page}) => {
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);
  await page.locator('#map-shade').selectOption({label: 'People without health insurance'});
  await expect(page.locator('#shade-status')).toHaveText('The data store has no figures for this measure yet. Try another one.');
  await expect(page.locator('#map')).not.toHaveClass(/\bshaded\b/);
  await expect(page.locator('#map-legend')).toBeHidden();
  await expect(page.locator('#map-table')).toBeHidden();
});

test('the home map has no colors: it only finds districts', async ({page}) => {
  await page.goto('/');
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
  await expect(page.locator('#map-shade, #map-legend, #map-table')).toHaveCount(0);
  await expect(page.locator('#map')).not.toHaveClass(/\bshaded\b/);
});
