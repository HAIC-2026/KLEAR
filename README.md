# KLEAR

KLEAR answers questions about Hawaiʻi's 51 House and 25 Senate legislative districts for the HAIC 2026 Know Your District challenge. A data pipeline builds a district-level fact base from Census, ACS, federal and state sources into DuckDB, with every fact tied to a source. Program eligibility rules live in YAML files. An agent built on Claude answers questions using only the store, and an eval checks its answers against a set of test questions.

## Quick start

Requires Python 3.12 or later and uv.

    git clone https://github.com/HAIC-2026/KLEAR.git
    cd KLEAR
    uv sync --locked

Run the tests and the linter. The default test run is offline and needs no keys. Tests that need the built store skip themselves until it exists:

    uv run pytest -q
    uv run ruff check && uv run ruff format --check

Build the data store. This needs network access, a free Census API key and two hand downloads, all described under Running and testing below, and takes about 5 minutes. It writes data/build/klear.duckdb:

    uv run python -m klear.etl build

Run the free eval. It plays saved model replies from eval/scripts through the real agent against the store, makes no API call and costs nothing. It needs the built store with its manifest.json beside it, or pass --store PATH to use a build elsewhere. It exits 0 when every check passes and writes its report to eval/reports:

    uv run python -m klear.eval run --mode replay --agent scripted

Live runs call the Claude API, need ANTHROPIC_API_KEY in the environment and cost money. Spending is tracked and capped at 60 dollars per user. Read eval/README.md before running one:

    uv run python -m klear.agent "How many kūpuna live in House District 22?"
    uv run python -m klear.eval run --mode live --agent klear --model claude-haiku-5-5

## Layout

- klear/etl: downloads the sources and builds the store (python -m klear.etl build).
- klear/store: the DuckDB schema and the writer that rejects unsourced facts.
- klear/validation: the build's validation checks and the docs/VALIDATION.md report.
- klear/rules: loads the program rules and checks each value against its official source quote.
- klear/agent: the agent, its data tools and the code that builds and checks each answer (python -m klear.agent).
- klear/eval: the answer format, eval runner, answer checks, reports and spending cap (python -m klear.eval run).
- data: the checked-in indicator catalog (data/catalog), Hawaiian place names (data/places) and program rules (data/rules). The build also writes data/raw (download cache), data/manual (hand downloads) and data/build (the store), none of them tracked.
- eval: test questions (eval/golden), saved model replies (eval/scripts), recorded live answers (eval/recordings) and model prices. Run reports go to eval/reports, which is not tracked.
- tests: the pytest suite, laid out like the klear packages, with small fixtures in tests/fixtures.

## Running and testing

Setup. Install uv, then create the environment from the lock file:

    uv sync --locked

Census API key. The build reads CENSUS_API_KEY from the environment or from the file ~/.config/klear/env, one line:

    CENSUS_API_KEY=your-key

The key is never printed, logged or saved. Request URLs in logs and provenance files show it as key=***.

Build. One command downloads the sources, builds the fact store, runs every validation check and writes the report:

    uv run python -m klear.etl build

- --refresh downloads every raw file and Census API response again instead of using the cache in data/raw.
- --skip-network-checks serves Census API calls only from the cache. Checks the cache cannot serve are skipped and reported as skipped. A fully cached build needs no API key.

Manual downloads. huduser.gov serves two files only to a browser. Download them by hand and save them under data/manual/ with these names. Until both are there, the build skips the steps that need them, names the file and its URL, and exits 2.

- 2018thru2022-140-csv.zip from https://www.huduser.gov/portal/datasets/cp/2018thru2022-140-csv.zip (HUD CHAS 2018-2022, tract level).
- Section8-FY26.xlsx from https://www.huduser.gov/portal/datasets/il/il26/Section8-FY26.xlsx (HUD FY2026 income limits). Then fill in data/rules/hud_il_fy2026_hi.yaml from it and set its status to confirmed.

The build hashes each manual file once, in a sidecar next to it, and lists it with the raw files in the manifest. Replacing a manual file later fails the raw file hash check. If the new file is intended, delete its .meta.json sidecar and rebuild.

The store in data/build/klear.duckdb is replaced only when no required check failed and every skipped step was skipped for a missing manual input. Otherwise the previous store is kept, and the console, manifest.json (store) and VALIDATION.md say why. The store's build_info table records the build that wrote it: time, code commit, exit code, status, skipped steps and check results.

