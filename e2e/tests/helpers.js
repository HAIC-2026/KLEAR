// Shared steps for the browser tests.
import {writeFile} from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';
import {expect} from '@playwright/test';

export const DISTRICT = 'House District 22';
// The same site run with no assistant, as with no API key (playwright.config.js starts it).
export const BASIC_URL = process.env.KLEAR_E2E_BASIC_URL || 'http://127.0.0.1:8775';
// Rules for WCAG 2.0, 2.1 and 2.2 at levels A and AA.
const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const FAILING = new Set(['serious', 'critical']);

// Every request that leaves the test server is answered here, so no test touches the internet.
export async function offline(context) {
  await context.route(url => !url.href.startsWith('http://127.0.0.1:'), route =>
    route.fulfill({status: 200, contentType: 'text/html', body: '<!doctype html><title>Source</title><h1>Source page</h1>'}));
}

// Search from the Find page and pick a district from the matches. The map zooms to it and the district's page opens
// at its profile.
export async function chooseDistrict(page, query = 'Kāneʻohe', button = /^House 22\b/) {
  await page.getByLabel('Town, ZIP code, street address, district or legislator').first().fill(query);
  await page.locator('#find').getByRole('button', {name: 'Find district'}).click();
  await page.locator('#find-matches').getByRole('button', {name: button}).click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  // The move from the Find page to the district's page has finished.
  await expect(page.locator('html')).not.toHaveClass(/\bmoving\b/);
}

// Whether the agent is a panel the round button opens (below 1200 px, and on the Find page), and whether it is open.
export const chatFloats = page => page.evaluate(() => document.documentElement.classList.contains('chat-floats'));
const chatIsOpen = page => page.evaluate(() => document.documentElement.classList.contains('chat-open'));

// Below 1200 px, and on the Find page, the agent is a panel the round button opens. showTab(page, 'Ask') opens it and
// any other name shuts it; where the agent sits beside the page there is nothing to do.
export async function showTab(page, name) {
  if (!(await chatFloats(page))) return;
  const open = await chatIsOpen(page);
  if (name === 'Ask' && !open) await page.locator('#chat-fab').click();
  if (name !== 'Ask' && open) await page.locator('#chat-min').click();
}

// Ask a question by typing it, and wait for the answer to arrive.
// The answer announces the place it is about, which is the chosen district unless the question names another.
export async function ask(page, question = 'How does poverty here compare with the county and state?', place = DISTRICT) {
  await showTab(page, 'Ask');
  await page.getByLabel('Your question').fill(question);
  await page.locator('#send').click();
  await expect(page.locator('#chat-live')).toContainText(`Answer ready for ${place}.`);
  return page.locator('#log article').last();
}

// Open every disclosure, so axe checks what is inside them too.
export async function openAll(page) {
  await page.locator('details').evaluateAll(all => all.forEach(d => { d.open = true; }));
}

// Run axe for WCAG A and AA. Serious and critical problems fail the test; the rest are attached as notes. It runs from
// the top of the page: further down, the header that stays on screen covers whatever has scrolled under it, and axe
// would count that against a button half under it, as if the button were too small.
export async function expectAccessible(page, testInfo, view) {
  await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
  const results = await new AxeBuilder({page}).withTags(WCAG).analyze();
  const describe = v => `${v.impact} ${v.id}: ${v.help}\n${v.nodes.map(n => `  ${n.target.join(' ')}`).join('\n')}`;
  const minor = results.violations.filter(v => !FAILING.has(v.impact));
  if (minor.length) testInfo.annotations.push({type: `axe (${view})`, description: minor.map(describe).join('\n')});
  const saved = testInfo.outputPath('axe-violations.json');
  await writeFile(saved, JSON.stringify(results.violations, null, 2));
  await testInfo.attach(`axe-${view}.json`, {path: saved, contentType: 'application/json'});
  const failing = results.violations.filter(v => FAILING.has(v.impact));
  expect(failing.map(describe), `axe found serious or critical problems on the ${view}`).toEqual([]);
}
