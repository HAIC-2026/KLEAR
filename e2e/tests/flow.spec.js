// The main flow at desktop and phone sizes: search, choose, profile, ask, open a source, and come back.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, openAll} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('search, choose, read the profile, ask, open a source, and come back', async ({page}) => {
  await page.goto('/');

  // Search: a town that spans districts lists them, with each share in the button's spoken name.
  await page.getByLabel('District, place, ZIP code, or street address').first().fill('Kāneʻohe');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#find-status')).toContainText('Choose a district.');
  const matches = page.locator('#find-matches');
  await expect(matches.getByRole('group', {name: 'House districts for Kāneʻohe'}).getByRole('button')).toHaveCount(2);

  // Choose: the chat now asks about that district, and the question box has focus.
  await matches.getByRole('button', {name: 'House 22 75% of Kāneʻohe residents'}).click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.getByLabel('Your question')).toBeFocused();

  // Profile: announced in one short line and labelled by its heading.
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  const profile = page.getByRole('region', {name: DISTRICT});
  await expect(profile.getByRole('heading', {name: DISTRICT})).toBeVisible();
  await profile.getByText('People', {exact: true}).click();
  await expect(profile.getByText('Kūpuna (65 and over)')).toBeVisible();

  // Ask: the conversation is not a live region, so long answers are not read aloud as they arrive.
  // A short status line says the answer is ready, and focus moves to the new answer's heading.
  await expect(page.locator('#log')).not.toHaveAttribute('role');
  await expect(page.locator('#log')).not.toHaveAttribute('aria-live');
  const answer = await ask(page);
  const heading = answer.getByRole('heading', {level: 3});
  await expect(heading).toBeFocused();
  await expect(heading).toHaveAccessibleName(new RegExp(`^KLEAR\\s*,\\s*${DISTRICT}$`));
  await expect(page.locator('#log .user')).toHaveText('You asked: How does poverty here compare with the county and state?');

  // Each chart says what it shows in a sentence and has its figures in a table.
  const chart = answer.getByRole('figure', {name: 'Percent of people below the poverty level'});
  await expect(chart.locator('.chart-summary')).toHaveText(
    'House District 22, at 12.3% ± 2.1 points, is higher than Honolulu County (8.9% ± 0.5 points) and is within the margin of error of Hawaiʻi (9.8% ± 0.4 points).');
  await expect(chart.locator('.chart-bars')).toHaveAttribute('aria-hidden', 'true');
  await chart.getByText('Show the data table').click();
  const table = chart.getByRole('table', {name: 'Percent of people below the poverty level'});
  await expect(table.getByRole('columnheader')).toHaveText(['Place', 'Estimate', '90% margin of error']);
  await expect(table.getByRole('row', {name: /Hawaiʻi/})).toHaveText(/Hawaiʻi\s*9\.8%\s*± 0\.4 points/);
  const kupuna = answer.getByRole('figure', {name: 'Population 65 years and over'});
  await expect(kupuna.locator('.chart-summary')).toContainText('is 11.1% of the total for Hawaiʻi (9,000)');

  // Open a source: it opens in a new tab, says so, and the conversation is still here afterwards.
  await answer.getByText('Sources', {exact: true}).click();
  const source = answer.getByRole('link', {name: 'American Community Survey 5-year (opens in a new tab)'});
  await source.focus();
  const [tab] = await Promise.all([page.waitForEvent('popup'), page.keyboard.press('Enter')]);
  await tab.waitForLoadState();
  expect(tab.url()).toBe('https://example.org/');
  await tab.close();
  await page.bringToFront();
  await expect(source).toBeFocused();
  await expect(page.locator('#log article')).toHaveCount(1);
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
});

// Every figure can be checked: an ACS figure links to its own table for the district on data.census.gov, and one
// KLEAR moved to districts from ZIP codes links to the methods page, which opens in a new tab like any source.
test('figures link to their table on data.census.gov or to the methods page', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await openAll(page);
  const profile = page.getByRole('region', {name: DISTRICT});
  const table = profile.getByRole('link', {name: 'Table DP05 for House District 22 (opens in a new tab)'});
  await expect(table).toHaveAttribute('href', 'https://data.census.gov/table/ACSDP5Y2024.DP05?g=620L900US15022');
  await expect(profile.getByRole('link', {name: 'ACS 5-year Data Profiles (opens in a new tab)'})).toHaveAttribute('href', 'https://example.org/profiles');
  const methods = profile.getByRole('link', {name: 'How KLEAR estimates district ranges (opens in a new tab)'});
  const [tab] = await Promise.all([page.waitForEvent('popup'), methods.click()]);
  await tab.waitForLoadState();
  const opened = new URL(tab.url());
  expect(opened.origin + opened.pathname + opened.hash).toBe(new URL('/methods.html#irs_soi_zip_2023', page.url()).href);
  await expect(tab.getByRole('heading', {name: 'Tax returns and the earned income tax credit'})).toBeVisible();
  await tab.close();
});

test('a failed question is announced and waits in the box to retry', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('District, place, ZIP code, or street address').first().fill('House 22');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await page.route('**/api/chat', route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error": "KLEAR could not answer. Please try again."}'}));
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#chat-live')).toHaveText('KLEAR could not answer. Please try again.');
  await expect(page.locator('#chat-status')).toHaveText('Your question is ready to retry.');
  await expect(page.getByLabel('Your question')).toHaveValue('How many kūpuna live here?');
  await expect(page.getByLabel('Your question')).toBeFocused();
});

test('focus stays put when the reader has moved on before the answer arrives', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('District, place, ZIP code, or street address').first().fill('House 22');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/chat', async route => { await held; await route.continue(); });
  await page.getByLabel('Your question').fill('How does poverty here compare?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await page.getByLabel('Highlight a ZIP code').focus();
  release();
  await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}.`);
  await expect(page.getByLabel('Highlight a ZIP code')).toBeFocused();
});
