// The district's legislator: named in reading order on the profile and the printout, with where and when the
// Legislature listed them, and as listed in an answer.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, expectAccessible, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

const LISTED = "As listed on the Legislative Reference Bureau's Representatives at a Glance on November 12, 2026.";
const BEFORE = 'dated October 5, 2026, before the November 3, 2026 general election';

test('the profile and the printout name the member with where and when they were listed', async ({page}, testInfo) => {
  // axe runs here, which is slow on a busy machine.
  test.slow();
  await page.goto('/');
  await chooseDistrict(page);
  const member = page.locator('#district-head .profile-member');
  await expect(member.locator('.profile-member-name')).toHaveText('Representative Leilani M. Kaʻai (opens in a new tab)');
  await expect(member.getByRole('link', {name: /Leilani M\. Kaʻai/})).toHaveAttribute('href', 'https://example.org/member/house022');
  // The note on where and when the name was listed opens under the name.
  await member.getByText('Where and when this was listed').click();
  await expect(member.locator('.profile-member-note')).toBeVisible();
  await expect(member.locator('.profile-member-note')).toContainText(LISTED);
  await expect(member.locator('.profile-member-note')).toContainText(BEFORE);
  await expect(member).not.toContainText(/party|democrat|republican/i);
  await expectAccessible(page, testInfo, 'profile with member');

  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet.locator('.sheet-member')).toContainText('Representative Leilani M. Kaʻai https://example.org/member/house022');
  await expect(sheet.locator('.sheet-member-note')).toContainText(LISTED);
  await expect(sheet.locator('.sheet-member-note')).toContainText(BEFORE);
  await page.emulateMedia({media: null});
});

test('an answer about who represents the district names the member and links the listing', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const answer = await ask(page, 'Who represents this district?');
  await expect(answer).toContainText("House District 22 is represented by Representative KAʻAI, Leilani M. (as listed on the Legislative Reference Bureau's Representatives at a Glance on November 12, 2026).");
  await expect(answer.locator('.caveat')).toContainText(BEFORE);
  const links = answer.locator('.member-links');
  await expect(links.getByRole('link', {name: /official page/})).toHaveAttribute('href', 'https://example.org/member/house022');
  await expect(links.getByRole('link', {name: /Representatives at a Glance/})).toHaveAttribute('href', 'https://lrb.hawaii.gov/par/current-legislature/');
});

// A district, town or name the question gives comes before the chosen district, in the basic lookup.
test('the basic lookup answers for the district or town the question names, not the chosen one', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const other = await ask(page, 'Basic lookup: who represents House District 23?', 'House District 23');
  await expect(other).toContainText('House District 23 is represented by Representative PALAKIKO, Kawika');
  await expect(other).not.toContainText('KAʻAI');

  const town = await ask(page, 'Basic lookup: who represents Kāneʻohe?');
  await expect(town).toContainText('Kāneʻohe spans more than one district.');
  await expect(town).toContainText("House District 22, with most of Kāneʻohe's residents: Representative KAʻAI, Leilani M.");
  await expect(town).toContainText("House District 23, with part of Kāneʻohe's residents: Representative PALAKIKO, Kawika");
  await expect(town).toContainText("Senate District 12, with all of Kāneʻohe's residents: Senator KAʻAI, Noelani");
  await expect(town.locator('.member-links').getByRole('link', {name: /official page/})).toHaveCount(3);

  const wrong = await ask(page, 'Basic lookup: which district does Representative Mele Kaʻai represent?');
  await expect(wrong).toContainText('I could not find a listed member with that exact name.');
  await expect(wrong).not.toContainText('Leilani');
});
