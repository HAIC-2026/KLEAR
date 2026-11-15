// Screenshots of the views that use color, as people with common color vision deficiencies see them.
// Chromium simulates each deficiency; the pictures are saved with the test results for people to review.
// tests/agent/test_web_palette.py checks the same colors by number.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, offline} from './helpers.js';

const VISION = ['none', 'protanopia', 'deuteranopia', 'tritanopia', 'achromatopsia'];

test.skip(({browserName, isMobile}) => browserName !== 'chromium' || isMobile, 'vision simulation needs desktop Chromium');

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
  // The zoom buttons sit in the map's corner, just before it in the Tab order.
  await page.locator('#zoom-out').focus();
  await page.keyboard.press('Tab');
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
  // House 22 sits in urban Honolulu, so picking it shows the enlargement, where its neighbor 23 is hovered.
  await map.getByLabel('Or choose from a list').selectOption({label: 'House District 22'});
  await page.locator('#map .district.copy[data-geo="house:023"]').hover();
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
