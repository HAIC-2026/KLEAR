// The site with no assistant, as with no API key: the free basic lookup answers every question. These tests run the
// real lookup (e2e/serve.py --basic-only), not a scripted model, so they check its own answers.
import {test, expect} from '@playwright/test';
import {BASIC_URL, offline, showTab} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

const BASIC_PRIVACY = 'Basic lookup: KLEAR matches questions to its own data, with no AI model. Questions are not sent to an AI provider. Leave out personal details.';

async function askHere(page, question, place) {
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill(question);
  await page.locator('#send').click();
  await expect(page.locator('#chat-live')).toContainText(place ? `Answer ready for ${place}.` : 'Answer ready');
  return page.locator('#log article').last();
}

test('before a question, the page says the basic lookup answers and nothing goes to an AI provider', async ({page}) => {
  await page.goto(`${BASIC_URL}/`);
  await expect(page.locator('#home-privacy')).toHaveText(BASIC_PRIVACY);
  await expect(page.locator('#home-privacy')).not.toContainText('Anthropic');
  await showTab(page, 'Ask');
  await expect(page.locator('#chat-privacy')).toHaveText(`${BASIC_PRIVACY} Privacy`);
  await expect(page.locator('#chat-mode')).toBeVisible();
  await expect(page.locator('#chat-mode')).toContainText('Basic lookup, with no AI model');
  const ready = await (await page.request.get(`${BASIC_URL}/api/ready`)).json();
  expect(ready.capabilities).toEqual({lookup: true, assistant: false, assistant_capped: false, prepared_answers: false, address_lookup: true});
});

test('the basic lookup compares the two districts asked about, never one with the state', async ({page}) => {
  await page.goto(`${BASIC_URL}/compare?d=house:022,house:023`);
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  const both = 'House District 22 and House District 23';
  const answer = await askHere(page, 'How does poverty in House District 22 compare with House District 23?', both);
  await expect(answer.locator('.mode-badge')).toContainText('Basic lookup');
  await expect(answer.locator('h3')).toHaveText(`KLEAR, ${both}`);
  await expect(answer).toContainText('House District 22, 12.3% ± 2.1 points; House District 23, 7.9% ± 1.8 points.');
  await expect(answer).toContainText('House District 22 is higher than House District 23, beyond the margins of error.');
  // The chart sets the two districts side by side, and the state is nowhere in it.
  const chart = answer.getByRole('figure', {name: /People below the poverty level/});
  await expect(chart.locator('.chip')).toHaveText('Higher than House District 23');
  await expect(chart).not.toContainText('Hawaiʻi');
  // Its next questions name both districts, and no switch to one of them is offered.
  await expect(answer.locator('.followup').first()).toContainText(both);
  await expect(answer.locator('.switch-offer')).toHaveCount(0);
  // Its copy button names both too.
  await expect(answer.getByRole('button', {name: `Copy answer for ${both}`})).toBeVisible();
});

test('"these two districts" on Compare means the two districts on the page', async ({page}) => {
  await page.goto(`${BASIC_URL}/compare?d=house:023,house:022`);
  await expect(page.locator('.cmp-table').first()).toBeVisible();
  const answer = await askHere(page, 'How do these two districts compare on poverty?', 'House District 23 and House District 22');
  await expect(answer).toContainText('House District 23 is lower than House District 22, beyond the margins of error.');
});

test('the kūpuna ranking starter is answered without a key, and opens the matching Rankings view', async ({page}) => {
  await page.goto(`${BASIC_URL}/`);
  await page.locator('#home-starters').getByRole('button', {name: 'Which House districts have the highest share of kūpuna?'}).click();
  await expect(page.locator('#chat-live')).toContainText('Answer ready');
  const answer = page.locator('#log article').last();
  await expect(answer).toContainText('House districts with the highest share who are kūpuna (65 and over)');
  await expect(answer).toContainText('First group: House District 23 (20.4% ± 1.6 points)');
  await expect(answer).not.toContainText('needs the KLEAR assistant');
  const link = answer.getByRole('link', {name: 'See every House district ranked and on the map'});
  await expect(link).toHaveAttribute('href', '/rankings?measure=pct_65_plus&chamber=house');
  await link.click();
  await expect(page).toHaveURL(/\/rankings\?measure=pct_65_plus&chamber=house$/);
  await expect(page.locator('#map-shade')).toHaveValue('pct_65_plus');
  await expect(page.locator('.chamber-switch [data-chamber=house]').first()).toHaveAttribute('aria-pressed', 'true');
});

