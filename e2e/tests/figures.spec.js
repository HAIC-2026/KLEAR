// The line under each answer that counts its figures and says each comes from a cited source.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

// Figures the answer draws: each tile with a value, each other place under a tile, each row of the table of more
// figures with a value, and each chart bar.
async function shownFigures(answer) {
  const tiles = await answer.locator('.tile .tile-value:not(.none)').count();
  const places = await answer.locator('.tile-place-value').filter({hasNotText: 'not available'}).count();
  const more = await answer.locator('.more-figures tbody tr').filter({hasNotText: 'not available'}).count();
  const bars = await answer.locator('.chart .bar-row').count();
  return tiles + places + more + bars;
}

for (const [mode, question, badge] of [
  ['assistant', 'How does poverty here compare with the county and state?', 'Assistant'],
  ['basic', 'Basic lookup: how many kūpuna live here?', 'Basic lookup'],
]) {
  test(`a ${mode} answer counts its figures and says each comes from a cited source`, async ({page}) => {
    await page.goto('/');
    await chooseDistrict(page);
    const answer = await ask(page, question);
    await expect(answer.locator('.mode-badge')).toContainText(badge);
    const count = await shownFigures(answer);
    expect(count).toBeGreaterThan(0);
    const text = count === 1
      ? 'The 1 figure in this answer comes from the cited source below.'
      : new RegExp(`^All ${count} figures in this answer come from the cited sources? below\\.$`);
    const line = answer.locator('p.verified');
    await expect(line).toHaveText(text);
    await expect(line).toBeVisible();
    // The line sits right above the source list it points to, and the status line reads it out.
    await expect(answer.locator('p.verified + details > summary')).toHaveText('Sources');
    await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}. ${await line.textContent()}`);
  });
}

test('answers with no figures and declines have no figure line', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  for (const question of ['How many people are enrolled in WIC here?', 'Which candidate should I vote for?']) {
    const answer = await ask(page, question);
    await expect(answer.locator('h3')).toBeVisible();
    await expect(answer.locator('.verified')).toHaveCount(0);
  }
  await expect(page.locator('#chat-live')).toHaveText(`Answer ready for ${DISTRICT}.`);
});
