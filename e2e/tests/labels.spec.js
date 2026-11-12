// Short labels on charts, tiles and profile rows, each with what it means one tap away, and the glossary.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, offline} from './helpers.js';

test.beforeEach(async ({context, page}) => {
  await offline(context);
  await page.goto('/');
});

// A label as it reads on screen, without a row's source number.
const labelTexts = locator => locator.evaluateAll(all => all.map(el =>
  [...el.childNodes].filter(n => n.nodeName !== 'SUP').map(n => n.textContent).join('').trim()));

test('a chart label opens to its definition, official label and who it counts', async ({page}) => {
  await chooseDistrict(page);
  const answer = await ask(page);
  const chart = answer.getByRole('figure', {name: 'People below the poverty level'});
  const about = chart.locator('details.meaning');
  await expect(about).not.toHaveAttribute('open');
  const summary = about.locator('summary');
  await expect(summary).toHaveText('What this means: People below the poverty level');
  // Big enough to tap.
  expect((await summary.boundingBox()).height).toBeGreaterThanOrEqual(24);
  await summary.click();
  await expect(about.getByText('The percent of people whose family income is below the poverty line for a family of their size.')).toBeVisible();
  await expect(about.locator('dt')).toHaveText(['Official label', 'Who is counted']);
  await expect(about.locator('dd')).toHaveText(['Percent of people below the poverty level', 'people']);
  const glossary = about.getByRole('link', {name: 'Glossary of terms (opens in a new tab)'});
  await expect(glossary).toHaveAttribute('href', '/glossary.html');
  await expect(glossary).toHaveAttribute('target', '_blank');
});

test('the disclosure opens from the keyboard', async ({page}) => {
  await chooseDistrict(page);
  const answer = await ask(page);
  const about = answer.getByRole('figure', {name: 'Kūpuna (65 and over)'}).locator('details.meaning');
  await about.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(about).toHaveAttribute('open');
  await expect(about.getByText('People age 65 or older.')).toBeVisible();
});

test('every label on screen is short and has its meaning beside it', async ({page}) => {
  await chooseDistrict(page);
  const answer = await ask(page, 'How many kūpuna live here?');
  await page.locator('#profile details.profile-section').evaluateAll(all => all.forEach(d => { d.open = true; }));
  const labels = page.locator('.tile-label, .chart-head > span:first-child, #profile .profile-label');
  const texts = await labelTexts(labels);
  expect(texts.length).toBeGreaterThan(3);
  for (const text of texts) expect(text.length, text).toBeLessThanOrEqual(45);
  const rows = page.locator('#profile .profile-row');
  await expect(rows.locator('details.meaning')).toHaveCount(await rows.count());
  for (const tile of await answer.locator('.tile').all()) await expect(tile.locator('details.meaning')).toHaveCount(1);
});

test('the glossary opens from the footer and explains margin of error', async ({page}) => {
  await page.getByRole('contentinfo').getByRole('link', {name: 'Glossary'}).click();
  await expect(page).toHaveURL(/\/glossary\.html$/);
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Glossary');
  await expect(page.locator('#margin-of-error dt')).toHaveText('Margin of error (±)');
  await page.goto('/glossary.html#cv');
  await expect(page.locator('#cv')).toBeInViewport();
});
