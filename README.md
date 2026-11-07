# KLEAR

KLEAR answers questions about Hawaiʻi's 51 House and 25 Senate legislative districts for the HAIC 2026 Know Your District challenge. A data pipeline builds a district-level fact base from Census, ACS, federal and state sources into DuckDB, with every fact tied to a source. Program eligibility rules live in YAML files. An agent built on Claude answers questions using only the store, and an eval checks its answers against a set of test questions.

## Run the site locally

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The same commands work in PowerShell on Windows, Terminal on macOS, and Linux.
Run them from the project root. No virtual environment activation or separate frontend server is needed.
Use a native Windows checkout in PowerShell or a native macOS checkout in Terminal;
WSL is not required. Run `uv sync --locked` on each machine rather than copying `.venv`
between operating systems.

```sh
git clone https://github.com/HAIC-2026/KLEAR.git
cd KLEAR
uv sync --locked
uv run python -m klear.web --basic-only --open
```

If you already have this checkout, start with `uv sync --locked`.
The last command starts the server at [http://127.0.0.1:8000](http://127.0.0.1:8000)
and opens it in your default browser. Keep the terminal running; press **Ctrl+C** to stop.
`--basic-only` guarantees no model API calls, even if you have an Anthropic key configured.
Omit `--open` if you prefer to open the URL yourself.

**The landing page can be previewed immediately, without keys or downloaded data.**
District search and chat need `data/build/klear.duckdb`. If it is missing, the terminal
prints the build command and the page explains that district lookup is unavailable.
Follow [Build the data store](#build-the-data-store) once to enable those features.

After the store is built, your everyday run command is:

```sh
uv run python -m klear.web --open
```

With `ANTHROPIC_API_KEY` configured, this enables the paid assistant under the daily cap.
Without that key, the site automatically uses free basic lookups from the store.
The terminal reports the URL, data store path, and active mode on startup.

If uv is unavailable after setup, use the environment's Python directly:

| Platform | Run from the project root |
| --- | --- |
| Windows (PowerShell) | `.\.venv\Scripts\python.exe -m klear.web --basic-only --open` |
| macOS (Terminal) | `.venv/bin/python -m klear.web --basic-only --open` |

Remove `--basic-only` to enable the assistant when you have an Anthropic key configured.

### Local keys

Create a `.env` file in the project root using [.env.example](.env.example) as a template.
If `.env` already exists, keep it and add only the missing settings:

```dotenv
CENSUS_API_KEY=your-census-key
ANTHROPIC_API_KEY=
```

- `CENSUS_API_KEY`: a free Census API key, needed for the first data build.
- `ANTHROPIC_API_KEY`: optional; fill it in to enable Claude answers. Those calls cost money.

KLEAR reads keys automatically from the process environment, then the project's `.env`,
then `~/.config/klear/env`, in that order. Blank entries fall through to the next location.
On Windows, the last path is `%USERPROFILE%\.config\klear\env`.
No `source`, environment export, or `--env-file` option is needed. Restart the web server after changing keys.
`.env` is ignored by Git; keys stay on the server and are never sent to the browser.

### Local troubleshooting

- **`uv` is not recognized:** install uv using the link above, then reopen your terminal.
  An already synced checkout can use the platform-specific Python command in the table above.
- **Port 8000 is already in use:** run `uv run python -m klear.web --port 8001 --open`.
- **The page opens but district search fails:** build the store below, or use `--store PATH`
  to select an existing `klear.duckdb`.
- **The site uses basic lookups:** check `ANTHROPIC_API_KEY` in `.env`, restart the server,
  and leave off `--basic-only`. The site also falls back to basic lookups if the daily cap
  is reached or the API is unavailable.

## Layout

- klear/etl: downloads the sources and builds the store (python -m klear.etl build).
- klear/store: the DuckDB schema and the writer that rejects unsourced facts.
- klear/validation: the build's validation checks and the docs/VALIDATION.md report.
- klear/rules: loads the program rules and checks each value against its official source quote.
- klear/agent: the agent, its data tools and the code that builds and checks each answer (python -m klear.agent).
- klear/web: the local web server, landing page and chat interface (python -m klear.web).
- klear/eval: the answer format, eval runner, answer checks, reports and spending cap (python -m klear.eval run).
- data: the checked-in indicator catalog (data/catalog), Hawaiian place names (data/places) and program rules (data/rules). The build also writes data/raw (download cache), data/manual (hand downloads) and data/build (the store), none of them tracked.
- eval: test questions (eval/golden), saved model replies (eval/scripts), recorded live answers (eval/recordings) and model prices. Run reports go to eval/reports, which is not tracked.
- tests: the pytest suite, laid out like the klear packages, with small fixtures in tests/fixtures.

## Build the data store

Add `CENSUS_API_KEY` to `.env` as described above, then run:

```sh
uv run python -m klear.etl build
```

This downloads public datasets, builds the fact store, runs validation checks and writes the report.
It needs internet access; the first build can take several minutes and later builds reuse `data/raw`.
It makes no Claude calls. Request URLs in logs and provenance files mask the Census key as `key=***`.

- --refresh downloads every raw file and Census API response again instead of using the cache in data/raw.
- --skip-network-checks serves Census API calls only from the cache. Checks the cache cannot serve are skipped and reported as skipped. A fully cached build needs no API key.

Manual downloads. huduser.gov serves two files only to a browser. Download them by hand and save them under `data/manual/` with these names. Until both are there, the build skips the steps that need them, names the file and its URL, and exits 2. When all other required checks pass, it still writes a store usable for local district search and basic chat; the missing HUD data is unavailable.

- 2018thru2022-140-csv.zip from https://www.huduser.gov/portal/datasets/cp/2018thru2022-140-csv.zip (HUD CHAS 2018-2022, tract level).
- Section8-FY26.xlsx from https://www.huduser.gov/portal/datasets/il/il26/Section8-FY26.xlsx (HUD FY2026 income limits). Then fill in data/rules/hud_il_fy2026_hi.yaml from it and set its status to confirmed.

The build hashes each manual file once, in a sidecar next to it, and lists it with the raw files in the manifest. Replacing a manual file later fails the raw file hash check. If the new file is intended, delete its .meta.json sidecar and rebuild.

The store in data/build/klear.duckdb is replaced only when no required check failed and every skipped step was skipped for a missing manual input. Otherwise the previous store is kept, and the console, manifest.json (store) and VALIDATION.md say why. The store's build_info table records the build that wrote it: time, code commit, exit code, status, skipped steps and check results.

The last line of the build output gives the exit code and its meaning:

- 0: all required checks ran and passed.
- 1: a required check failed.
- 2: the build is incomplete because a required check was skipped.

Outputs in data/build:

- klear.duckdb: the fact store.
- manifest.json: build time, code commit, skipped checks, store counts and every raw file with its source URL, retrieval time and SHA-256 hash.
- validation.json: full results of every check.
- house.geojson and senate.geojson: simplified district polygons for the web.

The build also rewrites docs/VALIDATION.md, the public validation report.

## Tests and evaluation

The default test run is offline and needs no keys. Tests that need a built store skip
until one exists. Windows runs natively without WSL; process tests that require Unix
`fork` or `SIGKILL` skip on Windows, with threaded tests covering file locking there.
GitHub Actions is configured to run the suite, lint and formatting checks on Windows,
macOS and Linux with Python 3.12, 3.13 and 3.14. The suite includes a real subprocess
startup check that serves the page, assets, district lookup and free chat from a temporary store.

```sh
uv run pytest -q
uv run ruff check
uv run ruff format --check
```

Tests that call the live Census API and state servers carry the `network` marker.
Run those explicitly with `uv run pytest -m network`.

The free eval plays saved model replies from `eval/scripts` through the agent against
the store. It makes no API calls. It needs the built store and its adjacent `manifest.json`,
or `--store PATH` to select another build. Reports go to `eval/reports`:

```sh
uv run python -m klear.eval run --mode replay --agent scripted
```

Live CLI and eval runs need `ANTHROPIC_API_KEY` and cost money. Their spending is
tracked separately from the website and capped at 60 dollars per user.
Read [eval/README.md](eval/README.md) before running them:

```sh
uv run python -m klear.agent "How many kūpuna live in House District 22?"
uv run python -m klear.eval run --mode live --agent klear --model claude-haiku-5-5
```

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

## Landing page and chat

See [Run the site locally](#run-the-site-locally) for startup instructions.
With an Anthropic key configured, questions go to the same KLEAR assistant that
`python -m klear.agent` runs. Without the key, the site uses free basic lookups.

Website spending has its own cap of 5 dollars per HST day, kept in ~/.config/klear/web_spend.json and separate from the 60 dollar eval cap. Each question reserves its worst-case cost first and then records the actual cost. When the day's cap is reached or the API fails, the site falls back to a basic keyword lookup from the store and says so. Each visitor can ask 6 questions a minute, and the server answers 3 at a time.

| Option | Purpose |
| --- | --- |
| `--open` | Open the site in your default browser after binding the server. |
| `--basic-only` | Use free lookups only, even if an Anthropic key is present. |
| `--port 8001` | Choose a different port; the default is 8000. |
| `--host 127.0.0.1` | Choose the bind address; the default is local access only. |
| `--store PATH` | Use a different DuckDB store. |
| `--daily-cap 5` | Set the website's model budget in dollars per HST day. |
| `--model MODEL` | Choose the model used by the assistant. |

The landing page uses the project-provided `background1.png` photo, copied
as a smaller metadata-free `klear/web/static/background1.jpg`. It loads no
third-party images, fonts, scripts, or analytics.

The district map draws the official 2022 House and Senate boundaries as plain SVG, with no map
tiles or map library. Its shapes, and the share of each ZIP area's residents in each district,
live in klear/web/static/districts.json, so the map works
without a data build. After a build changes the boundaries, regenerate it with:

    uv run python -m klear.etl.mapshapes

The landing page resolves House/Senate numbers, towns and ZIPs using the
built store. Overlaps are shown as choices instead of guessing a district.
Chat includes example questions, a waiting state, explicit refusal labels,
and figure cards with uncertainty, vintage, geography and official source
links. Program questions list official guidance without assessing individual
eligibility. Questions outside the available measures receive a clear limit
instead of a guessed answer. Questions are independent: previous messages
are displayed locally but are not sent as conversation history.

Only the static web assets are served; the repository and store cannot be
downloaded. The server rejects cross-origin browser requests. HTTP logs are disabled to avoid recording questions.

For judging access from other devices, deploy behind an HTTPS reverse proxy
with request limits. The included Python HTTP server is a demo entry point,
not a hardened public hosting service. Keep the built store on the server.

Delivery checkpoints (HST): usable for video before November 16, 2026 at
9:00 a.m.; finals demo ready November 21, 2026. Before recording, build and
validate the real store, run a live question and refusal with the operator's
server key, check the deployed URL on mobile, and confirm all source links.
