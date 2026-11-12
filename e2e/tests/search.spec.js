// District search beyond towns: neighborhoods, islands, street addresses and numbers out of range.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, expectAccessible, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

async function search(page, text) {
  await page.getByLabel('District, place, ZIP code, or street address').first().fill(text);
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
  await expect(page.locator('#find .privacy')).toContainText('goes to the U.S. Census Bureau only to find its district');
  const matches = await search(page, '1 Test St');
  await expect(page.locator('#find-status')).toHaveText('Choose the House or Senate district to ask about.');
  await expect(matches.locator('.match-title')).toHaveText('1 TEST ST, KANEOHE, HI, 96744');
  await expect(matches.getByRole('button')).toHaveText(['House 23', 'Senate 12']);
  await expect(page.locator('#find .privacy')).toBeVisible();
  await matches.getByRole('button', {name: 'House 23'}).click();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
});

test('a street address in a chat question gets its own district, and the question box says where it goes', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await expect(page.locator('#question-form .chat-privacy')).toHaveText('A street address in a question goes to the U.S. Census Bureau only to find its district. KLEAR does not store or log it.');
  await expect(page.locator('#question-form .chat-privacy')).toBeVisible();
  const answer = await ask(page, 'Which House and Senate district is 1 Test St, Kāneʻohe in?');
  await expect(answer).toContainText('That address is in House District 23 and Senate District 12.');
  await expect(answer.locator('h3')).not.toContainText('declined');
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

  await matches.getByRole('link', {name: 'see its districts on the map'}).click();
  await expect(page.locator('#zip-status')).toHaveText('Oʻahu spans 10 House districts. Share of its residents in each:');
  await expect(page.locator('#zip-matches').getByRole('button')).toHaveCount(10);
  await expect(page.locator('#map .district.zip-hit')).toHaveCount(10);
});

test('typing suggests places, and the arrow keys and Enter pick one, which searches at once', async ({page}, testInfo) => {
  await page.goto('/');
  const box = page.getByRole('combobox', {name: 'District, place, ZIP code, or street address'}).first();
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
  const box = page.getByRole('combobox', {name: 'District, place, ZIP code, or street address'}).first();
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

test('the chat’s district search suggests too, and typing one letter suggests nothing', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button', {name: 'Choose a district'}).click();
  const box = page.locator('#place');
  await expect(box).toHaveAttribute('role', 'combobox');
  await box.fill('o');
  await page.waitForTimeout(400);
  await expect(page.locator('#place-list')).toBeHidden();
  await box.fill('oah');
  await expect(page.locator('#place-list').getByRole('option')).toHaveText([/^Oʻahu\s+Island$/]);
  await box.press('Tab');
  await expect(page.locator('#place-list')).toBeHidden();
});

// People type the ʻokina many ways, or leave it out, and the kahakō too. Each finds the same place.
const TYPED = ['Kāneʻohe', "Kane'ohe", 'Kane`ohe', 'Kane‘ohe', 'Kane’ohe', 'Kaneʼohe', 'Kane´ohe', 'Kaneohe', "KANE'OHE"];

test('the ʻokina typed as ʻ, \', `, ‘, ’, ʼ or ´, or left out, finds the place', async ({page}) => {
  await page.goto('/');
  const box = page.getByRole('combobox', {name: 'District, place, ZIP code, or street address'}).first();
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
  const box = page.getByRole('combobox', {name: 'District, place, ZIP code, or street address'}).first();
  await box.fill('wai');
  await expect(page.locator('#find-place-list').getByRole('option')).toHaveCount(2);
  // Waikīkī is still listed when these keys arrive, before the list for kai loads.
  await box.fill('kai');
  await box.press('ArrowDown');
  await box.press('Enter');
  await expect(page.locator('#district-title')).toHaveText('House District 22');
  await expect(box).toHaveValue('kai');
});
