// The chat around each answer: next-question buttons, the waiting line with Cancel, errors with Retry, and shortcuts.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

// Hold every question until the returned function is called, without sending it on.
async function holdQuestions(page) {
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/chat', async route => { await held; await route.abort().catch(() => {}); });
  return release;
}

test('three related questions, each asked with a next-question button', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  const sent = [];
  page.on('request', r => { if (r.url().endsWith('/api/chat')) sent.push(r.postDataJSON()); });
  const first = await ask(page, 'How many kūpuna live in this district?');
  await expect(first.locator('.mode-badge')).toHaveText('Answer type: Assistant');
  const expected = [
    `Compare kūpuna in ${DISTRICT} with Honolulu County and the state.`,
    `How many kūpuna in ${DISTRICT} live below the poverty level?`,
    `How many people in ${DISTRICT} have no health insurance?`,
  ];
  for (const [i, question] of expected.entries()) {
    const next = page.locator('#log article').last().getByRole('group', {name: 'Ask next'});
    const button = next.getByRole('button').first();
    await expect(button).toHaveText(question);
    await button.click();
    await expect(page.locator('#log .user').last()).toHaveText(`You asked: ${question}`);
    await expect(page.locator('#log article')).toHaveCount(i + 2);
    await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
    // A question already asked is not offered again, even when it said "this district".
    await expect(page.locator('#log .followup', {hasText: `How many kūpuna live in ${DISTRICT}?`})).toHaveCount(0);
  }
  // Each question goes with the last two questions in this tab: each one's place and measures, never answer text.
  const asked = ['How many kūpuna live in this district?', ...expected];
  for (const [i, body] of sent.entries()) {
    expect(body.question).toBe(asked[i]);
    expect(body.district).toBe('house:022');
    expect((body.earlier || []).map(x => x.question)).toEqual(asked.slice(Math.max(0, i - 2), i));
    for (const x of body.earlier || []) expect(x).toEqual({question: x.question, district: 'house:022', indicators: ['pct_people_poverty', 'pop_65_plus', 'sites_lihtc']});
  }
  // Next questions keep to the district's address, and only the newest answer offers them.
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page.locator('#log .followups')).toHaveCount(1);
  await expect(page.locator('#log article').last().locator('.followups')).toBeVisible();
});

test('with no district chosen, a question naming a town offers its districts, and picking one answers for it', async ({page}) => {
  await page.goto('/');
  const sent = [];
  page.on('request', r => { if (r.url().endsWith('/api/chat')) sent.push(r.postDataJSON()); });
  await expect(page.locator('#welcome-text')).toHaveText('Ask about a district, a town or ZIP code, or all of Hawaiʻi.');
  await page.getByLabel('Your question').fill('How many kūpuna live in Kāneʻohe?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#chat-live')).toHaveText('Answer ready.');
  expect(sent[0]).toEqual({question: 'How many kūpuna live in Kāneʻohe?', district: null});
  const answer = page.locator('#log article').last();
  await expect(answer).toContainText('Kāneʻohe spans more than one House district.');
  const houses = answer.getByRole('group', {name: 'House districts for Kāneʻohe'});
  await expect(houses.getByRole('button')).toHaveCount(2);
  await houses.getByRole('button', {name: /^House 22\b/}).click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.locator('#log .user')).toHaveText(Array(2).fill('You asked: How many kūpuna live in Kāneʻohe?'));
  expect(sent[1].district).toBe('house:022');
  await expect(page.locator('#log .answer-choices')).toHaveCount(0);
});

test('with no district chosen, an answer about one district chooses it, and a follow-up keeps its subject', async ({page}) => {
  await page.goto('/');
  const sent = [];
  page.on('request', r => { if (r.url().endsWith('/api/chat')) sent.push(r.postDataJSON()); });
  const question = `How does poverty in ${DISTRICT} compare with the state?`;
  await page.getByLabel('Your question').fill(question);
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await expect(page.locator('#log article').last().locator('h3')).toHaveText(`KLEAR, ${DISTRICT}`);
  await expect(page.locator('#log .followups')).toHaveCount(1);
  await expect(page.locator('#log .switch-offer')).toHaveCount(0);
  await ask(page, 'And how does that compare with the county?');
  const subject = {question, district: 'house:022', indicators: ['pct_people_poverty', 'pop_65_plus', 'sites_lihtc']};
  expect(sent).toEqual([{question, district: null}, {question: 'And how does that compare with the county?', district: 'house:022', earlier: [subject]}]);
});

