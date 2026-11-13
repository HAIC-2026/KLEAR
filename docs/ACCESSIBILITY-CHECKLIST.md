# KLEAR accessibility test checklist

This is the record of how KLEAR was checked against WCAG 2.2 level AA: what was run, when, with which tools, and what it found. The [accessibility statement](ACCESSIBILITY.md) summarizes it for readers.

Fill in a row each time you run a manual check. Write "Pass", "Fail" with the issue number, or "Not done". Keep earlier rows; add new ones below them.

## Automated checks (run in CI on every push)

These run in GitHub Actions in the `browser` job ([ci.yml](../.github/workflows/ci.yml)) and locally with the commands in the [README](../README.md#browser-and-accessibility-tests). The site runs on a small fixture store with a scripted stand-in for the model ([e2e/serve.py](../e2e/serve.py)), so no data build or API key is needed.

| Check | How | Where it runs | Last result |
| --- | --- | --- | --- |
| axe, WCAG 2.0, 2.1 and 2.2 A and AA rules | `@axe-core/playwright` 4.14.0 (axe-core 4.14.0). Serious and critical findings fail the build; moderate and minor ones are attached to the report. | Chromium at 1280 × 800; Chromium on a Pixel 7 screen; WebKit on an iPhone 13 screen | November 9, 2026: no findings of any impact on the landing page, search results, district profile (all sections open), an answer with charts, tables and sources open, a declined and a failed question, and the map with a ZIP code and a picked district. November 12, 2026: also none on a district's page at 900 px wide, on its Profile tab, its Ask tab, and with a new answer marked |
| Main flow | Search a town, choose a district (its page opens at the top, with focus on its name), read the headline figures, open a section from the links at the top, ask, open a chart's data table, open a source in a new tab, return | Same three | November 12, 2026: pass |
| Keyboard only | Skip link, search, the district's name takes focus, "Skip to the question box", ask, Tab into the answer, open a data table, ask a suggested question; the Ask button keeps focus while an answer loads; Tab into the map, arrow keys, Enter to pick, Enter to ask; Profile and Ask tabs with the arrow keys at 900 px wide; a visible focus outline on every stop of the home page and a district's page | Chromium, desktop | November 12, 2026: pass |
| District page | At 1440 × 900 the first screen shows the district's name, its four headline figures and six more figures with no click, and an answer with a chart has a conversation area 600 px or more tall, which stays in view as the profile scrolls; at 390 × 844 the Profile and Ask tabs are on the first screen; each section is one click from the top and links back up; opened sections are remembered for the tab | Same three; 1440 × 900 on Chromium, desktop | November 12, 2026: pass |
| Map hover | A district under the mouse gets a darker fill and an outline drawn over its neighbors, different from the focus ring; both go when the mouse leaves | Chromium, desktop | November 9, 2026: pass |
| Reflow at 320 px (1280 px at 400%) | No sideways scrolling with every part of the page open | Same three | November 9, 2026: pass |
| 200% zoom (640 px wide) | No sideways scrolling; the question box and its button stay usable | Same three | November 9, 2026: pass |
| Text size | No visible text under 12 px | Same three | November 9, 2026: pass (smallest is 13 px; the map's district numbers, which are not checked, are 12 px) |
| Text spacing (WCAG 1.4.12) | Line height 1.5, letter spacing 0.12 em, word spacing 0.16 em, paragraph spacing 2 em; no sideways scrolling and no clipped buttons or labels | Same three | November 9, 2026: pass |
| Links | Only links that leave the site have the arrow; links that open a new tab say so to screen readers | Same three | November 9, 2026: pass |
| Hawaiian words | kūpuna, keiki and ʻohana are marked `lang="haw"`; place names are not | Same three | November 9, 2026: pass |
| Reduced motion | Smooth scrolling and animations stop | Same three | November 9, 2026: pass |
| Contrast and color blindness | Every text, border, bar, map and map hover color pair checked from the CSS tokens, including under simulated protanopia, deuteranopia and tritanopia ([test_web_palette.py](../tests/agent/test_web_palette.py)) | pytest, every OS | November 9, 2026: pass |
| Color blindness screenshots | Search results, a chart, and the map, as seen with protanopia, deuteranopia, tritanopia and achromatopsia (Chromium's simulation). Saved in the `browser-test-results` CI artifact for review | Chromium, desktop | November 9, 2026: reviewed; the asked-about district's bar, ZIP code districts, the picked district, the hovered district and the focused district stay distinct in all five |

## Manual checks

Automated tools find roughly a third of accessibility problems. These checks need a person.

### Keyboard only, in a real browser

Use Chrome or Firefox on Windows or macOS, with no mouse or trackpad.

1. Load the page and press Tab. The "Skip to content" link appears; press Enter, then Tab to the search box.
2. Search for "Kāneʻohe", Tab to a district button, press Enter. Focus moves to the question box.
3. Type a question, Tab to "Ask KLEAR", press Enter. When the answer arrives, focus is on its heading.
4. Tab through the answer: chart data tables and Sources open with Enter, and source links open in a new tab.
5. Tab to the map. Arrow keys move between districts, Home and End jump to the first and last, Enter picks, Enter again asks.
6. Check that focus is always visible and never hidden behind anything, and that nothing traps focus.

| Date | Tester | Browser and OS | Result |
| --- | --- | --- | --- |
| | | | Not done |

### NVDA with Chrome or Firefox (Windows)

Install [NVDA](https://www.nvaccess.org/download/). Use browse mode (arrows) to read and focus mode (Tab) for forms.

1. Press H to move by heading: page title, then each section, then "Questions to try". On a district's page, the district's name comes first.
2. Search for a town. NVDA reads "Choose a district…" once, and each button reads like "House 22 75% of Kāneʻohe residents".
3. Choose a district. Focus moves to its name, the page heading. NVDA says "Profile for House District 22 loaded." and does not read the whole profile.
4. Ask a question. NVDA says "Checking district data and sources…", then "Answer ready for House District 22." and reads the answer heading. It does not read the whole answer by itself.
5. Read a chart: NVDA reads the chart title and its summary sentence, skips the bars, and reads the data table with row and column headers.
6. On a source link, NVDA says "opens in a new tab".
7. On the map, each district reads as a button with its name and island, and "pressed" once picked.
8. With a Hawaiian voice installed, "kūpuna" is read in Hawaiian; without one, it is read normally.

| Date | Tester | NVDA version, browser | Result |
| --- | --- | --- | --- |
| | | | Not done |

### VoiceOver on iPhone (Safari)

Turn on VoiceOver in Settings, Accessibility. Swipe right to move, double-tap to activate, use the rotor for headings.

1. Steps 1 to 7 from the NVDA list, with swipes and the rotor instead of keys.
2. The search box, question box and buttons are each announced with their labels.
3. Pinch-zoom works on the page, and the map can be used through the list under it.

| Date | Tester | iOS version | Result |
| --- | --- | --- | --- |
| | | | Not done |

### 200% browser zoom and 320 px reflow, in a real browser

1. In Chrome, set zoom to 200% (Ctrl and +) on a 1280 px wide window. Repeat the main flow; nothing overlaps or is cut off, and there is no sideways scrolling.
2. Set zoom to 400%. The page reflows to one column with no sideways scrolling, and the source table becomes a list.

| Date | Tester | Browser | Result |
| --- | --- | --- | --- |
| | | | Not done |