test('every suggested question on the Find page gets an answer, not a dead end', async ({page}) => {
  await page.goto(`${BASIC_URL}/`);
  const starters = await page.locator('#home-starters button').allTextContents();
  expect(starters.length).toBeGreaterThan(3);
  for (const question of starters) {
    // A fresh page each time, with the last answer's panel shut.
    await page.evaluate(() => sessionStorage.clear());
    await page.goto(`${BASIC_URL}/`);
    await page.locator('#home-starters').getByRole('button', {name: question}).click();
    await expect(page.locator('#chat-live')).toContainText(/Answer ready|could mean|spans/);
    const answer = page.locator('#log article').last();
    await expect(answer, question).not.toContainText('needs the KLEAR assistant');
    await expect(answer, question).not.toContainText('Anthropic');
  }
});

const answerJson = extra => ({text: 'Kūpuna make up 12.3% of residents.', refusal: 'none', facts: [], programs: [], lead: ['Kūpuna make up 12.3% of residents.'], tiles: [], charts: [], caveats: [], sources: [], ...extra});

test('a prepared answer is labeled as written ahead, with no new AI call', async ({page}) => {
  await page.route('**/api/chat', route => route.fulfill({json: answerJson({mode: 'assistant', prepared: 'Prepared ahead of time from the data update of November 13, 2026.'})}));
  await page.goto('/district/house-22');
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill('How many kūpuna live in this district?');
  await page.locator('#send').click();
  const answer = page.locator('#log article').last();
  await expect(answer.locator('.mode-badge')).toHaveText('Answer type: Prepared answer');
  await expect(answer.locator('.mode-badge')).toHaveAttribute('title', /No new AI call was made/);
});

test('the notes follow the assistant when it reaches its cap or fails, and come back when it answers', async ({page}) => {
  await page.route('**/api/ready', route => route.fulfill({json: {ready: true, assistant: 'on', capabilities: {lookup: true, assistant: true, assistant_capped: false, prepared_answers: false, address_lookup: true}}}));
  let reply = answerJson({mode: 'basic', fallback: 'failed', lead: ['The full assistant is not available right now, so this is a basic lookup.']});
  await page.route('**/api/chat', route => route.fulfill({json: reply}));
  await page.goto('/district/house-22');
  await showTab(page, 'Ask');
  const note = page.locator('#chat-privacy-text');
  await expect(note).toHaveText('An AI model answers from KLEAR’s data. Questions go to its provider, Anthropic, so leave out personal details.');
  const ask = async () => {
    const before = await page.locator('#log article').count();
    await page.getByLabel('Your question').fill('How many kūpuna live in this district?');
    await page.locator('#send').click();
    await expect(page.locator('#log article')).toHaveCount(before + 1);
  };
  await ask();
  await expect(note).toContainText('The AI assistant could not answer the last question, so the basic lookup did.');
  reply = answerJson({mode: 'basic', fallback: 'capped', lead: ['The full assistant has reached today\'s spending limit, so this is a basic lookup.']});
  await ask();
  await expect(note).toContainText('The AI assistant has reached today’s spending limit');
  await expect(page.locator('#chat-mode')).toBeVisible();
  reply = answerJson({mode: 'assistant'});
  await ask();
  await expect(note).toHaveText('An AI model answers from KLEAR’s data. Questions go to its provider, Anthropic, so leave out personal details.');
  await expect(page.locator('#chat-mode')).toBeHidden();
});

test('with the cap already reached, the page says so before the first question', async ({page}) => {
  await page.route('**/api/ready', route => route.fulfill({json: {ready: true, assistant: 'on', capabilities: {lookup: true, assistant: true, assistant_capped: true, prepared_answers: false, address_lookup: false}}}));
  await page.goto('/');
  await expect(page.locator('#home-privacy')).toContainText('The AI assistant has reached today’s spending limit');
  await expect(page.locator('#home-privacy')).toContainText('Questions are not sent to an AI provider.');
  await expect(page.locator('#find .privacy')).toHaveText('Street address search is off on this site. Search by town, ZIP code, district or legislator.');
});