test('the statewide starter question is asked with no district chosen', async ({page}) => {
  await page.goto('/');
  const sent = [];
  page.on('request', r => { if (r.url().endsWith('/api/chat')) sent.push(r.postDataJSON()); });
  await page.locator('#examples').getByRole('button', {name: /highest share of kūpuna/}).click();
  const question = 'Which House districts have the highest share of kūpuna?';
  await expect(page.locator('#log .user')).toHaveText(`You asked: ${question}`);
  await expect(page.locator('#chat-live')).toHaveText(/^Answer ready/);
  expect(sent).toEqual([{question, district: null}]);
});

test('the examples include ones picked from the district profile, and they go when the district changes', async ({page}) => {
  // Add a place burned in the 2023 Maui wildfires to every profile.
  await page.route('**/api/profile?*', async route => {
    const response = await route.fetch();
    const profile = await response.json();
    profile.places.unshift({name: 'Lahaina', share: '46%'});
    await route.fulfill({response, json: profile});
  });
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  const local = page.locator('#examples .local');
  // House 22's poverty rate is about 40 percent above Honolulu County's in the test store.
  await expect(local).toHaveCount(2);
  await expect(local.nth(0)).toHaveAccessibleName(new RegExp(`^Survey years and the 2023 wildfires\\s*: Which survey years do the housing figures for ${DISTRICT} cover, given the 2023 Maui wildfires\\?$`));
  await expect(local.nth(1)).toHaveAccessibleName(new RegExp(`^Poverty higher than countywide\\s*: Compare poverty in ${DISTRICT} with Honolulu County and the state\\.$`));
  await local.nth(1).click();
  await expect(page.locator('#log .user')).toHaveText(`You asked: Compare poverty in ${DISTRICT} with Honolulu County and the state.`);
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.locator('#log .followups')).toHaveCount(1);

  await page.getByRole('button', {name: 'Change district'}).click();
  await page.locator('#place').fill('House 23');
  await page.locator('#lookup').getByRole('button', {name: 'Find district'}).click();
  await expect(page.locator('#district-title')).toHaveText('House District 23');
  await expect(page).toHaveURL(/\/district\/house-23$/);
  await expect(page.locator('#log .followups')).toHaveCount(0);
  await expect(page.locator('#profile-live')).toHaveText('Profile for House District 23 loaded.');
  await expect(local).toHaveCount(1);
  await expect(local).toHaveAccessibleName(/^Survey years and the 2023 wildfires\s*: .* for House District 23 cover/);

  // Back to House 22: its address, its examples, and no next questions that name House 23.
  await page.goBack();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page).toHaveURL(/\/district\/house-22$/);
  await expect(local).toHaveCount(2);
  await expect(page.locator('#log .followups')).toHaveCount(0);
});

test('a slow question shows the seconds, can be cancelled, and Retry asks it again', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  const release = await holdQuestions(page);
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  const waiting = page.locator('#log .pending');
  await expect(waiting).toContainText('Checking district data and sources…');
  await expect(waiting.locator('.elapsed')).toHaveText(/^[1-9]\d* seconds?$/);
  await waiting.getByRole('button', {name: 'Cancel'}).click();
  const message = 'You cancelled this question. Press Retry to ask it again.';
  await expect(page.locator('#log .error p')).toHaveText(message);
  await expect(page.locator('#chat-live')).toHaveText(message);
  await expect(page.locator('#log .pending')).toHaveCount(0);
  await expect(page.getByLabel('Your question')).toHaveValue('How many kūpuna live here?');
  await expect(page.getByLabel('Your question')).toBeFocused();
  await page.unroute('**/api/chat');
  release();
  await page.locator('#log').getByRole('button', {name: 'Retry'}).click();
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.locator('#log .user')).toHaveText(['You asked: How many kūpuna live here?', 'You asked: How many kūpuna live here?']);
  await expect(page.locator('#log .retry')).toHaveCount(0);
});

