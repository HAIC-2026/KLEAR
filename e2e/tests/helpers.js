// Shared steps for the browser tests.
import {writeFile} from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';
import {expect} from '@playwright/test';

export const DISTRICT = 'House District 22';
// Rules for WCAG 2.0, 2.1 and 2.2 at levels A and AA.
const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const FAILING = new Set(['serious', 'critical']);

// Every request that leaves the test server is answered here, so no test touches the internet.
export async function offline(context) {
  await context.route(url => !url.href.startsWith('http://127.0.0.1:'), route =>
    route.fulfill({status: 200, contentType: 'text/html', body: '<!doctype html><title>Source</title><h1>Source page</h1>'}));
}

// Search from the top of the page and pick a district from the matches.
export async function chooseDistrict(page, query = 'Kāneʻohe', button = /^House 22\b/) {
  await page.getByLabel('District, place, ZIP code, or street address').first().fill(query);
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await page.locator('#find-matches').getByRole('button', {name: button}).click();
  await expect(page.locator('#district-title')).toHaveText(DISTRICT);
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
}

// Ask a question by typing it, and wait for the answer to arrive.
export async function ask(page, question = 'How does poverty here compare with the county and state?') {
  await page.getByLabel('Your question').fill(question);
  await page.getByRole('button', {name: 'Ask KLEAR'}).click();
  await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}.`);
  return page.locator('#log article').last();
}

// Open every disclosure, so axe checks what is inside them too. The header's menu stays shut: open, it would cover the
// page under it (axe.spec.js checks it open on its own).
export async function openAll(page) {
  await page.locator('details:not(.nav-menu)').evaluateAll(all => all.forEach(d => { d.open = true; }));
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
