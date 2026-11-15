// District search beyond towns: neighborhoods, islands, street addresses and numbers out of range.
import {test, expect} from '@playwright/test';
import {chooseDistrict, expectAccessible, offline, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

async function search(page, text) {
  await page.getByLabel('Town, ZIP code, street address, district or legislator').first().fill(text);
  await page.getByRole('button', {name: 'Find district'}).first().click();
  return page.locator('#find-matches');
}

test('a neighborhood names its island, says what kind of area it is, and shows each share', async ({page}) => {
  await page.goto('/');
  const matches = await search(page, 'Waikiki');
  await expect(matches.locator('.match-title')).toHaveText('Waikīkī, Oʻahu');
  await expect(matches.locator('.match-note')).toHaveText('Honolulu neighborhood board area');
  await expect(matches.getByRole('button', {name: 'House 22 100% of Waikīkī residents'})).toBeVisible();
});

test('a street address lists its districts, and the page says where the address goes', async ({page}) => {
  await page.goto('/');
  await expect(page.locator('#find .privacy')).toHaveText('Addresses go to the U.S. Census Bureau only to find the district, and are never stored.');
  const matches = await search(page, '1 Test St');
  await expect(page.locator('#find-status')).toHaveText('Choose the House or Senate district.');
  await expect(matches.locator('.match-title')).toHaveText('1 Test St, Kāneʻohe, HI 96744');
  await expect(matches.getByRole('button')).toHaveText(['House 23', 'Senate 12']);
  await expect(page.locator('#find .privacy')).toBeVisible();
  await matches.getByRole('button', {name: 'House 23'}).click();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
});

test('a street address in a chat question gets its own district, and the question box says where it goes', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  // A short note under the question box says that an AI provider gets the question; Privacy opens the rest.
  const note = page.locator('#chat-privacy');
  await expect(note).toHaveText('An AI model answers from KLEAR’s data. Questions go to its provider, Anthropic, so leave out personal details. Privacy');
  await expect(note).toBeVisible();
  const privacy = note.getByRole('button', {name: 'Privacy'}), more = page.locator('#privacy-more');
  await expect(privacy).toHaveAttribute('aria-expanded', 'false');
  await expect(more).toBeHidden();
  await privacy.click();
  await expect(privacy).toHaveAttribute('aria-expanded', 'true');
  await expect(more).toContainText('does not store or log questions');
  await expect(more.getByRole('link', {name: 'More about privacy'})).toHaveAttribute('href', '/data#privacy');
  await privacy.click();
  await expect(more).toBeHidden();
  await page.getByLabel('Your question').fill('Which House and Senate district is 1 Test St, Kāneʻohe in?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  // The status line names the district the answer is about, the address's, not the one chosen before.
  await expect(page.locator('#chat-live')).toContainText('Answer ready for House District 23.');
  const answer = page.locator('#log article').last();
  await expect(answer).toContainText('That address is in House District 23 and Senate District 12.');
  // The answer is labeled with the address's district, and so are its next questions.
  await expect(answer.locator('h3')).toHaveText('KLEAR, House District 23');
  await expect(answer.locator('.followup').nth(1)).toContainText('House District 23');
  await expect(answer.locator('.followups')).not.toContainText('House District 22');
  await answer.getByRole('button', {name: 'Switch to House District 23'}).click();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
  await expect(page.locator('#profile-live')).toHaveText('Profile for House District 23 loaded.');
  await expect(answer.getByRole('button', {name: 'Switch to House District 23'})).toHaveCount(0);
});

test('a district number out of range says how many there are', async ({page}) => {
  await page.goto('/');
  await search(page, 'House 52');
  await expect(page.locator('#find-status')).toHaveText(/^Hawaiʻi has \d+ House districts, numbered 1 to \d+\.$/);
});

// The fixture store has no place this large, so the search reply is the one an island with many districts gets.
const house = Array.from({length: 10}, (_, i) => ({geo: `house:${String(18 + i).padStart(3, '0')}`, name: `House District ${18 + i}`, share: 0.1}));
const senate = Array.from({length: 9}, (_, i) => ({geo: `senate:${String(9 + i).padStart(3, '0')}`, name: `Senate District ${9 + i}`, share: 1 / 9}));
const ISLAND = {matches: [{kind: 'island', name: 'Oʻahu', island: 'Oʻahu', pop2020: 1000, overlaps: [...house, ...senate, {geo: 'county:15003', name: 'Honolulu County', share: 1}]}]};

test('a place with many districts leads with a summary, folds its list, and shows them on the map', async ({page}, testInfo) => {
  await page.route(/\/api\/resolve\?/, route => route.fulfill({json: ISLAND}));
  await page.goto('/');
  const matches = await search(page, 'Oahu');
  await expect(matches.locator('.match-title')).toHaveText('Oʻahu');
  await expect(matches.locator('.match-note')).toHaveText('Whole island');
  await expect(matches.locator('.match-summary')).toHaveText(
    'Oʻahu spans 10 House and 9 Senate districts. Search for a neighborhood, town or ZIP code in it, or see its districts on the map.');
  const all = matches.locator('details.match-all');
  await expect(all.locator('summary')).toHaveText('All 19 districts');
  await expect(matches.getByRole('button', {name: /^House 18\b/})).toBeHidden();
  await all.locator('summary').click();
  await expect(matches.getByRole('button', {name: 'House 18 10% of Oʻahu residents'})).toBeVisible();
  await expectAccessible(page, testInfo, 'search with a folded list');

  // The search's results already light up on the map below it; the link moves to the map.
  await expect(page.locator('#zip-status')).toHaveText('Oʻahu spans 10 House districts, outlined on the map.');
  await expect(page.locator('#map .district:not(.copy).zip-hit')).toHaveCount(10);
  await matches.getByRole('link', {name: 'see its districts on the map'}).click();
  await expect(page.locator('#district-map')).toBeInViewport();
});

test('typing suggests places, and the arrow keys and Enter pick one, which searches at once', async ({page}, testInfo) => {
  await page.goto('/');
  const box = page.getByRole('combobox', {name: 'Town, ZIP code, street address, district or legislator'}).first();
  await box.fill('wai');
  const list = page.locator('#find-place-list');
  const options = list.getByRole('option');
  await expect(options).toHaveText([/^Waikīkī\s+Oʻahu · Neighborhood$/, /^Waikīkī Beach\s+Oʻahu · in Waikīkī$/]);
  await expect(box).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#find-place-count')).toHaveText('2 suggestions. Use the up and down arrows to choose.');
  await expectAccessible(page, testInfo, 'search with suggestions open');

  // Focus stays in the box while the arrow keys move through the list, wrapping at either end.
  await box.press('ArrowDown');
  await expect(box).toHaveAttribute('aria-activedescendant', 'find-place-list-0');
  await expect(options.first()).toHaveAttribute('aria-selected', 'true');
  await box.press('ArrowUp');
  await expect(options.last()).toHaveAttribute('aria-selected', 'true');
  await box.press('ArrowDown');
  await expect(box).toBeFocused();
  await box.press('Enter');
  await expect(box).toHaveValue('Waikīkī');
  await expect(list).toBeHidden();
  await expect(box).toHaveAttribute('aria-expanded', 'false');
  await expect(box).not.toHaveAttribute('aria-activedescendant');
  await expect(page.locator('#find-matches .match-title')).toHaveText('Waikīkī, Oʻahu');
});

test('Escape closes the suggestions, and a clicked one picks exactly that place', async ({page}) => {
  await page.goto('/');
  const box = page.getByRole('combobox', {name: 'Town, ZIP code, street address, district or legislator'}).first();
  await box.fill('kai');
  const list = page.locator('#find-place-list'), options = list.getByRole('option');
  await expect(options).toHaveText([/^Kailua\s+Oʻahu · Town$/, /^Kailua\s+Hawaiʻi Island · Town$/]);
  await box.press('Escape');
  await expect(list).toBeHidden();
  await expect(box).toHaveValue('kai');
  await box.press('ArrowDown');
  await expect(options).toHaveCount(2);
  // The Oʻahu Kailua lies in one House district here, so picking it chooses that district.
  await options.filter({hasText: 'Oʻahu'}).click();
  await expect(page.locator('#district-title')).toHaveText('House District 22');
});

test('the compare page’s district search suggests too, and typing one letter suggests nothing', async ({page}) => {
  await page.goto('/compare');
  const box = page.locator('#cmp-place');
  await expect(box).toHaveAttribute('role', 'combobox');
  await box.fill('o');
  await page.waitForTimeout(400);
  await expect(page.locator('#cmp-place-list')).toBeHidden();
  await box.fill('oah');
  await expect(page.locator('#cmp-place-list').getByRole('option')).toHaveText([/^Oʻahu\s+Island$/]);
  await box.press('Tab');
  await expect(page.locator('#cmp-place-list')).toBeHidden();
});

// People type the ʻokina many ways, or leave it out, and the kahakō too. Each finds the same place.
const TYPED = ['Kāneʻohe', "Kane'ohe", 'Kane`ohe', 'Kane‘ohe', 'Kane’ohe', 'Kaneʼohe', 'Kane´ohe', 'Kaneohe', "KANE'OHE"];

test('the ʻokina typed as ʻ, \', `, ‘, ’, ʼ or ´, or left out, finds the place', async ({page}) => {
  await page.goto('/');
  const box = page.getByRole('combobox', {name: 'Town, ZIP code, street address, district or legislator'}).first();
  const options = page.locator('#find-place-list').getByRole('option');
  for (const text of TYPED) {
    await box.fill(text);
    await expect(options, text).toHaveText([/^Kāneʻohe\s+Oʻahu · Town$/]);
    await page.getByRole('button', {name: 'Find district'}).first().click();
    await expect(page.locator('#find-matches').getByRole('group', {name: 'House districts for Kāneʻohe'}), text).toBeVisible();
  }
});

test('after more typing, the keys never pick an option left from the earlier text', async ({page}) => {
  await page.goto('/');
  const box = page.getByRole('combobox', {name: 'Town, ZIP code, street address, district or legislator'}).first();
  await box.fill('wai');
  await expect(page.locator('#find-place-list').getByRole('option')).toHaveCount(2);
  // Waikīkī is still listed when these keys arrive, before the list for kai loads.
  await box.fill('kai');
  await box.press('ArrowDown');
  await box.press('Enter');
  await expect(page.locator('#district-title')).toHaveText('House District 22');
  await expect(page.locator('#find-place')).toHaveValue('kai');
});
