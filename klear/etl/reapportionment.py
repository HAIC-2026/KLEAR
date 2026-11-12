# Reads the Reapportionment Commission's final 2022 district files and their population fields.
import httpx
import pandas as pd
import pyogrio

from klear.etl.fetch import fetch

BASE = "https://elections.hawaii.gov/wp-content/uploads"
PAGE = "https://elections.hawaii.gov/about-us/boards-and-commissions/reapportionment/"
UNITS = ("Hawaii", "Maui", "Kauai", "Oahu")
CHAMBER_FILE = {"house": "House", "senate": "Senate"}
# TOTAL is the 2020 census count; PopHaw2022 the permanent-resident base v1231 adopted January 6, 2022
FIELDS = ["DISTRICT", "TOTAL", "PopHaw2022"]
COLUMNS = ["chamber", "geo_id", "unit", "census_total", "permanent"]


def file_name(unit: str, chamber: str) -> str:
    return f"{unit}_{CHAMBER_FILE[chamber]}_Final_2022.zip"


def read_unit(path, unit: str, chamber: str) -> pd.DataFrame:
    df = pyogrio.read_dataframe(f"zip://{path}", columns=FIELDS, read_geometry=False)
    return pd.DataFrame(
        {
            "chamber": chamber,
            "geo_id": [f"{int(d):03d}" for d in df["DISTRICT"]],
            "unit": unit,
            "census_total": df["TOTAL"].astype("int64").to_numpy(),
            "permanent": df["PopHaw2022"].astype("int64").to_numpy(),
        },
        columns=COLUMNS,
    )


def load_commission(client: httpx.Client | None = None) -> pd.DataFrame:
    # one shapefile per basic island unit and chamber, as the Commission publishes them
    parts = []
    for chamber in CHAMBER_FILE:
        for unit in UNITS:
            name = file_name(unit, chamber)
            path = fetch(f"{BASE}/{name}", f"reapportionment/{name}", client=client)
            parts.append(read_unit(path, unit, chamber))
    return pd.concat(parts, ignore_index=True)
