// The figures and comparison marks on profiles and Compare.
import {test, expect} from '@playwright/test';
import {DISTRICT, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('Compare expands both districts when chat is minimized and keeps the draft', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  const card = page.locator('.cmp-col-a'), launcher = page.locator('#chat-fab');
  await expect(card.locator('.cmp-name')).toBeVisible();
  const fullWidth = (await card.boundingBox()).width;
  await expect(launcher).toHaveAttribute('aria-expanded', 'false');
  await launcher.press('Enter');
  await expect(launcher).toHaveAttribute('aria-expanded', 'true');
  if (page.viewportSize().width >= 1200) {
    expect((await card.boundingBox()).width).toBeLessThan(fullWidth * 0.8);
    await expect(page.locator('#send')).toBeInViewport({ratio: 1});
  }
  const question = page.getByLabel('Your question');
  await question.fill('Compare housing costs');
  await question.press('Escape');
  await expect(launcher).toBeFocused();
  await expect(page.locator('#chat')).toBeHidden();
  expect((await card.boundingBox()).width).toBe(fullWidth);
  await launcher.click();
  await expect(question).toHaveValue('Compare housing costs');
  await page.locator('#chat-min').click();
  await card.getByRole('link', {name: DISTRICT, exact: true}).click();
  await expect(page.locator('#welcome-text')).toHaveText(/^Pick a question/);
});

test('the two pickers keep keyboard focus and a failed comparison can retry in place', async ({page, isMobile}) => {
  await page.route('**/api/compare?*', route => {
    const pair = new URL(route.request().url()).searchParams.get('d');
    return pair.includes(',') ? route.fulfill({status: 503, body: '{}'}) : route.continue();
  });
  await page.goto('/compare');
  await page.locator('.cmp-col-a').getByRole('link', {name: DISTRICT, exact: true}).press('Enter');
  await expect(page.locator(isMobile ? '.cmp-col-b .cmp-pick-title' : '#cmp-b-place')).toBeFocused();
  await expect(page.getByRole('button', {name: 'Print or save as PDF'})).toBeEnabled();
  await page.locator('.cmp-col-b').getByRole('link', {name: 'House District 23', exact: true}).press('Enter');
  await expect(page.locator('.cmp-col-b')).toBeFocused();
  await expect(page.getByRole('button', {name: 'Retry comparison'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Print or save as PDF'})).toBeDisabled();
  await expect(page.locator('#compare-view .skeleton')).toHaveCount(0);
  await page.unroute('**/api/compare?*');
  await page.getByRole('button', {name: 'Retry comparison'}).press('Enter');
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  await expect(page.getByRole('button', {name: 'Print or save as PDF'})).toBeEnabled();
  await expect(page).toHaveURL(/house:022,house:023$/);
});

test('a failed profile can retry without losing the district or printing an empty page', async ({page}) => {
  await page.route('**/api/profile?*', route => route.fulfill({status: 503}), {times: 1});
  await page.goto('/district/house-22');
  await expect(page.getByRole('button', {name: 'Print or save as PDF'})).toBeDisabled();
  await page.getByRole('button', {name: 'Retry district profile'}).press('Enter');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.getByRole('button', {name: 'Print or save as PDF'})).toBeEnabled();
});

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
  const row = measure(table, /^Kūpuna \(65 and over\)/);
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
  const count = pairMeasure(page, 'People', /^Kūpuna \(65 and over\)/);
  await expect(count.locator('.num')).toHaveText(['100', '1,000']);
  await expect(count.locator('.compare-line')).toHaveText(['▲ 900% above House 23']);
  await expect(page.locator('.cmp-table + .profile-key').first()).toContainText('Percent gaps use the lower figure as the baseline.');
});

test('Compare shows one reading and no gap when rates overlap', async ({page}) => {
  // Widen House 23's range so it overlaps House 22's while the estimates still differ by 4.4 points.
  await page.route(url => url.pathname === '/api/compare', async route => {
    const response = await route.fetch(), body = await response.json();
    const profile = body.districts.find(p => p.geo === 'house:023');
    for (const r of [...profile.headline, ...profile.sections.flatMap(s => s.rows)]) {
      if (r.id === 'pct_people_poverty') Object.assign(r, {low: 5.4, high: 10.4, spread: '± 2.5 points'});
    }
    await route.fulfill({response, json: body});
  });
  await page.goto('/compare?d=house:022,house:023');
  const rate = pairMeasure(page, 'At a glance', 'People below the poverty level');
  await expect(rate.locator('.num')).toHaveText(['12.3%', '7.9%']);
  await expect(rate.locator('.compare-line')).toHaveText(['≈ No clear difference']);
  await expect(rate.getByRole('cell', {name: 'No clear difference', exact: true})).toHaveAttribute('colspan', '2');
});

