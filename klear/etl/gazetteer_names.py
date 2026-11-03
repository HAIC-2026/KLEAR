# Generates data/places/hawaiian_names.yaml from USGS GNIS names, matched to Census place names by plain name.
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
HEADER = "# Census place id to Hawaiian name, from USGS GNIS (klear/etl/gazetteer_names.py).\n"


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


def main(client: httpx.Client | None = None) -> None:
    census = read_census_names(fetch_names(client))
    gnis = read_gnis(fetch(GNIS_URL, "blocks/DomesticNames_HI_Text.zip", client=client))
    overrides = build_overrides(census, gnis)
    body = yaml.safe_dump(overrides, allow_unicode=True, sort_keys=True, default_flow_style=False)
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(HEADER + body, encoding="utf-8")
    print(f"{len(overrides)} of {len(census)} places have a Hawaiian spelling")


if __name__ == "__main__":
    main()
