// Programs on the district profile and in answers: need groups with the district figure said once, one row per program
// with who it is for, one limit and how to reach it, and the date the details were checked said once.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline} from './helpers.js';

test.beforeEach(async ({context}) => offline(context));

// Rewrite the profile the server sends, so a test can stand in for a district with a group above the state's.
async function editProfile(page, edit) {
  await page.route('**/api/profile?*', async route => {
    const response = await route.fetch();
    const profile = await response.json();
    edit(profile);
    await route.fulfill({response, json: profile});
  });
}

async function openPrograms(page) {
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  const section = page.locator('#profile-programs');
  await section.evaluate(d => { d.open = true; });
  return section;
}

test('each program row says what it is for, its limit and how to reach it, and the checked date shows once', async ({page}) => {
  const section = await openPrograms(page);
  const rows = section.locator('.program');
  const count = await rows.count();
  expect(count).toBeGreaterThan(5);
  await expect(section.locator('.program-for')).toHaveCount(count);
  const snap = rows.filter({has: page.locator('.program-name', {hasText: /^SNAP\b/})});
  await expect(snap.locator('.program-level')).toHaveText('State');
  await expect(snap.locator('.program-for')).toHaveText(/^For Households with low income/);
  await expect(snap.locator('.program-limit')).toHaveText('Limit Gross monthly income up to $3,404 for a household of 3, in effect from October 1, 2026.');
  await expect(snap.getByRole('link', {name: /^Official page for SNAP/})).toHaveAttribute('href', 'https://humanservices.hawaii.gov/bessd/snap/');
  await expect(snap.getByRole('link', {name: 'Call 1-855-643-1643'})).toHaveAttribute('href', 'tel:+18556431643');
  // A program with no confirmed income rule shows no limit.
  const aging = rows.filter({has: page.locator('.program-name', {hasText: 'Elderly Affairs'})});
  await expect(aging.locator('.program-limit')).toHaveCount(0);
  await expect(aging.locator('.program-for')).toHaveCount(1);
  await expect(section.locator('.program-checked')).toHaveCount(1);
  await expect(section.locator('.program-checked')).toHaveText(/^Program details checked \w+ \d+(, \d{4})?( to (\w+ )?\d+)?, \d{4}\.$/);
  await expect(section.getByText("This district-level view cannot establish any person's eligibility")).toHaveCount(1);
  // Each group's figure is said once, and no row repeats it.
  const said = await section.locator('.program-figure').allTextContents();
  expect(said.length).toBeGreaterThan(0);
  expect(new Set(said).size).toBe(said.length);
  await expect(section.locator('.program .program-figure')).toHaveCount(0);
  // Nothing in this district sits above the state's, so every group is open and none is tucked away.
  await expect(section.locator('details.program-others')).toHaveCount(0);
});

test('groups not above the state’s go in a closed disclosure with their count', async ({page}, testInfo) => {
  await editProfile(page, p => {
    p.programs.groups.find(g => g.id === 'kupuna').above = true;
    p.programs.others_title = 'Other programs';
  });
  const section = await openPrograms(page);
  const open = section.locator(':scope > .program-group');
  await expect(open).toHaveCount(1);
  await expect(open.locator('.program-group-title')).toHaveText('Kūpuna');
  const others = section.locator('details.program-others');
  await expect(others).not.toHaveAttribute('open', '');
  const rest = await others.locator('.program').count();
  await expect(others.locator(':scope > summary')).toHaveText(`Other programs (${rest})`);
  expect(rest + await open.locator('.program').count()).toBe(await section.locator('.program').count());
  if (testInfo.project.name === 'desktop') {
    const box = await section.boundingBox();
    expect(box.height).toBeLessThan(900);
  }
  await others.locator(':scope > summary').click();
  await expect(others.locator('.program').first()).toBeVisible();
});

test('only a stale row says when it was checked, and the line at the top leaves it out', async ({page}) => {
  await editProfile(page, p => {
    const all = [...p.programs.programs, ...p.programs.others];
    for (const g of all) g.verified_on = '2026-11-09';
    const snap = all.find(g => g.id === 'snap');
    snap.stale = true;
    snap.verified_on = '2026-06-01';
    snap.checked = 'Check the official page. These details were last checked on June 1, 2026.';
  });
  const section = await openPrograms(page);
  await expect(section.locator('.program-checked')).toHaveText('Program details checked November 9, 2026.');
  await expect(section.locator('.program-stale')).toHaveCount(1);
  await expect(section.locator('.program-stale')).toHaveText('Check the official page. These details were last checked on June 1, 2026.');
});

test('a programs answer uses the same groups and rows', async ({page}) => {
  await page.goto('/');
  await chooseDistrict(page);
  const answer = await ask(page, 'Basic lookup: what support programs could residents of this district explore?');
  const programs = answer.locator('.answer-programs');
  await expect(programs.locator(':scope > summary')).toHaveText('Programs to explore here');
  await expect(programs.locator('.program-checked')).toHaveCount(1);
  await expect(programs.locator('.program-group-title').first()).toBeVisible();
  const count = await programs.locator('.program').count();
  expect(count).toBeGreaterThan(5);
  await expect(programs.locator('.program-for')).toHaveCount(count);
  const said = await programs.locator('.program-figure').allTextContents();
  expect(new Set(said).size).toBe(said.length);
});
