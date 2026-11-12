# Program income cut points KLEAR uses, each tied to the rule value that publishes it.
import math
import re
from dataclasses import dataclass

from klear.rules.registry import Rule

POVERTY_RULE = "fpg_2026_hi"
HUD_RULE = "hud_il_fy2026_hi"


@dataclass(frozen=True)
class Cut:
    program: str
    percent: int  # percent of the base
    base: str  # poverty: the 2026 Hawaiʻi poverty guideline; median: HUD area median family income
    rule_id: str
    percent_key: str | None = None  # rule value that states the percent
    limits: str | None = None  # prefix of the rule's dollar limits by household size
    periods: int = 1  # 12 when the dollar limits are monthly


CUTS = {
    "snap_gross": Cut("SNAP gross income test", 130, "poverty", "snap_fy2027", limits="gross_130pct_monthly",
                      periods=12),
    "snap_net": Cut("SNAP net income test", 100, "poverty", "snap_fy2027", limits="net_100pct_monthly",
                    periods=12),
    "medquest_adult": Cut("Med-QUEST adults 19 to 64, with the 5% disregard", 138, "poverty", "medquest_2026",
                          percent_key="adult_limit_pct_fpl_with_disregard", limits="adult_monthly_limit",
                          periods=12),
    "medquest_adult_standard": Cut("Med-QUEST adult income standard", 133, "poverty", "medquest_2026",
                                   percent_key="adult_limit_pct_fpl"),
    "wic": Cut("WIC", 185, "poverty", "wic_2026_27", percent_key="pct_of_fpg", limits="annual_185pct"),
    "aca_ptc_cap": Cut("ACA premium tax credit upper income limit", 400, "poverty", "aca_ptc_2026",
                       percent_key="income_cap_pct_of_poverty"),
    "hud_very_low": Cut("HUD very low income, used by HPHA public housing", 50, "median", "hpha_limits",
                        percent_key="low_income_pct_of_median"),
    "hud_low": Cut("HUD low income, the CHAS line at or below 80% HAMFI", 80, "median", HUD_RULE),
}  # fmt: skip
# HUD income limit columns: extremely low (30%), very low (50%) and low (80%) income, by household size
HUD_LEVELS = {30: "eli", 50: "l50", 80: "l80"}


def value(rule: Rule, key: str):
    e = rule.values[key]
    return e["value"] if isinstance(e, dict) else e


def limits(rule: Rule, prefix: str) -> dict:
    # dollar limits keyed by household size, plus "each_additional" when the rule publishes it
    pattern = re.compile(rf"^{re.escape(prefix)}_(?:size)?(\d+|each_additional)$")
    out = {}
    for key in rule.values:
        if m := pattern.match(str(key)):
            size = m[1]
            out[size if size == "each_additional" else int(size)] = value(rule, key)
    return out


def at_percent(percent: int, amount: int, periods: int = 1) -> int:
    # percent of a yearly amount per period, rounded up to the next dollar as SNAP, WIC and Med-QUEST publish
    return math.ceil(percent * amount / (100 * periods))
