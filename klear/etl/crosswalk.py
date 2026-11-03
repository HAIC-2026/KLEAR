# Population-weighted crosswalks from tract, ZCTA or county to legislative districts.
import pandas as pd

SRC = {"tract": "tract", "zcta": "zcta", "county": "county"}
DST = ("house", "senate")
LIST_MAX = 20


class NotACountError(ValueError):
    pass


def _listed(ids: list[str]) -> str:
    ids = sorted(ids)
    shown = ", ".join(ids[:LIST_MAX])
    return shown + (f", ... ({len(ids)} total)" if len(ids) > LIST_MAX else "")


def build_crosswalk(blocks: pd.DataFrame, src: str, dst: str) -> pd.DataFrame:
    if src not in SRC or dst not in DST:
        raise ValueError(f"unsupported crosswalk {src} -> {dst}")
    no_src = blocks[blocks[src].isna()]
    no_dst = blocks[blocks[src].notna() & blocks[dst].isna()]
    df = blocks.dropna(subset=[src, dst])
    pop = df.groupby([src, dst], as_index=False)["pop2020"].sum()
    total = pop.groupby(src)["pop2020"].transform("sum")
    zero = sorted(pop.loc[total == 0, src].unique())
    pop = pop[total > 0]
    pop = pop.assign(pop_share=pop["pop2020"] / total[total > 0])
    out = pop.rename(columns={src: "src_id", dst: "dst_id"})[["src_id", "dst_id", "pop_share"]]
    out = out.reset_index(drop=True)
    out.attrs["zero_pop"] = zero
    out.attrs["dropped_null_src"] = (len(no_src), int(no_src["pop2020"].sum()))
    out.attrs["dropped_null_dst"] = (len(no_dst), int(no_dst["pop2020"].sum()))
    notes = []
    for label, d in (("null " + src, no_src), ("null " + dst, no_dst)):
        if len(d):
            notes.append(
                f"{len(d):,} blocks with {label} dropped from weights, population {int(d['pop2020'].sum()):,}"
            )
    if zero:
        notes.append(f"{len(zero):,} {src} units with population 0 have no shares: {_listed(zero)}")
    out.attrs["notes"] = notes
    return out


def apportion(values: pd.DataFrame, xwalk: pd.DataFrame, units: dict[str, str]) -> pd.DataFrame:
    bad = sorted(i for i in values["indicator_id"].unique() if units.get(i) != "count")
    if bad:
        raise NotACountError(f"only counts can be apportioned: {', '.join(bad)}")
    mapped = set(xwalk["src_id"])
    zero = set(xwalk.attrs.get("zero_pop", []))
    notes = []
    for label, ids in (
        ("zero-population", zero),
        ("no blocks", set(values["src_id"]) - mapped - zero),
    ):
        hit = values[values["src_id"].isin(ids - mapped) & (values["value"].fillna(0) != 0)]
        if len(hit):
            notes.append(
                f"{label} source units dropped with value {hit['value'].sum():g}: {_listed(list(hit['src_id'].unique()))}"
            )
    joined = values.merge(xwalk, on="src_id", how="inner")
    joined["value"] = joined["value"] * joined["pop_share"]
    out = joined.groupby(["dst_id", "indicator_id"], as_index=False)["value"].sum()
    out.attrs["notes"] = notes
    return out
