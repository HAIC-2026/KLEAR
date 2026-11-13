// The map colored by a measure: shades of the chamber color with a legend, stripes on unreliable estimates and dots on
// districts with no figure, the picked district's figure under the map, and the same figures as a table.
// The fixture store has a poverty rate for House 22 and 23 and an unreliable one for Senate 12; other districts have none.
import {test, expect} from '@playwright/test';
import {offline} from './helpers.js';

const MEASURE = 'People below the poverty level';

test.beforeEach(async ({context, page}) => {
  await offline(context);
  await page.goto('/');
  await expect(page.locator('#map .district').first()).toBeAttached();
});

test('color the House map by a measure, read the legend, pick a district and open the table', async ({page}) => {
  const map = page.locator('#district-map'), legend = page.locator('#map-legend');
  await expect(legend).toBeHidden();
  await expect(page.locator('#map-table')).toBeHidden();
  await map.getByLabel('Color the map by').selectOption({label: MEASURE});
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);
  await expect(page.locator('#shade-live')).toHaveText(`${MEASURE}, by House district, now shown on the map.`);

  // Every House district has a shade, or the no-figure fill with dots, set by class rather than a style attribute.
  await expect(page.locator('#map .district')).toHaveCount(51);
  await expect(page.locator('#map .district:is(.q1,.q2,.q3,.q4,.q5,.none)')).toHaveCount(51);
  await expect(page.locator('#map .district[data-geo="house:022"]')).toHaveClass(/\bq5\b/);
  await expect(page.locator('#map .district[data-geo="house:023"]')).toHaveClass(/\bq1\b/);
  await expect(page.locator('#map .district.none')).toHaveCount(49);
  await expect(page.locator('#map .marks .none')).toHaveCount(49);
  await expect(page.locator('#map .marks .unreliable')).toHaveCount(0);
  await expect(page.locator('#map-legend [style], #map .district[style], #map .marks [style]')).toHaveCount(0);
  await expect(page.locator('#map .district[data-geo="house:022"]')).toHaveAccessibleName('House District 22, Oʻahu: 12.3% ± 2.1 points');
  await expect(page.locator('#map .district[data-geo="house:001"]')).toHaveAccessibleName('House District 1, Hawaiʻi: no figure');

  await expect(legend.locator('.legend-title')).toHaveText(`${MEASURE}, by House district`);
  await expect(legend.locator('.legend-keys li')).toHaveText(['Lightest shade: 7.9%', 'Darkest shade: 12.3%', 'No figure']);
  await expect(legend).toContainText('Darker shades are higher. Each shade holds about the same number of districts. Districts whose margins of error overlap may not differ.');
  await expect(legend.locator('.legend-source')).toHaveText('Source: American Community Survey 5-year (opens in a new tab), 2020-2024. Hawaiʻi as a whole: 9.8% ± 0.4 points.');
  await expect(legend.getByRole('link', {name: 'American Community Survey 5-year (opens in a new tab)'})).toHaveAttribute('target', '_blank');

  // The picked district keeps its shade, gets the picked outline, and its figure shows under the map as in its profile.
  await map.getByLabel('Or choose from a list').selectOption({label: 'House District 22'});
  await expect(page.locator('#map .district[data-geo="house:022"]')).toHaveClass(/\bq5\b/);
  await expect(page.locator('#map .pick-line')).toHaveAttribute('d', /\S/);
  const value = page.locator('#pick-value');
  await expect(value).toBeVisible();
  await expect(value).toHaveText(/^People below the poverty level: 12\.3% ± 2\.1 points\. .+ the rate in Hawaiʻi: 9\.8%\.$/);
  const profile = await (await page.request.get('/api/profile?district=house:022')).json();
  const row = profile.sections.flatMap(s => s.rows).find(r => r.id === 'pct_people_poverty');
  await expect(value).toContainText(`${row.value} ${row.spread}`);
  await expect(value).toContainText(`${row.against.at(-1).lead.replace(/^./, c => c.toUpperCase())} ${row.against.at(-1).rest}.`);

  // The table lists every district, highest first, then the ones with no figure.
  await page.locator('#map-table summary').click();
  await expect(page.locator('#map-table caption')).toHaveText(`${MEASURE}, by House district, highest first`);
  const rows = page.locator('#map-table tbody tr');
  await expect(rows).toHaveCount(51);
  await expect(rows.nth(0).locator('th, td')).toHaveText(['House District 22', row.value, row.spread, '']);
  await expect(rows.nth(1).locator('th, td')).toHaveText(['House District 23', '7.9%', '± 1.8 points', '']);
  await expect(rows.nth(2).locator('th, td')).toHaveText(['House District 1', 'Not available', '', 'No figure']);

  // A ZIP code keeps the shades and outlines its districts.
  await map.getByLabel('Highlight a ZIP code').fill('96744');
  await map.getByRole('button', {name: 'Show on map'}).click();
  await expect(page.locator('#map .zip-line')).toHaveCount(3);
  await expect(page.locator('#map .zip-line').first()).toBeVisible();
  await expect(page.locator('#map .district[data-geo="house:022"]')).toHaveClass(/\bq5\b/);
});

