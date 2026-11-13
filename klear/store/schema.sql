-- url is the address the build downloads from (an API, a CSV or ZIP file, a map service), kept for provenance
-- and listed in manifest.json. page_url is the publisher's page about the dataset, the one people are sent to.
CREATE TABLE IF NOT EXISTS source (
    source_id VARCHAR PRIMARY KEY CHECK (length(source_id) > 0),
    name VARCHAR NOT NULL,
    publisher VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    vintage VARCHAR NOT NULL,
    license VARCHAR NOT NULL,
    page_url VARCHAR NOT NULL CHECK (length(page_url) > 0)
);

CREATE TABLE IF NOT EXISTS geography (
    geo_type VARCHAR NOT NULL CHECK (geo_type IN ('state', 'county', 'senate', 'house', 'tract', 'zcta', 'block')),
    geo_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    parent_county VARCHAR,
    PRIMARY KEY (geo_type, geo_id)
);

CREATE TABLE IF NOT EXISTS indicator (
    indicator_id VARCHAR PRIMARY KEY,
    label VARCHAR NOT NULL,
    unit VARCHAR NOT NULL CHECK (unit IN ('count', 'dollars', 'percent', 'ratio', 'years')),
    universe VARCHAR,
    acs_var VARCHAR,
    additive BOOLEAN NOT NULL,
    -- plain caveat to state with every figure of the indicator, such as what an income-eligible estimate leaves out
    note VARCHAR
);

-- Geography types each indicator is published for. Completeness is checked only where complete is true,
-- partial coverage (a source that publishes only some tracts or ZIP codes) allows missing facts.
CREATE TABLE IF NOT EXISTS indicator_coverage (
    indicator_id VARCHAR NOT NULL REFERENCES indicator (indicator_id),
    geo_type VARCHAR NOT NULL CHECK (geo_type IN ('state', 'county', 'senate', 'house', 'tract', 'zcta', 'block')),
    complete BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (indicator_id, geo_type)
);

-- Named geographies a source does not publish inside an otherwise complete coverage, each with the reason.
CREATE TABLE IF NOT EXISTS coverage_exclusion (
    indicator_id VARCHAR NOT NULL REFERENCES indicator (indicator_id),
    geo_type VARCHAR NOT NULL,
    geo_id VARCHAR NOT NULL,
    reason VARCHAR NOT NULL CHECK (length(reason) > 0),
    PRIMARY KEY (indicator_id, geo_type, geo_id)
);

-- One row describing the build that wrote this store: when, from which code, its exit code and status,
-- and the skipped steps and check results as JSON.
CREATE TABLE IF NOT EXISTS build_info (
    built_at VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    exit_code INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    skipped_steps VARCHAR NOT NULL,
    checks VARCHAR NOT NULL
);

-- Status vocabulary. value_status says what the value is, moe_status what its margin of error is.
-- Each line gives the phrase the agent must use when it reports the fact.
--
-- value_status
--   ok              value is the published estimate. Say "X".
--   top_coded       published as "X+" (open-ended top interval), value holds X. Say "X or more".
--   bottom_coded    published as "X-" (open-ended bottom interval), value holds X. Say "X or less".
--   suppressed      value is NULL, the source did not publish it because too few survey responses,
--                   enrollees or households fall in the area. Say "not available, too few cases for the source to publish".
--   not_applicable  value is NULL, the measure does not apply to this geography. Say "not applicable here".
--
-- moe_status
--   ok              moe holds the published 90% margin of error. Say "X, plus or minus M".
--   controlled      moe is NULL, the estimate is controlled to official population totals and has no
--                   sampling error. Say "X, fixed to official population totals, no margin of error".
--   open_ended      moe is NULL, the value is top or bottom coded. Say "X or more" or "X or less" with no margin.
--   not_computable  moe is NULL, too few sample cases to compute one. Say "X, margin of error not
--                   available, treat as uncertain".
--   not_applicable  moe is NULL, no margin of error applies. Do not give a margin.
--   range           moe is NULL, value_low and value_high bound the value. Say "about X (range L to H)".
--
-- tier
--   D direct, A apportioned, P counted from points, M modeled, C county-level context: published only for
--   counties and the state, so a C fact is never stored for a district, tract or ZIP code.
CREATE TABLE IF NOT EXISTS fact (
    geo_type VARCHAR NOT NULL,
    geo_id VARCHAR NOT NULL,
    indicator_id VARCHAR NOT NULL REFERENCES indicator (indicator_id),
    value DOUBLE,
    moe DOUBLE CHECK (moe IS NULL OR moe >= 0),
    value_status VARCHAR NOT NULL
        CHECK (value_status IN ('ok', 'top_coded', 'bottom_coded', 'suppressed', 'not_applicable')),
    moe_status VARCHAR NOT NULL
        CHECK (moe_status IN ('ok', 'controlled', 'open_ended', 'not_computable', 'not_applicable', 'range')),
    tier VARCHAR NOT NULL CHECK (tier IN ('D', 'A', 'P', 'M', 'S', 'C')),
    source_id VARCHAR NOT NULL REFERENCES source (source_id),
    table_id VARCHAR,
    value_low DOUBLE,
    value_high DOUBLE,
    PRIMARY KEY (geo_type, geo_id, indicator_id, source_id),
    FOREIGN KEY (geo_type, geo_id) REFERENCES geography (geo_type, geo_id),
    CHECK ((value IS NULL) = (value_status IN ('suppressed', 'not_applicable'))),
    CHECK ((moe IS NULL) = (moe_status <> 'ok')),
    CHECK ((value_status IN ('top_coded', 'bottom_coded')) = (moe_status = 'open_ended')),
    CHECK (value IS NOT NULL OR moe_status IN ('not_computable', 'not_applicable')),
    CHECK ((value_low IS NULL) = (value_high IS NULL)),
    CHECK (value_low IS NULL OR (value_low <= value AND value <= value_high)),
    -- tier S (summed) carries a range only when some of its parts were suppressed
    CHECK (tier = 'S' OR (tier IN ('A', 'M') AND value IS NOT NULL) = (value_low IS NOT NULL)),
    CHECK ((moe_status = 'range') = (value_low IS NOT NULL)),
    CHECK (tier NOT IN ('A', 'M') OR value IS NULL OR moe_status = 'range'),
    CHECK (tier <> 'C' OR geo_type IN ('state', 'county'))
);

