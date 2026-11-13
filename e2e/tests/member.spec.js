// The district's legislator as the Legislature lists them: on the profile, the printout and in an answer.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, expectAccessible, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

const LISTED = "As listed on the Legislative Reference Bureau's Representatives at a Glance on November 12, 2026.";
const BEFORE = 'dated October 5, 2026, before the November 3, 2026 general election';

test('the profile and the printout name the member with where and when they were listed', async ({page}, testInfo) => {
  await page.goto('/');
  await chooseDistrict(page);
  const member = page.locator('#district-about .profile-member');
  await expect(member.locator('.profile-member-name')).toHaveText('Representative KAʻAI, Leilani M. (opens in a new tab)');
  await expect(member.getByRole('link', {name: /KAʻAI, Leilani M\./})).toHaveAttribute('href', 'https://example.org/member/house022');
  await expect(member.locator('.profile-member-note')).toContainText(LISTED);
  await expect(member.locator('.profile-member-note')).toContainText(BEFORE);
  await expect(member).not.toContainText(/party|democrat|republican/i);
  await expectAccessible(page, testInfo, 'profile with member');

  await page.emulateMedia({media: 'print'});
  const sheet = page.locator('#print-sheet');
  await expect(sheet.locator('.sheet-member')).toContainText('Representative KAʻAI, Leilani M. https://example.org/member/house022');
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
