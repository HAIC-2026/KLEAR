// Screenshots of the views that use color, as people with common color vision deficiencies see them.
// Chromium simulates each deficiency; the pictures are saved with the test results for people to review.
// tests/agent/test_web_palette.py checks the same colors by number.
import {test, expect} from '@playwright/test';
import {ask, chooseDistrict, offline} from './helpers.js';

const VISION = ['none', 'protanopia', 'deuteranopia', 'tritanopia', 'achromatopsia'];

test.skip(({browserName, isMobile}) => browserName !== 'chromium' || isMobile, 'vision simulation needs desktop Chromium');

test('chamber colors, charts, and the map under simulated color blindness', async ({context, page}, testInfo) => {
  await offline(context);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto('/');
  const cdp = await context.newCDPSession(page);

  await page.getByLabel('District, town, or ZIP code').first().fill('Kāneʻohe');
  await page.getByRole('button', {name: 'Find district'}).first().click();
  await expect(page.locator('#find-matches .match')).toHaveCount(3);
  await shoot('search-results', page.locator('#find-matches'));

  await chooseDistrict(page);
  const answer = await ask(page);
  await shoot('answer-charts', answer.locator('.chart').first());

  const map = page.locator('#district-map');
  await map.getByLabel('Highlight a ZIP code').fill('96744');
  await map.getByRole('button', {name: 'Show on map'}).click();
  await map.getByLabel('Or choose from a list').selectOption({label: 'House District 22'});
  await shoot('map-zip', page.locator('.map-frame'));
  // Without a ZIP code: dark district borders, a picked district, and the keyboard focus ring.
  await map.getByLabel('Highlight a ZIP code').fill('');
  await map.getByRole('button', {name: 'Senate'}).click();
  await map.getByLabel('Or choose from a list').selectOption({label: 'Senate District 24'});
  await page.locator('#island-tabs button').last().focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#map .ring-line')).toHaveAttribute('d', /\S/);
  // A different district under the mouse, to compare the hover outline and fill with the focus ring.
  await page.locator('#map .district[data-geo="senate:017"]').hover();
  await expect(page.locator('#map .hover-line')).toHaveAttribute('d', /\S/);
  await shoot('map-senate', page.locator('.map-frame'));

  async function shoot(view, locator) {
    await locator.scrollIntoViewIfNeeded();
    for (const type of VISION) {
      await cdp.send('Emulation.setEmulatedVisionDeficiency', {type});
      // Saved by name in the test results, which CI uploads, and attached to the HTML report.
      const path = testInfo.outputPath(`${view}-${type}.png`);
      await locator.screenshot({path, animations: 'disabled'});
      await testInfo.attach(`${view}-${type}.png`, {path, contentType: 'image/png'});
    }
    await cdp.send('Emulation.setEmulatedVisionDeficiency', {type: 'none'});
  }
});
