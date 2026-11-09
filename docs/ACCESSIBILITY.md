# KLEAR accessibility statement

KLEAR aims to meet the Web Content Accessibility Guidelines (WCAG) 2.1 at level AA. This page says what is in place, how it has been checked so far, what is known not to work yet, and how to report a problem.

Last updated: November 8, 2026.

## What is in place

- A "Skip to content" link, labels on every form field, and a visible outline on whatever has keyboard focus.
- Three ways to pick a district: a search by district, town, or ZIP code; a map you can use with the keyboard (arrow keys move between districts, Enter picks one, and Enter again asks about it); and a plain list of all districts.
- Text is at least 12 px. Measured contrast on white: body text 16:1, secondary text 7.4:1, links and buttons 8.3:1, text field borders 4.6:1, placeholder text 5.7:1.
- The ↗ arrow marks only links that leave the site.
- The loading indicator, highlight animation, and smooth scrolling stop when your device asks for reduced motion.
- The page fits a 320 px wide screen without scrolling sideways.

## How it has been checked

- November 8, 2026: an automated check in headless Microsoft Edge at 1440, 1366, 1280, 390, and 320 px wide found no sideways scrolling, no visible text under 12 px, and no script errors. Contrast ratios were calculated from the colors in the site's stylesheet.

Not done yet: an automated axe scan, a keyboard-only pass, screen reader testing (NVDA and VoiceOver), and a 200% zoom check. These are planned in [issue #23](https://github.com/HAIC-2026/KLEAR/issues/23).

## Known problems

- Screen readers read each new answer in full, including every figure and the source list, because the conversation is announced as it changes ([#23](https://github.com/HAIC-2026/KLEAR/issues/23)).
- Bar charts have no text summary or table alternative yet ([#23](https://github.com/HAIC-2026/KLEAR/issues/23)).
- Some figure labels use long Census table wording ([#26](https://github.com/HAIC-2026/KLEAR/issues/26)).

## Report a problem

[Open a GitHub issue](https://github.com/HAIC-2026/KLEAR/issues). Say which part of the site, what you were trying to do, and which browser and assistive technology you use.
