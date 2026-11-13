# How each stored fact was produced, in the words the assistant and the site use.
PLACES_TRACT = "cdc_places_2025"
PLACES_COUNTY = "cdc_places_2025_county"
HUD_HCV = "hud_hcv_tract"
CMS = "cms_oep_2026"
IRS = "irs_soi_zip_2023"
BLS = "bls_laus_county"
DOTAX = "hi_dotax_credits_2023"

# Method by fact tier.
TIER_METHODS = {
    "D": "published for this geography",
    "A": "apportioned estimate: moved to the district from ZIP code or tract data, the range shows how much "
    "that move could change it",
    "M": "modeled estimate: the range is a wide uncertainty band, not a formal confidence interval",
    "P": "counted from site locations",
    "S": "sum of the published counts for the smaller areas inside this place",
    "C": "county-level figure: published only for counties and the state, not for districts; name its county "
    "and never call it the district's figure",
}
# Method for apportioned sources whose range does not bound every part of the value, and for county-level sources
# with a caveat of their own.
SOURCE_METHODS = {
    IRS: "apportioned estimate: moved to the district from IRS ZIP code data; the range covers returns with a "
    "mapped ZIP code, but returns from small or unlisted ZIP codes are spread by population and are not "
    "bounded by it",
    CMS: "apportioned estimate: moved to the district from ZIP code data; enrollees the source did not "
    "publish by ZIP code are placed by assumption, so the range is not a guaranteed bound",
    BLS: "county-level figure: monthly BLS estimate for the whole county, not seasonally adjusted, not published "
    "for districts; name its county and never call it the district's figure",
    DOTAX: "county-level figure: Department of Taxation claims for the whole taxation district (the county), not "
    "published for districts, and the Oʻahu figures include nonresidents with an out-of-state address; name its "
    "county and never call it the district's figure",
}
# Tiers whose method a source can replace.
SOURCE_METHOD_TIERS = ("A", "C")
# Method for facts of one source at one geography type, where the tier alone would not say how they were made.
PLACE_METHODS = {
    (PLACES_TRACT, "tract"): "modeled estimate published by CDC for the tract: the range is CDC's 95 percent "
    "confidence interval",
    (PLACES_COUNTY, "county"): "modeled estimate published by CDC for the county: the range is CDC's 95 "
    "percent confidence interval",
    (PLACES_COUNTY, "state"): "modeled estimate derived by KLEAR, not published by CDC: the average of the "
    "five CDC county estimates weighted by adult population. The range averages the county confidence limits "
    "the same way, so it is wider than a formal confidence interval",
    (HUD_HCV, "county"): "sum of the published tract counts in the county: HUD does not publish tracts with "
    "10 or fewer voucher holders, so the range adds up to 10 for each of them",
    (HUD_HCV, "state"): "sum of the published tract counts in the state: HUD does not publish tracts with "
    "10 or fewer voucher holders, so the range adds up to 10 for each of them",
    (CMS, "state"): "sum of the published county counts: CMS publishes no rows for Kalawao County",
}
# Short notes for values KLEAR derived for a place, shown next to them when they are compared.
PLACE_NOTES = {
    (PLACES_COUNTY, "state"): "average of the CDC county estimates",
    (HUD_HCV, "county"): "sum of the published tract counts",
    (HUD_HCV, "state"): "sum of the published tract counts",
    (CMS, "state"): "sum of the published county counts",
}


def fact_method(tier: str, source_id: str, geo_type: str) -> str:
    """How a fact was produced, or '' for an unknown tier."""
    if (source_id, geo_type) in PLACE_METHODS:
        return PLACE_METHODS[(source_id, geo_type)]
    if tier in SOURCE_METHOD_TIERS and source_id in SOURCE_METHODS:
        return SOURCE_METHODS[source_id]
    return TIER_METHODS.get(tier, "")


def place_note(source_id: str, geo_type: str) -> str:
    """Short note for a value KLEAR derived for a place, or '' for a value published or counted there."""
    return PLACE_NOTES.get((source_id, geo_type), "")
