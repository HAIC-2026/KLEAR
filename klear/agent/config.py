# Limits, defaults and lookup tables for the KLEAR agent and its tools.
MAX_CALLS = 6
MAX_TOKENS = 1500
INPUT_CAP = 30000
DEFAULT_MODEL = "claude-haiku-5-5"
CLIENT_MAX_RETRIES = 0
CLIENT_TIMEOUT_S = 60
# Safety reviewer: at most REVIEW_CALLS small calls per question, each capped in input and output.
REVIEW_CALLS = 2
REVIEW_MAX_TOKENS = 300
REVIEW_INPUT_CAP = 6000
# Waits before each retry of a rate-limited (429) or overloaded (529) request.
RETRY_DELAYS_S = (2, 4, 8)
# Per-model request settings; keys match eval/prices.yaml. params go to count_tokens and create.
# force_submit: the model accepts a forced tool_choice, so the last call forces submit_answer
# (Sonnet 5.5 and Opus 5.5 return a 400 for any forced tool_choice).
# Opus 5.5 always thinks and thinking counts inside MAX_TOKENS, so its replies may hit the limit more often.
REQUEST_SETTINGS = {
    "claude-haiku-5-5": {"params": {"thinking": {"type": "disabled"}}, "force_submit": True},
    "claude-sonnet-5-5": {
        "params": {"thinking": {"type": "between_tools"}, "output_config": {"effort": "high"}},
        "force_submit": False,
    },
    "claude-opus-5-5": {"params": {"output_config": {"effort": "low"}}, "force_submit": False},
}
SEARCH_LIMIT = 12
SITE_LIMIT = 20
MAX_EDIT_DISTANCE = 2
# Query words expanded to the words used in indicator labels.
ALIASES = {
    "kupuna": ("65", "over"),
    "keiki": ("under", "18"),
    "rent": ("rent", "renter"),
    "renting": ("renter",),
    "rental": ("rent", "renter"),
    "tenure": ("renter", "owner", "occupied"),
    "homeowner": ("owner", "occupied"),
    "household": ("household", "occupied"),
}
# Agency behind each rule file, since rule files carry only the program name.
AGENCIES = {
    "fpg_2026_hi": "U.S. Department of Health and Human Services",
    "hud_il_fy2026_hi": "U.S. Department of Housing and Urban Development",
    "snap_fy2027": "U.S. Department of Agriculture, Food and Nutrition Service",
    "wic_2026_27": "Hawaiʻi State Department of Health, WIC Services Branch",
    "eitc_federal_ty2025": "Internal Revenue Service",
    "eitc_federal_ty2026": "Internal Revenue Service",
    "ssi_federal_2026": "Social Security Administration",
    "preschool_open_doors": "Hawaiʻi Department of Human Services",
    "h_heap_fy2026": "Hawaiʻi Department of Human Services",
    "medquest_2026": "Hawaiʻi Department of Human Services, Med-QUEST Division",
    "hi_state_credits": "Hawaiʻi Department of Taxation",
    "aca_ptc_2026": "Internal Revenue Service",
    "ssi_hi_supplement": "Hawaiʻi Department of Human Services",
    "kupuna_care": "Hawaiʻi Executive Office on Aging, Department of Health",
    "hpha_limits": "Hawaiʻi Public Housing Authority",
}
# Hawaiian group words by their plain spelling, as an answer must write them.
GROUP_WORDS = {"kupuna": "kūpuna", "keiki": "keiki", "ohana": "ʻohana"}
# The standard district profile: section titles and their indicators, read from the store with no model call.
PROFILE_SECTIONS = (
    (
        "People",
        (
            "pop_total",
            "pop_65_plus",
            "pct_65_plus",
            "pop_under_18",
            "median_age",
            "pop_native_hawaiian_alone_or_combo",
            "pct_limited_english",
            "civilian_veterans",
            "pct_disability",
        ),
    ),
    (
        "Income",
        (
            "median_household_income",
            "per_capita_income",
            "pct_people_poverty",
            "pct_children_poverty",
            "pct_65_plus_poverty",
        ),
    ),
    (
        "Housing",
        (
            "pct_renter_occupied",
            "median_gross_rent",
            "pct_rent_burden_35_plus",
            "pct_severe_overcrowding",
            "median_home_value",
        ),
    ),
    ("Health and access", ("pct_uninsured", "pct_public_coverage", "pct_broadband")),
    (
        "Programs in use",
        (
            "pct_hh_snap",
            "pct_hh_ssi",
            "pct_hh_public_assistance",
            "irs_eitc_returns",
            "irs_eitc_dollars",
            "aca_consumers",
            "aca_aptc_consumers",
            "hud_vouchers",
        ),
    ),
    (
        "Modeled needs",
        (
            "places_food_insecurity",
            "places_housing_insecurity",
            "places_lack_transport",
            "places_utility_shutoff_threat",
        ),
    ),
    ("Help sites", ("sites_health_center", "sites_public_housing", "sites_lihtc")),
)
# Profile figures to show first, before the full sections.
PROFILE_HEADLINE = ("pop_total", "median_household_income", "pct_people_poverty", "pct_65_plus")
PROFILE_INDICATORS = tuple(
    dict.fromkeys(PROFILE_HEADLINE + tuple(i for _, ids in PROFILE_SECTIONS for i in ids))
)
# Facts a question asks for by name: trigger words, then indicators the answer must cite for its district.
REQUIRED_FACTS = (
    (("profile",), PROFILE_INDICATORS),
    (("income", "picture"), ("median_household_income", "pct_people_poverty")),
)
# Indicators that measure a program at a place, such as its claims or receipt.
MEASURES = {
    "aca_ptc_2026": ("aca_consumers", "aca_aptc_consumers"),
    "eitc_federal_ty2025": ("irs_eitc_returns", "irs_eitc_dollars"),
    "eitc_federal_ty2026": ("irs_eitc_returns", "irs_eitc_dollars"),
    "hud_il_fy2026_hi": ("hud_vouchers",),
    "snap_fy2027": ("pct_hh_snap", "places_received_snap"),
    "ssi_federal_2026": ("pct_hh_ssi",),
    "ssi_hi_supplement": ("pct_hh_ssi",),
}
# What each program rule covers, as list_programs reports and matches it.
COVERS = {
    "aca_ptc_2026": "help paying marketplace health plan premiums",
    "eitc_federal_ty2025": "federal tax credit for low and moderate income working filers",
    "eitc_federal_ty2026": "federal tax credit for low and moderate income working filers",
    "fpg_2026_hi": "poverty guideline thresholds that many programs use for eligibility",
    "h_heap_fy2026": "help with home energy and utility bills",
    "hi_state_credits": "state tax credits for low income filers, food and excise costs and renters",
    "hpha_limits": "income limits for state public housing",
    "hud_il_fy2026_hi": "income limits for housing choice vouchers, public housing and other federal rental help",
    "kupuna_care": "home and community services for kūpuna",
    "medquest_2026": "Medicaid health coverage",
    "preschool_open_doors": "preschool tuition subsidies",
    "snap_fy2027": "food assistance benefits",
    "ssi_federal_2026": "monthly payments for aged, blind or disabled people with low income and few resources",
    "ssi_hi_supplement": "state supplement to SSI payments",
    "wic_2026_27": "nutrition help for pregnant women, infants and young children",
}
# Null-valued fact statuses and why such facts are listed as missing.
NULL_REASONS = {
    "suppressed": "too few cases for the source to publish",
    "not_applicable": "not applicable here",
}
# How each fact tier was produced, as get_facts reports it.
METHODS = {
    "D": "published for this geography",
    "A": "apportioned estimate: moved to the district from ZIP code or tract data, the range shows how much "
    "that move could change it",
    "M": "modeled estimate: the range is a wide uncertainty band, not a formal confidence interval",
    "P": "counted from site locations",
}
# Method text for apportioned sources whose range does not bound every part of the value.
SOURCE_METHODS = {
    "irs_soi_zip_2023": "apportioned estimate: moved to the district from IRS ZIP code data; the range covers "
    "returns with a mapped ZIP code, but returns from small or unlisted ZIP codes are spread by population and "
    "are not bounded by it",
    "cms_oep_2026": "apportioned estimate: moved to the district from ZIP code data; enrollees the source did "
    "not publish by ZIP code are placed by assumption, so the range is not a guaranteed bound",
}
# County and island names (plain form) mapped to county ids.
COUNTY_NAMES = {
    "honolulu county": "15003", "oahu": "15003",
    "hawaii county": "15001", "big island": "15001", "hawaii island": "15001", "island of hawaii": "15001",
    "the big island": "15001", "big island of hawaii": "15001", "the big island of hawaii": "15001",
    "the island of hawaii": "15001",
    "maui": "15009", "maui county": "15009", "molokai": "15009", "lanai": "15009",
    "kauai": "15007", "kauai county": "15007", "niihau": "15007",
    "kalawao": "15005", "kalawao county": "15005", "kalaupapa": "15005",
}  # fmt: skip
# Common English words that must never fuzzy-match a place name.
STOP_WORDS = frozenset(
    [
        "about",
        "after",
        "again",
        "assistance",
        "benefit",
        "benefits",
        "before",
        "cheap",
        "child",
        "children",
        "costs",
        "could",
        "eligible",
        "family",
        "first",
        "health",
        "house",
        "housing",
        "income",
        "learn",
        "money",
        "other",
        "people",
        "place",
        "price",
        "prices",
        "rental",
        "school",
        "senior",
        "should",
        "where",
        "which",
        "would",
        "young",
    ]
)