test('a question with no answer after 45 seconds is stopped and says what to do', async ({page}) => {
  await page.clock.install();
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  const release = await holdQuestions(page);
  await page.getByLabel('Your question').fill('How many kūpuna live here?');
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#log .pending')).toBeVisible();
  await page.clock.fastForward('00:46');
  const message = 'KLEAR did not answer within 45 seconds, so the question was stopped. Press Retry to ask again.';
  await expect(page.locator('#log .error p')).toHaveText(message);
  await expect(page.locator('#log').getByRole('button', {name: 'Retry'})).toBeVisible();
  release();
});

test('error pages that are not JSON get plain messages that say what to do next', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  const replies = [
    [502, '<!doctype html><title>502 Bad Gateway</title><h1>Bad Gateway</h1>', 'KLEAR’s server is not responding right now (error 502). Wait a moment, then press Retry.', true],
    [200, '<!doctype html><title>Sign in</title><h1>Sign in to the network</h1>', 'KLEAR sent a reply it could not read. Press Retry to ask again.', true],
    [404, 'Not Found', 'KLEAR could not take this question (error 404). Reload the page, then ask again.', false],
  ];
  for (const [status, body, message, retry] of replies) {
    await page.route('**/api/chat', route => route.fulfill({status, contentType: 'text/html', body}));
    await page.getByLabel('Your question').fill('How does poverty here compare?');
    await page.getByRole('button', {name: 'Ask KLEAR'}).click();
    await expect(page.locator('#chat-live')).toHaveText(message);
    const error = page.locator('#log .error').last();
    await expect(error.locator('p')).toHaveText(message);
    await expect(error.getByRole('button', {name: 'Retry'})).toHaveCount(retry ? 1 : 0);
    await page.unroute('**/api/chat');
  }
  await expect(page.locator('#log')).not.toContainText('Unexpected token');
});

test('Ctrl+Enter sends the question, and Enter alone starts a new line', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  await showTab(page, 'Ask');
  const box = page.getByLabel('Your question');
  await expect(box).toHaveAccessibleDescription('Press Ctrl+Enter to send.');
  await box.fill('How many kūpuna');
  await box.press('Enter');
  await box.pressSequentially('live here?');
  await expect(box).toHaveValue('How many kūpuna\nlive here?');
  await expect(page.locator('#log .user')).toHaveCount(0);
  await box.press('Control+Enter');
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${DISTRICT}.`);
  await expect(page.locator('#log .user')).toHaveText('You asked: How many kūpuna\nlive here?');
  await box.fill('How does poverty here compare?');
  await box.press('Meta+Enter');
  await expect(page.locator('#log article')).toHaveCount(2);
});

test('an answer with no data points to datatrove.ai, and an answer with figures does not', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const missing = await ask(page, 'How many people are enrolled in WIC here?');
  await expect(missing.locator('h3')).toContainText('Data not available');
  const line = missing.locator('p.elsewhere');
  await expect(line).toHaveText('For other Hawaiʻi public data on this topic, try datatrove.ai (opens in a new tab).');
  const link = line.getByRole('link', {name: 'datatrove.ai (opens in a new tab)'});
  await expect(link).toHaveAttribute('href', 'https://datatrove.ai/');
  await expect(link).toHaveClass('ext');
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  const found = await ask(page);
  await expect(found.locator('.tile').first()).toBeVisible();
  await expect(found.locator('.elsewhere')).toHaveCount(0);
  await expect(found.getByRole('link', {name: /datatrove/})).toHaveCount(0);
});

test('Data and sources and About name datatrove.ai as a site that leaves KLEAR', async ({page}) => {
  await page.goto('/');
  for (const section of ['#sources', '#about']) {
    const link = page.locator(`${section} a[href="https://datatrove.ai/"]`);
    await expect(link).toHaveText('datatrove.ai (external site)');
    await expect(link).toHaveClass('ext');
  }
});
