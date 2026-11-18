// Sharing a district: its own address with Back, Forward and refresh, Copy link, printing what the reader chooses, and a
// CSV download.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

const address = page => new URL('/district/house-22', page.url()).href;

test('each district has its own address, which Back, Forward and a pasted link follow', async ({page, context, isMobile}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page).toHaveTitle(`${DISTRICT} – KLEAR`);

  // Back to the Find page: its heading, search, numbers and question box return in place of the district's page, and
  // the district's suggestions leave the agent.
  await page.goBack();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#district-title')).toHaveText('a district');
  await expect(page.locator('#find-col')).toBeVisible();
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Find your district');
  await expect(page.locator('#profile')).toBeHidden();
  await expect(page.locator('#home-starters').getByRole('button', {name: 'How many kūpuna live in Kāneʻohe?'})).toBeVisible();
  await expect(page.locator('#suggest')).toBeHidden();
  await expect(page).toHaveTitle('KLEAR: Hawaiʻi legislative district data');

  // Another district, opened by its number, adds another address; a link to a part of its page changes nothing.
  await page.getByRole('navigation', {name: 'Every district by number'}).getByRole('link', {name: 'House District 23', exact: true}).click();
  await expect(page).toHaveURL(/\/district\/house-23$/);
  await showTab(page, 'Profile');
  await page.locator('#profile').getByRole('navigation', {name: 'In this profile'}).getByRole('link', {name: 'People'}).click();
  await expect(page).toHaveURL(/\/district\/house-23#profile-people$/);
  await page.goBack();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
  await page.goBack();
  await expect(page).toHaveURL(/:\d+\/$/);
  await expect(page.locator('#find-col')).toBeVisible();
  await page.goForward();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
  await expect(page.locator('#profile-live')).toHaveText('Profile for House District 23 loaded.');
  await expect(page.locator('#find-col')).toBeHidden();

  // A pasted link opens the same district in a new tab, at the top of its page, with its name as the page heading.
  const pasted = await context.newPage();
  await pasted.goto('/district/house-22');
  await expect(pasted.locator('#district-title')).toHaveText(DISTRICT);
  await expect(pasted.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(pasted.getByRole('heading', {level: 1})).toHaveText(DISTRICT);
  await expect(pasted.locator('#find-col')).toBeHidden();
  if (!isMobile) await expect(pasted.locator('#profile .profile-stats')).toBeInViewport();
  expect(await pasted.evaluate(() => scrollY)).toBe(0);

  // A bad district link returns 404 and lands on Find with the chamber still in sync.
  await pasted.goto('/');
  // On a phone the switch over the numbers is the one that shows; the map's own shows beside the map on wide screens.
  const senate = pasted.locator(isMobile ? '#find-col .find-switch' : '#chamber-switch').getByRole('button', {name: 'Senate'});
  await senate.click();
  const missing = await pasted.goto('/district/house-99');
  expect(missing.status()).toBe(404);
  await expect(pasted).toHaveURL(/:\d+\/$/);
  await expect(pasted.locator('#find-status')).toContainText('51 House and 25 Senate districts');
  await expect(senate).toHaveAttribute('aria-pressed', 'true');
  await expect(pasted.locator('#seat-index .seat-chamber.senate')).toBeVisible();
  await expect(pasted.locator('#seat-index .seat-chamber.house')).toBeHidden();
  await expect(pasted.locator('#map .frame-caption').first()).toHaveText(/Senate$/);
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
  await page.locator('#send').click();
  await expect(page.locator('#log .pending')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Your question')).toHaveValue('How many kūpuna live here?');
  await expect(page.locator('#log .user')).toHaveCount(0);

  // A failed answer shows its error until the reload, which keeps only the question.
  await page.route('**/api/chat', route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error": "KLEAR could not answer. Please try again."}'}));
  await page.locator('#send').click();
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
  await page.getByRole('group', {name: 'Share'}).getByRole('button', {name: 'Copy link'}).click();
  await expect(page.locator('#share-status')).toHaveText('Link copied.');
  await expect(page.locator('#profile-live')).toHaveText('Link copied.');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(address(page));
});

test('printing gives the short brief by default, with every source, year and address written out', async ({page}) => {
  await page.goto('/');
  // Before a district is chosen, the page prints as it is.
  await page.emulateMedia({media: 'print'});
  await expect(page.locator('#print-sheet')).toBeHidden();
  await expect(page.locator('main')).toBeVisible();
  await page.emulateMedia({media: null});

  await chooseDistrict(page);
  await expect(page.getByRole('group', {name: 'Share'}).getByRole('button', {name: 'Print or save as PDF'})).toBeVisible();
  // The browser's own Print prints the short brief: the district's name and legislator, At a glance, and Programs and help.
  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet).toBeVisible();
  await expect(page.locator('main')).toBeHidden();
  await expect(sheet.getByRole('heading', {level: 1})).toHaveText(DISTRICT);
  await expect(sheet.getByRole('heading', {level: 2})).toHaveText(['1 At a glance', 'About this district’s figures', '4 Programs and help', 'Sources']);
  await expect(sheet.locator('.sheet-member')).toContainText('Representative Leilani M. Kaʻai https://example.org/member/house022');

  // Each figure beside the county and the state, with its margin of error, how it compares, and the sources it uses.
  await expect(sheet).toHaveAttribute('data-chamber', 'house');
  const glance = sheet.locator('.sheet-glance').getByRole('table');
  await expect(glance.locator('thead th')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi', 'How it compares', 'Source']);
  await expect(glance.getByRole('row', {name: /People below the poverty level/})).toHaveText(
    /^People below the poverty level\s*12\.3%\s*± 2\.1 points\s*8\.9%\s*± 0\.5 points\s*9\.8%\s*± 0\.4 points\s*▲ 3\.4 points above county\s*≈ No clear difference \(state\)\s*1$/);
  await expect(sheet.locator('.sheet-note').first()).toContainText('± is the 90% margin of error.');
  await expect(sheet.locator('.sheet-about')).toContainText(/Shares of 2020 Census residents \(source \d\)\./);

  // Every program, by need group, written as on the profile, with its address and phone: more than the one-page
  // summary's five.
  const programs = sheet.locator('.sheet-program-list');
  expect(await programs.locator('li').count()).toBeGreaterThan(5);
  await expect(programs.locator('.sheet-program-group').first()).toHaveText(/^Housing\. .+ statewide\.$/);
  await expect(programs).toContainText('Public housing and housing choice vouchers (State): Rental homes in state and federal public housing');
  await expect(programs).toContainText('For Households with low income');
  await expect(programs).toContainText('Limit Income at or below 50% of the county median, in effect from January 1, 2026.');
  await expect(programs).toContainText('https://hpha.hawaii.gov/how-to-apply · Call (808) 204-9042');
  await expect(programs.locator('.sheet-note')).toContainText(/Program details checked \w+ \d+/);

  // Sources with their vintages, the live profile's address, and the date it was printed, all written out.
  const sources = sheet.locator('.sheet-sources li');
  await expect(sources.first()).toHaveText('American Community Survey 5-year, 2020-2024 https://example.org');
  await expect(sheet.locator('.sheet-sources')).toContainText('2020 Census Redistricting Data (P.L. 94-171), population by block, 2020 Census');
  await expect(sheet.locator('.sheet-live')).toContainText(address(page));
  await expect(sheet.locator('.sheet-live')).toContainText(/Printed \w+ \d+, \d{4}/);
  await expect(sheet.locator('.sheet-foot')).toContainText('Not an official State of Hawaiʻi product.');
  await expect(sheet.locator('a')).toHaveCount(0);
  await page.emulateMedia({media: null});
});

// Print from the Print button, as a reader does: the dialog asks what to print, and the browser's print dialog is stood
// in for by its beforeprint event.
async function openPrint(page) {
  await page.emulateMedia({media: 'screen'});
  await page.evaluate(() => { window.print = () => { window.printed = (window.printed || 0) + 1; window.dispatchEvent(new Event('beforeprint')); }; });
  await page.getByRole('group', {name: 'Share'}).getByRole('button', {name: 'Print or save as PDF'}).click();
  const dialog = page.getByRole('dialog', {name: /^Print /});
  await expect(dialog).toBeVisible();
  return dialog;
}
async function printNow(page, dialog) {
  await dialog.getByRole('button', {name: 'Print', exact: true}).click();
  await expect(dialog).toBeHidden();
  await expect.poll(() => page.evaluate(() => window.printed || 0)).toBeGreaterThan(0);
  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet).toBeVisible();
  return sheet;
}

test('the print dialog asks what to print, and a long printout runs over several pages', async ({page}, testInfo) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  let dialog = await openPrint(page);
  await expect(dialog.getByRole('heading', {level: 2})).toHaveText(`Print ${DISTRICT}`);
  const parts = dialog.getByRole('group', {name: 'Sections'}).getByRole('checkbox');
  await expect(parts).toHaveCount(5);
  await expect(dialog.getByRole('checkbox', {name: '1 At a glance'})).toBeChecked();
  await expect(dialog.getByRole('checkbox', {name: '2 People'})).not.toBeChecked();
  await expect(dialog.getByRole('checkbox', {name: '4 Programs and help'})).toBeChecked();
  await expect(dialog.getByRole('checkbox', {name: 'County and state columns'})).toBeChecked();
  await expect(dialog.getByRole('checkbox', {name: /One-page summary/})).not.toBeChecked();

  // Nothing chosen: the dialog says so and stays open.
  await dialog.getByRole('button', {name: 'Clear'}).click();
  await dialog.getByRole('button', {name: 'Print', exact: true}).click();
  await expect(dialog.getByRole('alert')).toHaveText('Choose at least one section to print.');
  await expect(dialog).toBeVisible();

  // Everything, with the district's figures alone: every section, in the profile's numbers, one column of figures.
  await dialog.getByRole('button', {name: 'Select all'}).click();
  await dialog.getByRole('checkbox', {name: 'County and state columns'}).uncheck();
  let sheet = await printNow(page, dialog);
  await expect(sheet.locator('.sheet-section > h2')).toHaveText(['1 At a glance', 'About this district’s figures', '2 People', '3 Income and work', '4 Programs and help', '5 Reference', 'Sources']);
  await expect(sheet.locator('.sheet-glance thead th')).toHaveText(['Measure', DISTRICT, 'Source']);
  await expect(sheet.locator('.sheet-plan-box')).toContainText('Official description The 22nd Representative District comprises the portion of the island of Oʻahu including Manoa.');
  // The choice holds for the next printout, and for the browser's own Print.
  await page.emulateMedia({media: 'screen'});
  dialog = await openPrint(page);
  await expect(dialog.getByRole('checkbox', {name: '5 Reference'})).toBeChecked();
  await expect(dialog.getByRole('checkbox', {name: 'County and state columns'})).not.toBeChecked();
  await dialog.getByRole('button', {name: 'Cancel'}).click();
  await expect(dialog).toBeHidden();

  // Long program text runs the printout onto more pages; nothing is cut to fit.
  await editProfile(page, p => {
    for (const g of [...p.programs.programs, ...p.programs.others]) g.description = `${g.description} `.repeat(12).trim();
  });
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  dialog = await openPrint(page);
  sheet = await printNow(page, dialog);
  expect(await sheet.locator('.sheet-program-list li').count()).toBeGreaterThan(5);
  expect(await sheet.evaluate(s => s.style.zoom)).toBe('');
  if (testInfo.project.name === 'desktop') {
    const pdf = await page.pdf({preferCSSPageSize: true});
    expect(pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g).length).toBeGreaterThan(1);
  }
  await page.emulateMedia({media: null});
});

