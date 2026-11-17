// Screenshots of the views that use color, as people with common color vision deficiencies see them.
// Chromium simulates each deficiency; the pictures are saved with the test results for people to review.
// tests/agent/test_web_palette.py checks the same colors by number.
import {test, expect} from '@playwright/test';
import {DISTRICT, ask, chooseDistrict, offline, openAll} from './helpers.js';

const VISION = ['none', 'protanopia', 'deuteranopia', 'tritanopia', 'achromatopsia'];

test.skip(({browserName, isMobile}) => browserName !== 'chromium' || isMobile, 'vision simulation needs desktop Chromium');

test('comparison marks use neutral text in both directions', async ({context, page}, testInfo) => {
  await offline(context);
  await page.goto('/district/house-22');
  await expect(page.locator('#profile-live')).toHaveText(`Profile for ${DISTRICT} loaded.`);
  await openAll(page);
  const glance = page.locator('#profile-glance');
  const ink = await page.locator('body').evaluate(el => getComputedStyle(el).color);
  const muted = await glance.locator('.profile-key').evaluate(el => getComputedStyle(el).color);
  expect(ink).not.toBe(muted);
  await expect(glance.getByText('▲ 3.4 points above county', {exact: true})).toHaveCSS('color', ink);
  await expect(page.getByText('▼ 0.2 points below state', {exact: true})).toHaveCSS('color', ink);
  await expect(glance.getByText('≈ No clear difference (state)', {exact: true})).toHaveCSS('color', muted);
  await shooter(await context.newCDPSession(page), testInfo)('at-a-glance', glance);
});

test('chamber colors, charts, and the map under simulated color blindness', async ({context, page}, testInfo) => {
  // Twenty screenshots take longer than the default limit on a busy machine.
  test.slow();
  await offline(context);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto('/');
  const shoot = shooter(await context.newCDPSession(page), testInfo);

  await page.getByLabel('Town, ZIP code, street address, district or legislator').first().fill('Kāneʻohe');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#find-matches .match')).toHaveCount(3);
  await shoot('search-results', page.locator('#find-matches'));

  // A ZIP code searched beside the map lights up its districts on it.
  await page.getByLabel('Town, ZIP code, street address, district or legislator').first().fill('96744');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#map .district.zip-hit').first()).toBeAttached();
  await shoot('map-zip', page.locator('.map-frame'));
  // Without a ZIP code: the Senate map's district borders, and the keyboard focus ring.
  await page.goto('/');
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();
  await page.getByRole('group', {name: 'Show House or Senate districts'}).getByRole('button', {name: 'Senate'}).click();
  // Tab from the zoom controls past the four island captions to a district.
  await page.locator('#zoom-out').focus();
  for (let i = 0; i < 5; i++) await page.keyboard.press('Tab');
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#map .ring-line')).toHaveAttribute('d', /\S/);
  // A different district under the mouse, to compare the hover outline and fill with the focus ring.
  await page.locator('#map .district:not(.copy)[data-geo="senate:017"]').hover();
  await expect(page.locator('#map .hover-line')).toHaveAttribute('d', /\S/);
  await shoot('map-senate', page.locator('.map-frame'));

  // A district's page: the map zoomed to the picked district beside its name, and an answer's charts.
  await page.goto('/');
  await chooseDistrict(page);
  await shoot('district-map', page.locator('#district-map .map-frame'));
  const answer = await ask(page);
  await shoot('answer-charts', answer.locator('.chart').first());
});

test('the map colored by a measure under simulated color blindness', async ({context, page}, testInfo) => {
  // Four views, each shot under every vision type: allow for a loaded machine.
  test.slow();
  await offline(context);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto('/rankings');
  const shoot = shooter(await context.newCDPSession(page), testInfo);
  const map = page.locator('#district-map');
  await expect(page.locator('#map .district:not(.copy)').first()).toBeAttached();

  // House: the two shades the fixture store has, dots on districts with no figure, a picked district and the hover
  // outline.
  await expect(page.locator('#map')).toHaveClass(/\bshaded\b/);
  // House 22 sits in urban Honolulu, so picking it zooms the map to the city, where its neighbor 23 is hovered.
  await map.getByRole('button', {name: 'Honolulu', exact: true}).click();
  await page.locator('#map .district[data-geo="house:022"]').click();
  await page.locator('#map .district[data-geo="house:023"]').hover();
  await expect(page.locator('#map .hover-line')).toHaveAttribute('d', /\S/);
  await shoot('map-shaded-house', page.locator('.map-frame'));
  await shoot('map-shaded-legend', page.locator('#map-legend'));

  // Senate: stripes on an unreliable estimate, with the keyboard focus ring on it.
  await map.getByRole('button', {name: 'Senate'}).click();
  await expect(page.locator('#map .marks .unreliable')).toHaveCount(1);
  await map.getByRole('button', {name: 'Oʻahu', exact: true}).click();
  await page.locator('#map .district:not(.copy)[data-geo="senate:012"]').focus();
  await shoot('map-shaded-senate', page.locator('.map-frame'));
  await shoot('map-shaded-senate-legend', page.locator('#map-legend'));
});

// Saves a view as seen with each vision type, by name in the test results, which CI uploads, and in the HTML report.
function shooter(cdp, testInfo) {
  return async (view, locator) => {
    await locator.scrollIntoViewIfNeeded();
    for (const type of VISION) {
      await cdp.send('Emulation.setEmulatedVisionDeficiency', {type});
      const path = testInfo.outputPath(`${view}-${type}.png`);
      await locator.screenshot({path, animations: 'disabled'});
      await testInfo.attach(`${view}-${type}.png`, {path, contentType: 'image/png'});
    }
    await cdp.send('Emulation.setEmulatedVisionDeficiency', {type: 'none'});
  };
}
