// Sharing a district: its own address with Back, Forward and refresh, Copy link, a one-page printout and a CSV download.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

const address = page => new URL('/district/house-22', page.url()).href;

test('each district has its own address, which Back, Forward and a pasted link follow', async ({page, context}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page).toHaveTitle(`${DISTRICT} – KLEAR`);

  // Another district, chosen from the chat's own search, adds another address; a section link within the same
  // district changes nothing.
  await showTab(page, 'Ask');
  await page.getByRole('button', {name: 'Change district'}).click();
  await page.locator('#place').fill('House 23');
  await page.locator('#lookup').getByRole('button', {name: 'Find district'}).click();
  await expect(page).toHaveURL(/\/district\/house-23$/);
  await page.getByRole('navigation', {name: 'Main navigation'}).getByRole('link', {name: 'Map'}).click();
  await expect(page).toHaveURL(/\/district\/house-23#district-map$/);
  await page.goBack();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
  await page.goBack();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);

  // Back to the home page: its photo band, heading and starter questions return in place of the district's page.
  await page.goBack();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#district-title')).toHaveText('No district chosen yet');
  await expect(page.locator('.band')).toBeVisible();
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Hawaiʻi legislative district data');
  await expect(page.locator('#profile')).toBeHidden();
  await expect(page.locator('#starter').getByRole('button', {name: 'Give me a socioeconomic profile of this district.'})).toBeVisible();
  await expect(page).toHaveTitle('KLEAR: Hawaiʻi legislative district data');

  await page.goForward();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.locator('#starter')).toBeHidden();

  // A pasted link opens the same district in a new tab, at the top of its page, with its name as the page heading.
  const pasted = await context.newPage();
  await pasted.goto('/district/house-22');
  await expect(pasted.locator('#district-title')).toHaveText(DISTRICT);
  await expect(pasted.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(pasted.getByRole('heading', {level: 1})).toHaveText(DISTRICT);
  await expect(pasted.locator('.band')).toBeHidden();
  await expect(pasted.locator('#profile .profile-stats')).toBeInViewport();
  expect(await pasted.evaluate(() => scrollY)).toBe(0);

  // A link to a district that does not exist lands on the page with no district and says why.
  await pasted.goto('/district/house-99');
  await expect(pasted).toHaveURL(/:\d+\/$/);
  await expect(pasted.locator('#find-status')).toContainText('51 House and 25 Senate districts');
});

test('a refresh keeps the district and its answers', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await ask(page);
  await page.reload();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.locator('#log .user')).toHaveText('You asked: How does poverty here compare with the county and state?');
  const answer = page.locator('#log article');
  await expect(answer).toHaveCount(1);
  await expect(answer.getByRole('figure', {name: 'People below the poverty level'})).toBeVisible();
  // The restored conversation carries on.
  await ask(page, 'How many kūpuna live here?');
  await expect(page.locator('#log article')).toHaveCount(2);
});

test('a question still loading, or failed, when the page reloads waits in the box', async ({page}) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  // The answer never arrives; the reload cancels the request. A reload keeps the Ask tab open.
  await page.route('**/api/chat', () => {});
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .pending')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Your question')).toHaveValue('How many kūpuna live here?');
  await expect(page.locator('#log .user')).toHaveCount(0);

  // A failed answer shows its error until the reload, which keeps only the question.
  await page.route('**/api/chat', route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error": "KLEAR could not answer. Please try again."}'}));
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .error p')).toHaveText('KLEAR could not answer. Please try again.');
  await page.reload();
  await expect(page.getByLabel('Your question')).toHaveValue('How many kūpuna live here?');
  await expect(page.locator('#log .error, #log .user')).toHaveCount(0);
});

test('Copy link copies the district’s address and says so', async ({page, context, browserName}) => {
  test.skip(browserName !== 'chromium', 'Reading the clipboard back needs Chromium permissions');
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await chooseDistrict(page);
  await page.getByRole('group', {name: 'Share this profile'}).getByRole('button', {name: 'Copy link'}).click();
  await expect(page.locator('#profile .profile-share-status')).toHaveText('Link copied.');
  await expect(page.locator('#profile-live')).toHaveText('Link copied.');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(address(page));
});

