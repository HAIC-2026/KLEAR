// The outreach page: a program's income-eligible group across House or Senate districts, in groups whose ranges
// overlap, and the outreach note on a district profile. The fixture store has the SNAP pair for two House districts.
import {test, expect} from '@playwright/test';
import {chooseDistrict, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

const LEAD = 'House District 22 has the most households in poverty that did not get SNAP of any House district: 400 ± 60.';

test('the outreach page names the top group two clicks from the home page', async ({page}) => {
  await page.goto('/');
  await page.getByRole('navigation', {name: 'Main navigation'}).getByRole('link', {name: 'Outreach'}).click();
  await expect(page).toHaveURL(/\/outreach\.html\?pair=snap&chamber=house$/);
  await expect(page.locator('#result-title')).toHaveText('Households in poverty without SNAP, by House district');
  await expect(page.locator('.outreach-lead')).toHaveText(LEAD);
  await expect(page.locator('.outreach-year')).toContainText('± is the 90% margin of error');

  // The top group links each district to its profile, with its figure and the SNAP figure beside it.
  const top = page.locator('.tier.top');
  await expect(top.getByRole('heading')).toHaveText('Top group: 1 district');
  const row = top.getByRole('row', {name: /House District 22/});
  await expect(row.getByRole('link', {name: 'House District 22'})).toHaveAttribute('href', /\/district\/house-22$/);
  await expect(row).toContainText('400 ± 60');
  await expect(row).toContainText('900 ± 120');

  // An unreliable estimate says so in words.
  await page.locator('details.tier summary', {hasText: 'Second group: 1 district'}).click();
  await expect(page.locator('details.tier').first()).toContainText('Unreliable estimate: the margin of error is large');
  await expect(page.locator('.outreach-facts')).toContainText('1 of these 2 estimates is unreliable');

  // The program's page and phone, the income test caveat and the eligibility line.
  const programs = page.locator('.outreach-programs');
  await expect(programs.getByRole('link', {name: 'SNAP (opens in a new tab)'})).toHaveAttribute('href', /^https:\/\/[^/]+\.gov\//);
  await expect(programs.getByRole('link', {name: '1-855-643-1643'})).toHaveAttribute('href', 'tel:+18556431643');
  await expect(page.locator('.outreach-caveat')).toContainText('Meets the income test only.');
  await expect(page.locator('.outreach-caveat')).toContainText('cannot establish any person');

  // The second click: the Senate, whose district has no figure in the fixture store.
  await page.getByRole('radio', {name: 'Senate', exact: true}).check();
  await expect(page).toHaveURL(/\?pair=snap&chamber=senate$/);
  await expect(page.locator('#live')).toHaveText('Showing Households in poverty without SNAP, by Senate district.');
  await expect(page.locator('.outreach-lead')).toHaveText('No Senate district has a figure for households in poverty that did not get SNAP.');
});

test('a link opens the program and chamber it names', async ({page}) => {
  await page.goto('/outreach.html?pair=snap&chamber=house');
  await expect(page.getByRole('radio', {name: /^SNAP/})).toBeChecked();
  await expect(page.getByRole('radio', {name: 'House', exact: true})).toBeChecked();
  await expect(page.locator('.outreach-lead')).toHaveText(LEAD);
  // A program the store has no figures for says so, and the page stays usable.
  await page.getByRole('radio', {name: /^Med-QUEST/}).check();
  await expect(page.locator('#status')).toHaveText('The data store has no figures for this group yet. Try another program.');
  await expect(page.locator('#result')).toBeHidden();
  await page.getByRole('radio', {name: /^SNAP/}).check();
  await expect(page.locator('.outreach-lead')).toHaveText(LEAD);
});

test('a district profile notes where it is in the top group', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const note = page.locator('#profile .profile-outreach');
  await expect(note.getByRole('heading')).toHaveText('Outreach opportunities');
  await expect(note).toContainText(LEAD);
  const link = note.getByRole('link', {name: 'Compare House districts for SNAP (opens in a new tab)'});
  await expect(link).toHaveAttribute('href', /\/outreach\.html\?pair=snap&chamber=house$/);
  // The note sits above the profile's sections.
  const order = await page.locator('#profile > .profile-outreach, #profile > details').evaluateAll(all => all.map(n => n.className));
  expect(order[0]).toBe('profile-outreach');
});