The last line of the build output gives the exit code and its meaning:

- 0: all required checks ran and passed.
- 1: a required check failed.
- 2: the build is incomplete because a required check was skipped.

Tests. The default run is offline. Tests that call the live Census API and state servers carry the network marker:

    uv run pytest
    uv run pytest -m network
    uv run ruff check && uv run ruff format --check

Outputs in data/build:

- klear.duckdb: the fact store.
- manifest.json: build time, code commit, skipped checks, store counts and every raw file with its source URL, retrieval time and SHA-256 hash.
- validation.json: full results of every check.
- house.geojson and senate.geojson: simplified district polygons for the web.

The build also rewrites docs/VALIDATION.md, the public validation report.

## Fact store

The DuckDB store in data/build/klear.duckdb has these tables (schema in klear/store/schema.sql):

- source: every publisher and dataset a fact can come from. A fact without a registered source cannot be stored.
- geography: the state, the counties, the House and Senate districts, and the 2020 tracts and ZCTAs of the block table (geo_type, geo_id, name, parent_county).
- indicator: the curated catalog from data/catalog/indicators.yaml, with label, unit, universe, ACS variable and additive (counts and dollar totals that may be summed across units).
- indicator_coverage: the geography types each indicator is declared for, each marked complete or partial. Every geography of a complete type must have a fact. Partial coverage is for sources that publish only some tracts or ZIP codes.
- coverage_exclusion: named geographies a source does not publish inside a complete coverage, with the reason (IRS and CMS publish no Kalawao County rows).
- build_info: one row describing the build that wrote the store.
- fact: one value per geography, indicator and source, with its margin of error, value_status, moe_status, tier (D direct, A apportioned, P points, M modeled, C context) and source table. Tier A and M facts with a value carry value_low and value_high with moe_status range, and tier D and P facts leave both empty.
- site: public housing, LIHTC and health center points with their House and Senate district, the meters the point was snapped to reach it, and source.

Sources beyond the Census Bureau, each moved to House and Senate districts with a range: IRS SOI tax year 2023 returns and earned income tax credit by ZIP code, CMS 2026 marketplace enrollment by ZIP code, HUD housing choice vouchers by tract, CDC PLACES 2025 modeled need by tract and HUD CHAS 2018-2022 cost burden by tract. HUD public housing, LIHTC projects and HRSA health center sites are stored as points with their districts. Program rules (poverty guidelines, SNAP, WIC, EITC, SSI and others) live in data/rules/ as YAML, and every build checks each confirmed value against a quote on its official page.

Ranges. For counts moved from ZIP codes or tracts, value_low counts only units wholly inside the district, and value_high counts every unit touching it plus the most a suppressed unit touching it could hide. The IRS 99999 amount has no known location: it is assigned to districts by 2020 population and value_high includes only the district's population share of it, so its true district share is not inside the guaranteed range. A CMS county residual is placed in that county's suppressed ZIP codes and counted whole in value_high of every district they touch, but the residual location is assumed, not published. PLACES district ranges average the tract confidence limits with the same weights as the estimate, so they assume tract errors move together and are wider than a formal interval, and a suppressed tract counts as 0 in value_low and 100 percent in value_high.

## Status vocabulary

Every fact carries two statuses. The agent must report each fact with the phrase given here.

value_status, what the value is:

| Status | Meaning | Agent says |
| --- | --- | --- |
| ok | the published estimate | "X" |
| top_coded | published as "X+", value holds X | "X or more" |
| bottom_coded | published as "X-", value holds X | "X or less" |
| suppressed | not published by the source because too few survey responses, enrollees or households fall in the area, value is empty | "not available, too few cases for the source to publish" |
| not_applicable | the measure does not apply here, value is empty | "not applicable here" |

moe_status, what the margin of error is:

| Status | Meaning | Agent says |
| --- | --- | --- |
| ok | the published 90% margin of error | "X, plus or minus M" |
| controlled | estimate fixed to official population totals, no sampling error | "X, fixed to official population totals, no margin of error" |
| open_ended | the value is top or bottom coded | "X or more" or "X or less", with no margin |
| not_computable | too few sample cases to compute a margin | "X, margin of error not available, treat as uncertain" |
| not_applicable | no margin of error applies | no margin given |
| range | moe is NULL, value_low and value_high bound the value | "about X (range L to H)" |

Raw Census and DBEDT codes map onto these statuses in klear/etl/acs_values.py and nowhere else.