test('the profile’s figures download as a CSV file', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const link = page.getByRole('group', {name: 'Share'}).getByRole('link', {name: 'Download data (CSV)'});
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  expect(download.suggestedFilename()).toBe('klear-house-district-22.csv');
  const csv = await (await page.request.get(await link.getAttribute('href'))).text();
  expect(csv.split('\r\n')[0]).toBe('﻿Section,Measure,Geography,Estimate,Margin of error (90%),Range low,Range high,Unit,Source,Vintage,Source URL,Note');
  expect(csv).toContain('Income,Percent of people below the poverty level,House District 22,12.3,2.1,,,percent,American Community Survey 5-year,2020-2024,https://example.org,');
});

test('the profile gives the official description and permanent residents with their report pages', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  // The report's part closes the Reference group, after the figures; that group's link at the top of the profile opens it.
  await page.locator('#profile').getByRole('navigation', {name: 'In this profile'}).getByRole('link', {name: 'Reference'}).click();
  const plan = page.locator('.profile-plan');
  await expect(plan).toBeVisible();
  await expect(plan).toContainText('Official description: The 22nd Representative District comprises the portion of the island of Oʻahu including Manoa. (report page D-017, PDF page 891)');
  await expect(plan).toContainText('Permanent residents in the 2022 plan: 26,451 (report page 15, PDF page 23)');
  await expect(plan).toContainText('So it is not the same as the total population above');
  await expect(plan.getByRole('link', {name: /2021 Reapportionment Commission Final Report/})).toHaveAttribute('href', 'https://example.org/reapportionment');
  // A count the build left out is not shown; the description still is.
  await page.goto('/district/house-23');
  await expect(page.locator('.profile-plan')).toContainText('The 23rd Representative District');
  await expect(page.locator('.profile-plan')).not.toContainText('Permanent residents');
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

// The one-page summary: key figures beside the county and the state, the top programs, and every source, on one US
// Letter page.
async function printSummary(page) {
  const dialog = await openPrint(page);
  await dialog.getByRole('checkbox', {name: /One-page summary/}).check();
  await expect(dialog.getByRole('checkbox', {name: '1 At a glance'})).toBeDisabled();
  return printNow(page, dialog);
}

test('the one-page summary drops programs from the end until it fits on one page', async ({page}, testInfo) => {
  // Long program text that would run the summary onto a second page at six programs.
  await editProfile(page, p => {
    for (const g of [...p.programs.programs, ...p.programs.others]) g.description = `${g.description} `.repeat(6).trim();
  });
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  const sheet = await printSummary(page);
  await expect(sheet).toHaveClass(/\bone-page\b/);
  const programs = sheet.locator('.sheet-programs');
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

test('a one-page summary keeps its full size when it fits, and a long one still fits one page', async ({page}, testInfo) => {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  let sheet = await printSummary(page);
  // The short fixture district prints four of its five programs at the full type size, unscaled: its largest industry
  // row takes the fifth's room.
  await expect(sheet.locator('.sheet-programs li')).toHaveCount(4);
  await expect(sheet.locator('.sheet-key thead th')).toHaveText(['Measure', DISTRICT, 'Honolulu County', 'Hawaiʻi', 'Source']);
  expect(await sheet.evaluate(s => s.style.zoom)).toBe('');
  expect(parseFloat(await sheet.evaluate(s => getComputedStyle(s).fontSize))).toBeCloseTo((9.5 * 4) / 3, 2);
  // One US Letter page. Only desktop Chromium makes PDFs, and only a PDF lays the sheet out at the page's width.
  if (testInfo.project.name === 'desktop') {
    const pdf = await page.pdf({preferCSSPageSize: true});
    expect(pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g)).toHaveLength(1);
  }

  // Long caveats and program text: the summary lists fewer programs or scales down, and still prints on one page.
  await editProfile(page, p => {
    for (const g of [...p.programs.programs, ...p.programs.others]) g.description = `${g.description} `.repeat(12).trim();
    p.caveats = Array.from({length: 6}, (_, i) => ({id: `long-${i}`, title: `Note ${i + 1}`, text: 'A long note about these figures. '.repeat(8).trim(), sources: []}));
  });
  await page.emulateMedia({media: 'screen'});
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  sheet = await printSummary(page);
  const count = await sheet.locator('.sheet-programs li').count();
  const zoom = await sheet.evaluate(s => s.style.zoom);
  expect(count < 5 || zoom !== '').toBe(true);
  expect(count).toBeGreaterThanOrEqual(3);
  if (testInfo.project.name === 'desktop') {
    const pdf = await page.pdf({preferCSSPageSize: true});
    expect(pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g)).toHaveLength(1);
  }
  await page.emulateMedia({media: null});
});

test('a comparison prints the two districts side by side', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  const dialog = await openPrint(page);
  await expect(dialog.getByRole('heading', {level: 2})).toHaveText(`Print ${DISTRICT} and House District 23`);
  await expect(dialog.getByRole('checkbox', {name: /One-page summary/})).toBeHidden();
  await expect(dialog.getByRole('checkbox', {name: 'Hawaiʻi’s figures beside the districts'})).toBeChecked();
  await dialog.getByRole('checkbox', {name: '2 People'}).check();
  const sheet = await printNow(page, dialog);
  await expect(sheet.getByRole('heading', {level: 1})).toHaveText(`${DISTRICT} and House District 23`);
  await expect(sheet.locator('.sheet-glance thead th')).toHaveText(['Measure', DISTRICT, 'House District 23', 'Hawaiʻi', 'Difference', 'Source']);
  await expect(sheet.locator('.sheet-glance').getByRole('row', {name: /People below the poverty level/})).toContainText(`▲ 4.4 points higher in ${DISTRICT}`);
  await expect(sheet.locator('.sheet-section > h2')).toHaveText(['1 At a glance', '2 People', '4 Programs and help', 'Sources']);
  await expect(sheet.locator('.sheet-live')).toContainText('/compare?d=house:022,house:023');
  await page.emulateMedia({media: null});
});