-- Stored facts that equal a second publication of the same figure (value, margin of error and both
-- statuses), with the page or file that publishes it. The build writes a row only for a fact it compared.
CREATE TABLE IF NOT EXISTS fact_match (
    geo_type VARCHAR NOT NULL,
    geo_id VARCHAR NOT NULL,
    indicator_id VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    match_source_id VARCHAR NOT NULL REFERENCES source (source_id),
    match_url VARCHAR NOT NULL CHECK (match_url LIKE 'https://%'),
    PRIMARY KEY (geo_type, geo_id, indicator_id, source_id, match_source_id),
    FOREIGN KEY (geo_type, geo_id, indicator_id, source_id) REFERENCES fact (geo_type, geo_id, indicator_id, source_id),
    CHECK (match_source_id <> source_id)
);

CREATE TABLE IF NOT EXISTS site (
    site_id VARCHAR PRIMARY KEY CHECK (length(site_id) > 0),
    kind VARCHAR NOT NULL CHECK (kind IN ('public_housing', 'lihtc', 'health_center')),
    name VARCHAR NOT NULL,
    address VARCHAR,
    lat DOUBLE NOT NULL CHECK (lat BETWEEN 18 AND 23),
    lon DOUBLE NOT NULL CHECK (lon BETWEEN -161 AND -154),
    house VARCHAR NOT NULL,
    senate VARCHAR NOT NULL,
    snapped_m DOUBLE NOT NULL CHECK (snapped_m >= 0),
    units INTEGER,
    source_id VARCHAR NOT NULL REFERENCES source (source_id)
);

-- 2020 Census blocks with their county, tract, ZCTA, 2022-plan districts and population.
CREATE TABLE IF NOT EXISTS blocks (
    block VARCHAR PRIMARY KEY,
    county VARCHAR NOT NULL,
    tract VARCHAR NOT NULL,
    zcta VARCHAR,
    house VARCHAR,
    senate VARCHAR,
    pop2020 BIGINT NOT NULL CHECK (pop2020 >= 0)
);

-- Census 2020 places (CDPs) with Hawaiian names, for resolving town names to districts.
CREATE TABLE IF NOT EXISTS place (
    place_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    plain VARCHAR NOT NULL,
    pop2020 BIGINT NOT NULL CHECK (pop2020 > 0)
);

-- 2020 population of each place inside a county, House or Senate district, or ZCTA.
CREATE TABLE IF NOT EXISTS place_overlap (
    place_id VARCHAR NOT NULL REFERENCES place (place_id),
    geo_type VARCHAR NOT NULL CHECK (geo_type IN ('county', 'house', 'senate', 'zcta')),
    geo_id VARCHAR NOT NULL,
    pop2020 BIGINT NOT NULL,
    share DOUBLE NOT NULL CHECK (share > 0 AND share <= 1),
    PRIMARY KEY (place_id, geo_type, geo_id)
);

-- Islands, Honolulu neighborhood board areas and Census county divisions, for resolving names that are not
-- Census places. island is the island holding most of the area's 2020 residents.
CREATE TABLE IF NOT EXISTS area (
    area_id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL CHECK (kind IN ('island', 'neighborhood', 'division')),
    name VARCHAR NOT NULL,
    plain VARCHAR NOT NULL,
    island VARCHAR NOT NULL,
    pop2020 BIGINT NOT NULL CHECK (pop2020 > 0)
);

-- 2020 population of each area inside a county, House or Senate district, ZCTA or Census place.
CREATE TABLE IF NOT EXISTS area_overlap (
    area_id VARCHAR NOT NULL REFERENCES area (area_id),
    geo_type VARCHAR NOT NULL CHECK (geo_type IN ('county', 'house', 'senate', 'zcta', 'place')),
    geo_id VARCHAR NOT NULL,
    pop2020 BIGINT NOT NULL,
    share DOUBLE NOT NULL CHECK (share > 0 AND share <= 1),
    PRIMARY KEY (area_id, geo_type, geo_id)
);

-- Other names people use for an area or Census place, such as Kona or upcountry Maui: plain is the form search
-- matches and name the spelling search suggests.
CREATE TABLE IF NOT EXISTS area_alias (
    plain VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    target_type VARCHAR NOT NULL CHECK (target_type IN ('area', 'place')),
    target_id VARCHAR NOT NULL,
    PRIMARY KEY (plain, target_type, target_id)
);
