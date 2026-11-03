# Tests for the DBEDT-versus-Census-API source fidelity check.
import httpx
import pandas as pd
import pytest

from klear.etl.census_api import get_acs, get_variable_labels
from klear.etl.dbedt import COLUMNS, fetch_dbedt, parse_dbedt_profile
from klear.etl.profile import align_dbedt_to_profile, profile_bases
from klear.validation import CheckResult
from klear.validation.source_fidelity import KINDS, check_source_fidelity

ROWS_PER_GEO = 542


def dbedt(**over):
    row = {
        "geo_type": "house", "geo_id": "001", "sheet": "DP03", "row_no": 4, "label": "In labor force",
        "section": "EMPLOYMENT STATUS", "estimate": 12471.0, "estimate_status": "ok", "moe": 1063.0,
        "moe_status": "ok", "percent": 59.3, "percent_moe": 2.8, "percent_status": "ok",
        "percent_moe_status": "ok", "acs_var": "DP03_0002",
    } | over  # fmt: skip
    return pd.DataFrame([row], columns=COLUMNS + ["acs_var"])


def _status(v):
    return "ok" if v is not None else "not_applicable"


def api(e=12471.0, m=1063.0, pe=59.3, pm=2.8, var="DP03_0002", **status):
    def r(v, est, moe, es, ms):
        return {
            "geo_type": "house", "geo_id": "001", "variable": v, "estimate": est,
            "estimate_status": es, "moe": moe, "moe_status": ms,
        }  # fmt: skip

    return pd.DataFrame(
        [
            r(var, e, m, status.get("e_status", _status(e)), status.get("m_status", _status(m))),
            r(var + "P", pe, pm, status.get("pe_status", _status(pe)), status.get("pm_status", _status(pm))),
        ]
    )


def null_dbedt(**over):
    base = {
        "estimate": None, "estimate_status": "not_applicable", "moe": None, "moe_status": "not_applicable",
        "percent": None, "percent_moe": None, "percent_status": "not_applicable",
        "percent_moe_status": "not_applicable",
    }  # fmt: skip
    return dbedt(**(base | over))


def test_fidelity_all_match():
    res = check_source_fidelity(dbedt(), api())
    assert isinstance(res, CheckResult)
    assert (res.name, res.passed, res.gate, res.n_compared, res.n_mismatch) == (
        "source_fidelity", True, True, 4, 0,
    )  # fmt: skip


def test_fidelity_counts_mismatches():
    res = check_source_fidelity(dbedt(), api(m=1064.0))
    assert not res.passed
    assert (res.n_compared, res.n_mismatch) == (4, 1)
    d = res.details[0]
    assert (d["field"], d["dbedt"], d["api"], d["acs_var"], d["row_no"], d["geo_id"]) == (
        "moe", 1063.0, 1064.0, "DP03_0002", 4, "001",
    )  # fmt: skip


@pytest.mark.parametrize(
    "frame_api,field",
    [
        (api(e=12472.0), "estimate"),
        (api(pe=59.4), "percent"),
        (api(pm=2.9), "percent_moe"),
        (api(pe=None, pm=None), "percent"),
    ],
)
def test_fidelity_flags_each_field(frame_api, field):
    res = check_source_fidelity(dbedt(), frame_api)
    assert res.n_mismatch >= 1
    assert field in {d["field"] for d in res.details}


def test_fidelity_treats_both_null_as_match():
    d = null_dbedt(estimate_status="suppressed", moe_status="not_computable")
    a = api(e=None, m=None, pe=None, pm=None, e_status="suppressed", m_status="not_computable")
    res = check_source_fidelity(d, a)
    assert (res.passed, res.n_mismatch) == (True, 0)


def test_fidelity_null_versus_number_is_mismatch():
    d = dbedt(estimate=None, estimate_status="suppressed")
    res = check_source_fidelity(d, api())
    assert [x["field"] for x in res.details] == ["estimate"]


def test_fidelity_status_kind_mismatch_counts():
    d = null_dbedt(moe_status="controlled")
    res = check_source_fidelity(d, api(e=None, m=None, pe=None, pm=None))
    assert not res.passed
    assert [(x["field"], x["dbedt_status"], x["api_status"]) for x in res.details] == [
        ("moe", "controlled", "not_applicable")
    ]


ENTRY_CASES = []
for _field, _pairs in KINDS.items():
    for _d, _a in sorted(_pairs):
        ENTRY_CASES.append((_field, _d, _a))


