# Tests that each program income cut point KLEAR uses matches the cut point its official rule publishes.
import pytest

from klear.etl import chas
from klear.rules.cuts import CUTS, HUD_LEVELS, HUD_RULE, POVERTY_RULE, at_percent, limits, value
from klear.rules.registry import load_rules

RULES = {r.rule_id: r for r in load_rules()}
POVERTY = {int(k): v for k, v in RULES[POVERTY_RULE].values.items()}
HUD = RULES[HUD_RULE].values


@pytest.mark.parametrize("name", sorted(CUTS))
def test_cut_names_a_confirmed_rule(name):
    cut = CUTS[name]
    assert cut.base in ("poverty", "median")
    assert RULES[cut.rule_id].status == "confirmed"


@pytest.mark.parametrize("name", [n for n, c in CUTS.items() if c.percent_key])
def test_cut_is_the_percent_its_rule_publishes(name):
    cut = CUTS[name]
    assert value(RULES[cut.rule_id], cut.percent_key) == cut.percent


@pytest.mark.parametrize("name", [n for n, c in CUTS.items() if c.limits])
def test_dollar_limits_are_the_cut_times_the_poverty_guideline(name):
    cut = CUTS[name]
    table = limits(RULES[cut.rule_id], cut.limits)
    assert len(table) >= 2, f"{cut.rule_id} publishes no {cut.limits} limits"
    for size, dollars in table.items():
        base = POVERTY[8] - POVERTY[7] if size == "each_additional" else POVERTY[size]
        assert dollars == at_percent(cut.percent, base, cut.periods), (name, size)


@pytest.mark.parametrize("name", [n for n, c in CUTS.items() if c.limits])
def test_limit_names_carry_the_cut_percent(name):
    cut = CUTS[name]
    if "pct" in cut.limits:
        assert f"_{cut.percent}pct" in cut.limits


def test_medquest_disregard_is_the_gap_between_its_two_cuts():
    rule = RULES["medquest_2026"]
    assert CUTS["medquest_adult"].percent - CUTS["medquest_adult_standard"].percent == value(
        rule, "disregard_pct"
    )


def test_snap_gross_limit_is_above_the_net_limit_for_every_size():
    rule = RULES["snap_fy2027"]
    gross, net = limits(rule, CUTS["snap_gross"].limits), limits(rule, CUTS["snap_net"].limits)
    assert gross.keys() == net.keys() and all(gross[k] > net[k] for k in gross)


@pytest.mark.parametrize("county", sorted(HUD))
def test_hud_levels_rise_with_the_cut_and_household_size(county):
    # extremely low (30%) is at least the poverty guideline, capped at very low (50%), below low (80%)
    v = HUD[county]
    for size in range(1, 9):
        eli, l50, l80 = (v[f"{HUD_LEVELS[p]}_{size}"] for p in (30, 50, 80))
        assert eli == min(l50, max(eli, POVERTY[size]))
        assert eli <= l50 < l80
        if size > 1:
            assert v[f"l80_{size}"] > v[f"l80_{size - 1}"]


def test_hpha_uses_the_hud_very_low_income_level():
    assert CUTS["hud_very_low"].percent in HUD_LEVELS and HUD_LEVELS[CUTS["hud_very_low"].percent] == "l50"
    assert "very low (50%)" in RULES[HUD_RULE].note


def test_chas_bands_use_the_hud_low_income_cut():
    cut = CUTS["hud_low"]
    assert HUD_LEVELS[cut.percent] == "l80" and "low (80%)" in RULES[HUD_RULE].note
    labels = [*chas.CHAS_INDICATORS.values(), chas.LOW_INCOME]
    assert all(f"at or below {cut.percent}% HAMFI" in label for label in labels)
