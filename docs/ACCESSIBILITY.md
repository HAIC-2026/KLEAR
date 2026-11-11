# KLEAR accessibility statement

KLEAR aims to meet the Web Content Accessibility Guidelines (WCAG) 2.2 at level AA. This page says what is in place, how it has been tested, what is known not to work yet, and how to report a problem.

Last updated: November 9, 2026.

## What is in place

- A "Skip to content" link, labels on every form field, and a visible outline on whatever has keyboard focus. On the map, the focused district gets a dark outline with a white edge, drawn above its neighbors.
- Three ways to pick a district: a search by district, town, neighborhood, island, ZIP code, or street address; a map you can use with the keyboard (arrow keys move between districts, Enter picks one, and Enter again asks about it); and a plain list of all districts.
- Answers are not read aloud as they arrive. A short status line says "Answer ready for House District 22.", and if you are still in the chat, focus moves to the new answer's heading, so you can read it from the top. If you have moved to another field, focus stays where you are.
- The district profile beside the chat works the same way: a short status line ("Profile for House District 22 loaded.") is announced, and the figures are read only when you move to them.
- Every bar chart has a sentence that says what it shows, such as "House District 22, at 12.3% ± 2.1 points, is higher than Honolulu County and is within the margin of error of Hawaiʻi.", and a "Show the data table" button that opens the same figures as a table. Screen readers skip the bars themselves.
- Colors do not carry meaning alone. In charts, the district you asked about comes first, its bar is darker, and its name is bold. On the map, districts have dark borders, a ZIP code's districts are filled darker than the rest, and the district under the mouse gets a darker fill and a dark outline, thinner than the keyboard focus ring so the two never look alike. The colors were checked with simulated protanopia, deuteranopia, tritanopia, and full color blindness.
- Text is at least 13 px, except the district numbers on the map, which are 12 px. Measured contrast: body text 16:1, secondary text 8.0:1 on white and 7.3:1 on gray, links and buttons 8.3:1, text field borders 4.6:1, placeholder text 5.7:1, chart bars, map borders, and the hover outline at least 3:1 against what is around them.
- The ↗ arrow marks only links that leave the site. Screen readers also hear "opens in a new tab" or "external site".
- The Hawaiian words kūpuna, keiki, and ʻohana are marked as Hawaiian, so a screen reader with a Hawaiian voice can pronounce them. Place names such as Hawaiʻi and Kāneʻohe are left as they are, since they are names used in English.
- The loading indicator, highlight animation, map transitions, and smooth scrolling stop when your device asks for reduced motion.
- The page fits a 320 px wide screen without scrolling sideways, and works at 200% and 400% zoom.
- The suggested-question buttons above the question box show short labels, and each button's spoken name begins with that label, so voice control can use the words on screen.

## How it has been tested

Automated checks run on every change, in GitHub Actions. The full list, with dates and results, is in the [accessibility test checklist](ACCESSIBILITY-CHECKLIST.md).

- **axe** (axe-core 4.14.0, through Playwright) checks the landing page, search results, district profile, an answer with charts and sources, declined and failed questions, and the map, for every WCAG 2.0, 2.1, and 2.2 A and AA rule. It runs in Chrome's engine at desktop and phone sizes and in Safari's engine (WebKit) at phone size. On November 9, 2026, it found no problems.
- **Browser tests** go through the main flow at the same sizes (search, choose a district, read the profile, ask, open a data table, open a source, come back), use the page with the keyboard only, check reflow at 320 px and 200% zoom, increase text spacing, and check text size and link wording.
- **Color checks** measure every color pair from the style sheet, including under simulated color blindness, and save screenshots of the charts and map as people with color vision deficiencies see them.

Not done yet: screen reader testing with NVDA and with VoiceOver on iPhone, and a keyboard and zoom pass by a person in a real browser. The steps are in the [checklist](ACCESSIBILITY-CHECKLIST.md).

## Known problems

- Screen reader testing by a person has not been done yet (see above).
- Some figure labels use long Census table wording ([#26](https://github.com/HAIC-2026/KLEAR/issues/26)).
- On the map, very small districts can be hard to tap on a phone. The list under the map offers every district.
- Answers written by the assistant are checked for figures and sources, but their wording is not checked for plain language.

## Report a problem

[Open a GitHub issue](https://github.com/HAIC-2026/KLEAR/issues). Say which part of the site, what you were trying to do, and which browser and assistive technology you use.