@pytest.mark.parametrize("field,d_status,a_status", ENTRY_CASES)
def test_fidelity_each_kind_entry_passes(field, d_status, a_status):
    # build a row where only the field under test carries the pair; values follow the pair
    d_over, a_over = {}, {}
    numeric_d = d_status in ("ok", "top_coded", "bottom_coded", "universe_repeat")
    if field == "estimate":
        offset = {"top_coded": 1.0, "bottom_coded": -1.0}.get(d_status, 0.0)
        d_over = {"estimate": 100.0 if numeric_d else None, "estimate_status": d_status}
        a_over = {"e": 100.0 + offset if numeric_d else None, "e_status": a_status}
        if d_status in ("top_coded", "bottom_coded"):
            d_over |= {"moe": None, "moe_status": "open_ended"}
            a_over |= {"m": None, "m_status": "open_ended"}
    elif field == "moe":
        d_over = {"moe": 5.0 if d_status == "ok" else None, "moe_status": d_status}
        a_over = {"m": 5.0 if d_status == "ok" else None, "m_status": a_status}
    elif field == "percent":
        value = {"ok": 59.3, "universe_repeat": None}.get(d_status)
        d_over = {"percent": value, "percent_status": d_status}
        a_over = {"pe": 12471.0 if d_status == "universe_repeat" else value, "pe_status": a_status}
    else:
        d_over = {"percent_moe": 2.8 if d_status == "ok" else None, "percent_moe_status": d_status}
        a_over = {"pm": 2.8 if d_status == "ok" else None, "pm_status": a_status}
    res = check_source_fidelity(dbedt(**d_over), api(**a_over))
    assert res.passed, res.details


def test_fidelity_missing_api_row_is_mismatch():
    res = check_source_fidelity(dbedt(), api(var="DP03_0003"))
    assert not res.passed
    assert "missing" in {d["field"] for d in res.details}


def test_fidelity_universe_row_percent():
    # DBEDT nulls a universe row's percent; the API repeats the count as PE with a null PM.
    d = dbedt(
        percent=None, percent_moe=None, percent_status="universe_repeat", percent_moe_status="not_applicable"
    )
    assert check_source_fidelity(d, api(pe=12471.0, pm=None)).passed
    assert not check_source_fidelity(d, api(pe=40.0, pm=None)).passed


def test_fidelity_top_coded_equivalence():
    # DBEDT shows "4,000+" with MOE "***"; the API returns 4001 with MOE -333333333.
    d = null_dbedt(
        sheet="DP04", row_no=117, label="Median (dollars)", acs_var="DP04_0101", estimate=4000.0,
        estimate_status="top_coded", moe_status="open_ended",
    )  # fmt: skip
    a = api(var="DP04_0101", e=4001.0, m=None, pe=None, pm=None, m_status="open_ended")
    res = check_source_fidelity(d, a)
    assert (res.passed, res.n_mismatch) == (True, 0)
    assert any("open-ended" in n for n in res.notes)
    same_value = api(var="DP04_0101", e=4000.0, m=None, pe=None, pm=None, m_status="open_ended")
    assert not check_source_fidelity(d, same_value).passed
    assert not check_source_fidelity(d.assign(estimate_status="ok"), a).passed
    small_sample = api(var="DP04_0101", e=4001.0, m=None, pe=None, pm=None, m_status="not_applicable")
    assert not check_source_fidelity(d, small_sample).passed


def test_fidelity_bottom_coded_equivalence():
    d = null_dbedt(
        acs_var="DP04_0134", estimate=2500.0, estimate_status="bottom_coded", moe_status="open_ended"
    )
    a = api(var="DP04_0134", e=2499.0, m=None, pe=None, pm=None, m_status="open_ended")
    assert check_source_fidelity(d, a).passed
    wrong = api(var="DP04_0134", e=2501.0, m=None, pe=None, pm=None, m_status="open_ended")
    assert not check_source_fidelity(d, wrong).passed


def test_fidelity_rejects_unaligned_rows():
    with pytest.raises(ValueError, match="acs_var"):
        check_source_fidelity(dbedt(acs_var=None), api())


@pytest.mark.network
def test_full_fidelity_all_76_districts():
    labels = get_variable_labels("acs/acs5/profile")
    bases = profile_bases(labels)
    frames, apis = [], []
    with httpx.Client(timeout=120) as client:
        for g in ("state", "county", "senate", "house"):
            frames.append(align_dbedt_to_profile(parse_dbedt_profile(fetch_dbedt(g), g), labels))
            apis.append(get_acs("acs/acs5/profile", bases, g, client=client))
    aligned = pd.concat(frames, ignore_index=True)
    per_geo = aligned.groupby(["geo_type", "geo_id"]).size()
    assert len(per_geo) == 82
    assert per_geo.groupby(level="geo_type").size().to_dict() == {
        "state": 1, "county": 5, "senate": 25, "house": 51,
    }  # fmt: skip
    assert set(per_geo) == {ROWS_PER_GEO}
    assert len(aligned) == 44444
    res = check_source_fidelity(aligned, pd.concat(apis, ignore_index=True))
    print(res.n_compared, res.n_mismatch, res.notes)
    assert res.n_compared == 4 * 44444
    assert res.n_mismatch == 0, res.details[:20]
    assert res.passed
