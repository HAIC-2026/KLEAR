// The main flow at desktop and phone sizes: search, choose, profile, ask, open a source, and come back.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, openAll, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

test('search, choose, read the profile, ask, open a source, and come back', async ({page}) => {
  await page.goto('/');

  // Search: a town that spans districts lists them, with each share in the button's spoken name.
  await page.getByLabel('District, place, ZIP code, or street address').first().fill('Kāneʻohe');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#find-status')).toContainText('Choose a district.');
  const matches = page.locator('#find-matches');
  await expect(matches.getByRole('group', {name: 'House districts for Kāneʻohe'}).getByRole('button')).toHaveCount(2);

  // Choose: the district's own page opens at the top, its name the page heading, which takes focus. The chat asks
  // about it.
  await matches.getByRole('button', {name: 'House 22 75% of Kāneʻohe residents'}).click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  const title = page.getByRole('heading', {level: 1});
  await expect(title).toHaveText(DISTRICT);
  await expect(title).toBeFocused();
  await expect(page.locator('.band')).toBeHidden();
  expect(await page.evaluate(() => scrollY)).toBe(0);

  // Profile: announced in one short line. It starts with the headline figures and their margins.
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.locator('#district-about')).toContainText('Honolulu County');
  const profile = page.locator('#profile');
  const headline = profile.locator('.profile-stats div').first();
  await expect(headline.locator('dd').first()).toHaveText('12.3% ± 2.1 points');
  await expect(headline.locator('dt')).toHaveText('People below the poverty level');
  // Under the label, how the figure stands against the county and the state.
  await expect(headline.locator('dd.profile-reading')).toHaveText('Higher than the county only');
  // A link at the top leads to each section, and opens it.
  const jumps = profile.getByRole('navigation', {name: 'In this profile'});
  await expect(jumps.getByRole('link')).toHaveText(['People', 'Income', 'Programs to explore here', 'Programs in use', 'Who may qualify', 'Help sites', 'County-level figures', 'Reapportionment plan']);
  await jumps.getByRole('link', {name: 'People'}).click();
  // The row's label, not its "What this means" button, which names the label to screen readers.
  await expect(profile.locator('.profile-label', {hasText: 'Kūpuna (65 and over)'})).toBeVisible();

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
  const chart = answer.getByRole('figure', {name: 'People below the poverty level'});
  await expect(chart.locator('.chart-summary')).toHaveText(
    'House District 22, at 12.3% ± 2.1 points, is higher than Honolulu County (8.9% ± 0.5 points) and is within the margin of error of Hawaiʻi (9.8% ± 0.4 points).');
  await expect(chart.locator('.chart-bars')).toHaveAttribute('aria-hidden', 'true');
  await chart.getByText('Show the data table').click();
  const table = chart.getByRole('table', {name: 'People below the poverty level'});
  await expect(table.getByRole('columnheader')).toHaveText(['Place', 'Estimate', '90% margin of error']);
  await expect(table.getByRole('row', {name: /Hawaiʻi/})).toHaveText(/Hawaiʻi\s*9\.8%\s*± 0\.4 points/);
  await expect(chart.locator('.chart-note')).toHaveText('Bars show the estimate. Lines show the 90% margin of error.');

  // Counts stay tiles, never bars against the county or state: one tile names the district and lists the county
  // and state under it. An unreliable one carries a caution chip, an apportioned one says how it was estimated, and
  // the district's caveat follows the answer with its sources.
  await expect(answer.getByRole('figure', {name: 'Kūpuna (65 and over)'})).toHaveCount(0);
  const kupuna = answer.locator('.tile').filter({hasText: 'Kūpuna (65 and over)'});
  await expect(kupuna).toHaveCount(1);
  await expect(kupuna.locator('.tile-meta')).toHaveText('House District 22, ± 600, 11.1% of the Hawaiʻi total');
  await expect(kupuna.locator('.tile-place-name')).toHaveText(['Honolulu County', 'Hawaiʻi']);
  await expect(kupuna.locator('.chip.caution')).toHaveText('Use with caution. The margin of error is large compared with the estimate.');
  const eitc = answer.locator('.tile').filter({hasText: 'Tax returns claiming the EITC'});
  await expect(eitc.locator('.method')).toContainText('How estimated: apportioned to the district from ZIP code or census tract data');
  await expect(eitc.locator('.method a')).toHaveAttribute('href', /\/methods\.html#irs_soi_zip_2023$/);
  await expect(answer.locator('.source-line')).toContainText('Use with caution marks an estimate with a large margin of error.');
  const note = answer.locator('.answer-notes .place-note');
  await expect(note.locator('.place-note-text')).toContainText('Kalawao County. Kalawao County (Kalaupapa) is very small');
  await expect(note.getByRole('link', {name: /2020 Census, table P1, Kalawao County/})).toHaveCount(1);
  // The profile flags the same estimate and states the caveat near its top.
  await expect(profile.locator('.profile-notes')).toContainText('Kalawao County (Kalaupapa) is very small');
  // The caution sits in the row of notes right under the estimate's own row.
  const notes = profile.locator('.profile-row:has-text("Kūpuna (65 and over)") + .profile-more').first();
  await expect(notes.locator('.profile-caution')).toHaveText('Use with caution The margin of error is large compared with the estimate.');

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
  await showTab(page, 'Ask');
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
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('How does poverty here compare?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await page.getByLabel('Highlight a ZIP code').focus();
  release();
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.getByLabel('Highlight a ZIP code')).toBeFocused();
});