// The five industries in the fixture: House 22's three largest are education, retail and food; House 23's are
// education, professional services and public administration. Each district has a figure for all five.
const INDUSTRIES = ['Work in education, health or social services', 'Work in retail trade', 'Work in hotels, restaurants or recreation',
  'Work in professional or business services', 'Work in government (public administration)'];
const industriesOf = page => page.getByRole('table', {name: /^Work and education: .*side by side$/}).locator('tbody .cmp-measure')
  .evaluateAll(labels => labels.map(l => l.firstChild.textContent.trim()));

test('Compare shows every industry either district’s profile shows, with a figure for both', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  const work = page.getByRole('table', {name: /^Work and education: .*side by side$/});
  await expect(work).toBeVisible();
  for (const label of INDUSTRIES) await expect(work.locator('tbody').filter({hasText: label}).locator('.num')).toHaveCount(2);
  await expect(work.locator('.cmp-figure.missing')).toHaveCount(0);
  await expect(pairMeasure(page, 'Work and education', 'Work in retail trade').locator('.num')).toHaveText(['15.0%', '8.0%']);
  await expect(pairMeasure(page, 'Work and education', 'Work in government (public administration)').locator('.num')).toHaveText(['6.0%', '13.0%']);
  // The section says which industries it shows, for both districts.
  await expect(page.getByText(/^The industry rows are the industries among either district's three largest/)).toBeVisible();
  const order = await industriesOf(page);
  expect(new Set(order)).toEqual(new Set(INDUSTRIES));
  // Swapped, the same rows in the same order: only the columns change places.
  await page.goto('/compare?d=house:023,house:022');
  await expect(page.getByRole('table', {name: /^Work and education: .*side by side$/})).toBeVisible();
  expect(await industriesOf(page)).toEqual(order);
  await expect(pairMeasure(page, 'Work and education', 'Work in retail trade').locator('.num')).toHaveText(['8.0%', '15.0%']);
});

test('a district with no figure in a row says why, and no figure is filled in', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  await page.locator('.cmp-section').evaluateAll(all => all.forEach(d => { d.open = true; }));
  const row = pairMeasure(page, 'Help sites', 'Low-income housing tax credit projects');
  await expect(row.locator('.cmp-figure').nth(0).locator('.num')).toHaveText('2');
  await expect(row.locator('.cmp-figure').nth(1)).toHaveText(/No figure\s*Not published for this district$/);
  await expect(row.locator('.compare-line')).toHaveCount(0);
});

test('county figures under both districts say they are one county’s, with no difference between the districts', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  const reference = page.locator('.cmp-section[data-key="cmp-reference"]');
  await reference.locator(':scope > summary').click();
  await expect(reference.getByRole('heading', {name: 'County context'})).toBeVisible();
  await expect(reference.locator('.profile-scope')).toHaveText('These figures cover all of Honolulu County, where most residents of both House District 22 and House District 23 live. They are not published for districts, so both districts have the same county figure, which cannot show how the two differ.');
  const table = reference.getByRole('table', {name: 'County context: Honolulu County, side by side'});
  const row = table.locator('tbody').filter({hasText: 'Unemployment rate'});
  // One cell for the one county figure, named for the county, and no reading that the districts are alike.
  await expect(row.locator('.cmp-figure')).toHaveCount(1);
  await expect(row.locator('.cmp-figure')).toHaveAttribute('colspan', '2');
  await expect(row.locator('.cmp-place')).toHaveText('Honolulu County');
  await expect(reference).not.toContainText('No clear difference');
  await expect(reference.locator('.profile-key')).toHaveCount(0);
});

test('each compared part keeps its sources and definitions, and they open by keyboard', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  const income = page.locator('.cmp-section[data-key="cmp-income-work"]');
  const sources = income.locator('.sources-fold').first();
  await sources.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(sources).toHaveAttribute('open', '');
  await expect(sources.getByRole('link', {name: /American Community Survey 5-year/}).first()).toBeVisible();
  await expect(sources).toContainText('2020-2024');
  const button = income.getByRole('button', {name: 'What this means: People below the poverty level'});
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator(`#${await button.getAttribute('aria-controls')}`)).toBeVisible();
});
