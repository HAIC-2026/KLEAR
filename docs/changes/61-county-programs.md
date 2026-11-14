# County programs

District program lists now include county services only where Census blocks show residents in that county. Any positive population qualifies, including districts that cross county lines. Empty or omitted county lists retain statewide coverage. Program search accepts an explicit district, or uses districts resolved in the session. Without a district it returns only statewide entries.

The catalog adds six county services: the Hawaiʻi, Maui and Kauaʻi aging offices, Maui and Kauaʻi housing choice voucher offices, and Honolulu's home exemption. Each YAML entry records the official page, phone source, date checked and verification notes. The Hawaiʻi aging page was read through its indexed official content because direct access returned 403.

`level` describes the administering government, not the funding source. Thus state-run SNAP and county-run federal vouchers show State and their county respectively. Existing entries receive explicit levels. Optional levels preserve old catalog compatibility, with Government as the display fallback. County codes are five-digit Census codes. The new owner housing cost burden need figure supports property tax relief.

Information-only entries may use `rules: []`. This means KLEAR does not model their numeric eligibility thresholds, even if the linked program has such thresholds. Official guidance, a phone source, need figures, verified date and verification notes remain required. Answers and evals accept these entries' program IDs while retaining existing rule IDs for threshold-backed programs. All program rows show their level, including printed rows.

## Referral line decision

Do not add Aloha United Way 211 in this change. Keep the government program catalog's HTTPS `.gov` requirement. A nonprofit referral line deserves a separate resource type and review policy rather than being labeled as a government program. County aging offices already provide local referrals within this scope.

## Validation

The catalog loads all eighteen entries. County filtering, zero-population blocks, districts spanning counties, optional fields, retained verification requirements and answer rows have regression coverage. Both new neighbor-island golden scripts replay through the agent and pass the scorers using a fixture store. New county descriptions and audience text score about grade seven.

The full production-store replay is blocked in this checkout: `data/build/klear.duckdb` is absent. A build attempt could not load the store because the Census API key and required cached data are missing; it also reported missing manual source files. A fixture replay does not replace this final production-data check.

The browser suite's unrelated keyboard-hint assertion now accepts both Ctrl and Command, matching the app's platform-specific hint on iPhone WebKit. The print regression checks the new State label.

Checks completed: Ruff lint and format checks pass, as do reading-level and ʻokina tests. The full pytest run had 1,720 passes and 127 skips; its two prompt assertions pass after the fix and failed-test rerun (1,722 passing tests overall). The Playwright run had 154 passes and 13 skips; its one platform-hint failure passes after the fix and rerun (155 passing browser tests overall). Production-store tests skip without the built store.