test('printing gives a one-page summary with every source, year and address written out', async ({page}, testInfo) => {
  await page.goto('/');
  // Before a district is chosen, the page prints as it is.
  await page.emulateMedia({media: 'print'});
  await expect(page.locator('#print-sheet')).toBeHidden();
  await expect(page.locator('main')).toBeVisible();
  await page.emulateMedia({media: null});

  await chooseDistrict(page);
  await expect(page.getByRole('group', {name: 'Share this profile'}).getByRole('button', {name: 'Print or save as PDF'})).toBeVisible();
  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet).toBeVisible();
  await expect(page.locator('main')).toBeHidden();
  await expect(sheet.getByRole('heading', {level: 1})).toHaveText(DISTRICT);

  // Each figure beside the county and the state, with its margin of error, grouped, and with the sources it uses.
  await expect(sheet).toHaveAttribute('data-chamber', 'house');
  const table = sheet.getByRole('table');
  await expect(table.locator('thead th')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi', 'Source']);
  await expect(table.getByRole('columnheader', {name: 'Income and poverty'})).toBeVisible();
  await expect(table.getByRole('row', {name: /People below the poverty level/})).toHaveText(
    /^People below the poverty level\s*12\.3%\s*± 2\.1 points\s*8\.9%\s*± 0\.5 points\s*9\.8%\s*± 0\.4 points\s*1$/);
  await expect(sheet.locator('.sheet-note').first()).toContainText('± is the 90% margin of error.');
  await expect(sheet.locator('.sheet-about')).toContainText('Shares of 2020 Census residents (source 2).');

  // Sources with their vintages, then the programs and the live profile, all as written-out addresses.
  const sources = sheet.locator('.sheet-sources li');
  await expect(sources.first()).toHaveText('American Community Survey 5-year, 2020-2024 https://example.org');
  await expect(sources.last()).toContainText('2020 Census Redistricting Data (P.L. 94-171), population by block, 2020 Census');
  await expect(sources.last()).toContainText('https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html');
  // No need figure in the fixture district sits above the state's, so the statewide programs print, six at most.
  const programs = sheet.locator('.sheet-programs');
  await expect(programs.locator('li')).toHaveCount(6);
  await expect(programs).toContainText('SNAP (State): Monthly food benefits on an EBT card');
  await expect(programs).toContainText('https://humanservices.hawaii.gov/bessd/snap/');
  await expect(programs.locator('.sheet-note')).toContainText('None of this district’s need figures sit clearly above the state’s');
  await expect(programs.locator('.sheet-note')).toContainText('The live profile lists every program.');
  await expect(sheet.locator('.sheet-live')).toContainText(address(page));
  await expect(sheet.locator('.sheet-live')).toContainText(/Printed \w+ \d+, \d{4}/);
  await expect(sheet.locator('.sheet-foot')).toContainText('Not an official State of Hawaiʻi product.');
  await expect(sheet.locator('a')).toHaveCount(0);

  // One US Letter page. Only desktop Chromium makes PDFs.
  if (testInfo.project.name === 'desktop') {
    const pdf = await page.pdf({preferCSSPageSize: true});
    expect(pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g)).toHaveLength(1);
  }
  await page.emulateMedia({media: null});
});

test('the profile’s figures download as a CSV file', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const link = page.getByRole('group', {name: 'Share this profile'}).getByRole('link', {name: 'Download data (CSV)'});
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  expect(download.suggestedFilename()).toBe('klear-house-district-22.csv');
  const csv = await (await page.request.get(await link.getAttribute('href'))).text();
  expect(csv.split('\r\n')[0]).toBe('﻿Section,Measure,Geography,Estimate,Margin of error (90%),Range low,Range high,Unit,Source,Vintage,Source URL,Note');
  expect(csv).toContain('Income,Percent of people below the poverty level,House District 22,12.3,2.1,,,percent,American Community Survey 5-year,2020-2024,https://example.org,');
});

// Rewrite the profile the server sends, to stand in for an older server or an unexpected reply.
async function editProfile(page, edit) {
  await page.unrouteAll();
  await page.route('**/api/profile?*', async route => {
    const response = await route.fetch();
    const profile = await response.json();
    edit(profile);
    await route.fulfill({response, json: profile});
  });
}

test('the printout drops programs from the end until it fits on one page', async ({page}, testInfo) => {
  // Long program text that would run the summary onto a second page at six programs.
  await editProfile(page, p => {
    for (const g of [...p.programs.programs, ...p.programs.others]) g.description = `${g.description} `.repeat(6).trim();
  });
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.emulateMedia({media: 'print'});
  const programs = page.locator('#print-sheet .sheet-programs');
  const count = await programs.locator('li').count();
  expect(count).toBeGreaterThan(0);
  expect(count).toBeLessThan(6);
  await expect(programs.locator('li').first()).toContainText(/\((State|Federal)\)/);
  await expect(programs.locator('.sheet-note')).toContainText('The live profile lists every program.');
  if (testInfo.project.name === 'desktop') {
    const pdf = await page.pdf({preferCSSPageSize: true});
    expect(pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g)).toHaveLength(1);
  }
  await page.emulateMedia({media: null});
});

test('the profile and its printout cope with missing or unexpected fields', async ({page}) => {
  // Without programs, a build date or comparison sources, the summary still prints every figure it has.
  await editProfile(page, p => {
    delete p.programs;
    delete p.built;
    delete p.shares_source;
    for (const s of p.sections) for (const r of s.rows) for (const a of r.against) delete a.source;
  });
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet.getByRole('row', {name: /People below the poverty level/})).toContainText('12.3%');
  await expect(sheet.locator('.sheet-programs')).toHaveCount(0);
  await expect(sheet.locator('.sheet-sources li')).toHaveCount(1);
  await expect(sheet.locator('.sheet-about')).toContainText('Shares of 2020 Census residents.');
  await page.emulateMedia({media: null});

  // A profile the summary cannot lay out still shows on screen, and the page then prints as it is.
  await editProfile(page, p => { p.geo = 42; });
  await page.reload();
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.getByRole('group', {name: 'Share this profile'})).toBeVisible();
  await page.emulateMedia({media: 'print'});
  await expect(page.locator('#print-sheet')).toBeHidden();
  await expect(page.locator('main')).toBeVisible();
  await page.emulateMedia({media: null});
});

test('a saved conversation that cannot be read is skipped, and the page still works', async ({page}) => {
  await page.goto('/');
  for (const saved of ['{not json', JSON.stringify([null, 5, 'text', {geo: 'house:022', name: DISTRICT, question: 'Broken?', answer: {lead: 'not a list'}}])]) {
    await page.evaluate(value => sessionStorage.setItem('klear-chat', value), saved);
    await page.goto('/district/house-22');
    await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
    await expect(page.locator('#log').locator('.user, article, .error')).toHaveCount(0);
  }
  await ask(page);
  await expect(page.locator('#log article')).toHaveCount(1);
});

test('a district address written another way is shown the usual way', async ({page}) => {
  await page.goto('/district/house-07');
  await expect(page).toHaveURL(/\/district\/house-7$/);
  await expect(page.locator('#district-title')).toHaveText('House District 7');
});