test('a district chosen while an answer loads shows its profile at once', async ({page}) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/chat', async route => { await held; await route.continue(); });
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('How does poverty here compare?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await page.getByRole('button', {name: 'Change district'}).click();
  await page.locator('#place').fill('House 23');
  await page.locator('#lookup').getByRole('button', {name: 'Find district'}).click();
  await expect(page.locator('#profile-live')).toHaveText('Profile for House District 23 loaded.');
  // The page heading follows the profile at once; the chat keeps the district it is answering about.
  await expect(page.getByRole('heading', {level: 1})).toHaveText('House District 23');
  await expect(page.locator('#chat-status')).toHaveText('Switching to House District 23 after this answer.');
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  release();
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.locator('#district-title')).toHaveText('House District 23');
  // The switch keeps the profile already shown instead of loading it again.
  await expect(page.locator('#profile-live')).toHaveText('Profile for House District 23 loaded.');
  await expect(page.locator('#profile .profile-status')).toHaveCount(0);
});

// Every section is one click from the top of the profile and ends with a link back up. On wide screens the first
// sections start open. Sections the reader opens or closes stay that way for the next profile in the same tab.
test('each section is one click from the top, links back up, and stays as the reader left it', async ({page}) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  const wide = page.viewportSize().width >= 1200, profile = page.locator('#profile');
  await expect(profile.locator('details.profile-section[open] > summary')).toHaveText(wide ? ['People', 'Income', 'Programs to explore here'] : []);

  const section = profile.locator('#profile-help-sites');
  await expect(section).not.toHaveAttribute('open');
  await profile.getByRole('navigation', {name: 'In this profile'}).getByRole('link', {name: 'Help sites'}).click();
  await expect(section).toHaveAttribute('open', '');
  await expect(page).toHaveURL(/\/district\/house-22#profile-help-sites$/);
  await expect(section.locator(':scope > summary')).toBeInViewport();
  await section.getByRole('link', {name: 'Back to top of profile'}).click();
  await expect(page.getByRole('heading', {level: 1})).toBeInViewport();
  // A section link is not a new district, so Back stays on this one.
  await page.goBack();
  await expect(page.getByRole('heading', {level: 1})).toHaveText(DISTRICT);

  // Fold Income the other way from how it started; the next district's profile keeps that, and leaves People as it was.
  const income = profile.locator('#profile-income'), wasOpen = await income.evaluate(d => d.open);
  await income.locator(':scope > summary').click();
  await page.goto('/district/house-23');
  await expect(page.locator('#profile-live')).toHaveText('Profile for House District 23 loaded.');
  await expect(profile.locator('#profile-income')).toHaveJSProperty('open', !wasOpen);
  await expect(profile.locator('#profile-people')).toHaveJSProperty('open', wide);

  // A link to a section opens the profile at that section.
  await page.goto('/district/house-22#profile-programs-in-use');
  await expect(profile.locator('#profile-programs-in-use')).toHaveAttribute('open', '');
  await expect(profile.locator('#profile-programs-in-use > summary')).toBeInViewport();
});
