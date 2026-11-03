# Builds tract and ZCTA geography rows from the blocks table.
import pandas as pd

from klear.store.db import GEO_COLS

# Kalawao County, which several sources do not publish
KALAWAO = "15005"
NAMES = {"tract": "Census tract {}", "zcta": "ZIP code area {}"}


def _parent_county(blocks: pd.DataFrame, unit: str) -> pd.Series:
    pop = blocks.groupby([unit, "county"], as_index=False)["pop2020"].sum()
    # largest population first, county id breaks ties
    pop = pop.sort_values([unit, "pop2020", "county"], ascending=[True, False, True])
    return pop.drop_duplicates(unit).set_index(unit)["county"]


def small_geographies(blocks: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for unit, template in NAMES.items():
        valid = blocks.dropna(subset=[unit])
        county = _parent_county(valid, unit)
        ids = sorted(county.index)
        parts.append(
            pd.DataFrame(
                {
                    "geo_type": unit,
                    "geo_id": ids,
                    "name": [template.format(i) for i in ids],
                    "parent_county": [county[i] for i in ids],
                }
            )
        )
    return pd.concat(parts, ignore_index=True)[GEO_COLS]