test('the Senate map marks an unreliable estimate with stripes, and the plain map comes back', async ({page}) => {
  const map = page.locator('#district-map'), legend = page.locator('#map-legend');
  await map.getByLabel('Color the map by').selectOption({label: MEASURE});
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);
  await map.getByRole('button', {name: 'Senate'}).click();
  await expect(page.locator('#map .district')).toHaveCount(25);
  await expect(page.locator('#map .district:is(.q1,.q2,.q3,.q4,.q5,.none)')).toHaveCount(25);
  await expect(page.locator('#map .district[data-geo="senate:012"]')).toHaveClass(/\bq3\b/);
  await expect(page.locator('#map .marks .unreliable')).toHaveCount(1);
  await expect(page.locator('#map .marks .none')).toHaveCount(24);
  await expect(legend.locator('.legend-title')).toHaveText(`${MEASURE}, by Senate district`);
  await expect(legend.locator('.legend-keys li')).toHaveText(['Shade 1: 3.0%', 'Unreliable estimate', 'No figure']);
  await expect(legend).toContainText('The only estimate is unreliable because its margin of error is large. It is striped.');
  await expect(page.locator('#map .district[data-geo="senate:012"]')).toHaveAccessibleName('Senate District 12, Oʻahu: 3.0% ± 2.5 points, unreliable estimate');
  await map.getByLabel('Or choose from a list').selectOption({label: 'Senate District 12'});
  await expect(page.locator('#pick-value')).toContainText('Unreliable estimate: the margin of error is large.');
  await page.locator('#map-table summary').click();
  await expect(page.locator('#map-table tbody tr')).toHaveCount(25);
  await expect(page.locator('#map-table tbody tr').first().locator('td').last()).toHaveText('Unreliable estimate: the margin of error is large');

  await map.getByLabel('Color the map by').selectOption({label: 'Nothing (plain map)'});
  await expect(page.locator('#map')).not.toHaveClass(/\bshaded\b/);
  await expect(page.locator('#map .district:is(.q1,.q2,.q3,.q4,.q5,.none)')).toHaveCount(0);
  await expect(legend).toBeHidden();
  await expect(page.locator('#map-table')).toBeHidden();
  await expect(page.locator('#pick-value')).toBeHidden();
  await expect(page.locator('#shade-live')).toHaveText('The map is plain again.');
  await expect(page.locator('#map .district[data-geo="senate:012"]')).toHaveAccessibleName('Senate District 12, Oʻahu');
});

test('a measure the store lacks says so and leaves the map plain', async ({page}) => {
  await page.locator('#district-map').getByLabel('Color the map by').selectOption({label: 'People without health insurance'});
  await expect(page.locator('#shade-status')).toHaveText('The data store has no figures for this measure yet. Try another one.');
  await expect(page.locator('#map')).not.toHaveClass(/\bshaded\b/);
  await expect(page.locator('#map-legend')).toBeHidden();
});
