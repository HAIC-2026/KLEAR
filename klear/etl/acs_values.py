# Parses ACS estimate and margin-of-error cells into (value, status) pairs.
import math
import re

# Raw ACS codes map here, and only here, onto the store's value_status and moe_status vocabulary.
SUPPRESSED_SENTINELS = ("-666666666", "-999999999", "-333333333", "-222222222", "-555555555")
ESTIMATE_CODES = {code: "suppressed" for code in SUPPRESSED_SENTINELS} | {
    "-888888888": "not_applicable",
    "(X)": "not_applicable",
    "-": "suppressed",
    "N": "suppressed",
}
MOE_CODES = {
    "*****": "controlled",  # DBEDT: estimate controlled to official totals, no sampling error
    "-555555555": "controlled",
    "***": "open_ended",  # DBEDT: median in an open-ended interval
    "-333333333": "open_ended",
    "**": "not_computable",  # DBEDT: too few sample cases to compute a MOE
    "-222222222": "not_computable",
    "-666666666": "not_computable",
    "-999999999": "not_computable",
    "-": "not_computable",
    "N": "not_computable",
    "(X)": "not_applicable",
    "-888888888": "not_applicable",
}
BLANK_STATUS = {"estimate": "suppressed", "moe": "not_computable"}
NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+\.\d+)"
ESTIMATE_RE = re.compile(NUMBER + r"([+-]?)")
CODED = {"+": "top_coded", "-": "bottom_coded"}
# API encodes an open-ended "X+" or "X-" estimate as X+1 or X-1 with MOE -333333333
OPEN_ENDED_OFFSET = {"top_coded": 1.0, "bottom_coded": -1.0}
MOE_RE = re.compile(r"(?:±|\+/-)?" + NUMBER)
SENTINEL_RE = re.compile(r"(-\d{9})\.0+")


def _text(raw: str | float | None) -> str | None:
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    text = str(raw).strip()
    match = SENTINEL_RE.fullmatch(text)
    return match.group(1) if match else text


def _parse(raw, codes, pattern, blank) -> tuple[float | None, str]:
    text = _text(raw)
    if text is None:
        return (None, blank)
    if text in codes:
        return (None, codes[text])
    match = pattern.fullmatch(text)
    if match is None:
        raise ValueError(f"unparseable ACS value: {raw!r}")
    status = CODED.get(match.group(2), "ok") if match.re is ESTIMATE_RE else "ok"
    return (float(match.group(1).replace(",", "")), status)


def parse_estimate(raw: str | float | None) -> tuple[float | None, str]:
    return _parse(raw, ESTIMATE_CODES, ESTIMATE_RE, BLANK_STATUS["estimate"])


def parse_moe(raw: str | float | None) -> tuple[float | None, str]:
    return _parse(raw, MOE_CODES, MOE_RE, BLANK_STATUS["moe"])
