# Generates data/places/hawaiian_names.yaml from USGS GNIS names, matched to Census place names by plain name,
# keeping the entries checked by hand.
import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import yaml

from klear.etl.fetch import fetch
from klear.etl.places_gazetteer import (
    OVERRIDES_PATH,
    display_name,
    fetch_names,
    plain_name,
    read_census_names,
)

GNIS_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/DomesticNames_HI_Text.zip"
)
GNIS_MEMBER = "Text/DomesticNames_HI.txt"
GNIS_CLASSES = {"Populated Place", "Civil"}
HEADER = (
    "# Census place id to Hawaiian name (klear/etl/gazetteer_names.py).\n"
    "# A bare name comes from USGS GNIS and is rewritten on each run. An entry with a name, source and url\n"
    "# was checked by hand against that source and is kept on each run.\n"
)


def read_gnis(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        df = pd.read_csv(io.BytesIO(z.read(GNIS_MEMBER)), sep="|", dtype=str, keep_default_na=False)
    return sorted({display_name(n) for n in df.loc[df["feature_class"].isin(GNIS_CLASSES), "feature_name"]})


def build_overrides(census: dict[str, str], gnis_names: list[str]) -> dict[str, str]:
    # a Census name gets a GNIS spelling only when exactly one non-ASCII spelling matches by plain name
    by_plain: dict[str, set[str]] = {}
    for n in gnis_names:
        if not n.isascii():
            by_plain.setdefault(plain_name(n), set()).add(n)
    out = {}
    for pid, name in sorted(census.items()):
        found = by_plain.get(plain_name(name), set())
        if len(found) == 1:
            out[pid] = next(iter(found))
    return out


def read_checked(path: Path) -> dict[str, dict]:
    """The entries of an overrides file checked by hand, each with its name, source and url."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def dump_overrides(overrides: dict[str, str | dict]) -> str:
    body = yaml.safe_dump(overrides, allow_unicode=True, sort_keys=True, default_flow_style=False, width=1000)
    return HEADER + body


def main(client: httpx.Client | None = None) -> None:
    census = read_census_names(fetch_names(client))
    gnis = read_gnis(fetch(GNIS_URL, "blocks/DomesticNames_HI_Text.zip", client=client))
    checked = read_checked(OVERRIDES_PATH)
    overrides = build_overrides(census, gnis) | checked
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(dump_overrides(overrides), encoding="utf-8")
    print(
        f"{len(overrides)} of {len(census)} places have a Hawaiian spelling, {len(checked)} checked by hand"
    )


if __name__ == "__main__":
    main()