test('a comparison prints the same aligned rows as the page, and county rows say whose they are', async ({page}) => {
  await page.goto('/compare?d=house:022,house:023');
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  const dialog = await openPrint(page);
  await dialog.getByRole('button', {name: 'Select all'}).click();
  const sheet = await printNow(page, dialog);
  // Every industry either profile shows, with a figure for both districts and for Hawaiʻi.
  for (const label of ['Work in retail trade', 'Work in government \\(public administration\\)', 'Work in professional or business services', 'Work in hotels, restaurants or recreation']) {
    await expect(sheet.getByRole('row', {name: new RegExp(label)}).locator('td.num')).toHaveCount(3);
  }
  await expect(sheet.getByRole('row', {name: /Unemployment rate/}).last()).toContainText('Same Honolulu County figure for both');
  await expect(sheet).toContainText('County context: These figures cover all of Honolulu County');
  await page.emulateMedia({media: null});
});

test('the profile and its printout cope with missing or unexpected fields', async ({page}) => {
  // Without programs, a build date, the Commission's report or comparison sources, the summary still prints every
  // figure it has.
  await editProfile(page, p => {
    delete p.programs;
    delete p.plan;
    delete p.built;
    delete p.shares_source;
    for (const s of p.sections) for (const r of s.rows) for (const a of r.against) delete a.source;
  });
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet.getByRole('row', {name: /People below the poverty level/}).first()).toContainText('12.3%');
  await expect(sheet.locator('.sheet-program-list')).toHaveCount(0);
  await expect(sheet.locator('.sheet-sources li')).toHaveCount(2);
  await expect(sheet.locator('.sheet-about')).toContainText('Shares of 2020 Census residents.');
  await page.emulateMedia({media: null});

  // A profile the summary cannot lay out still shows on screen, and the page then prints as it is.
  await editProfile(page, p => { p.geo = 42; });
  await page.reload();
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.getByRole('group', {name: 'Share'})).toBeVisible();
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
