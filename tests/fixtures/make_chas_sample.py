# Builds the hand-made CHAS sample zip in the 2018-2022 layout, which has no data dictionary.
import zipfile
from pathlib import Path

import pandas as pd

from klear.etl.chas import ESTIMATES

OUT = Path(__file__).parent / "chas_sample" / "2018thru2022-140-csv.zip"
TRACTS = ["14000US15003008001", "14000US15001021200", "14000US15009030307"]


def table8(tracts: list[str], base: int = 100, moe: int = 20) -> pd.DataFrame:
    cols = [f"T8_est{n}" for n in range(1, ESTIMATES + 1)]
    rows = []
    for i, geoid in enumerate(tracts):
        est = {c: base + 10 * i + j for j, c in enumerate(cols)}
        err = {c.replace("_est", "_moe"): moe + j for j, c in enumerate(cols)}
        rows.append({"geoid": geoid, "name": f"Census Tract {i}", **est, **err})
    return pd.DataFrame(rows)


def build() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("140/Table8.csv", table8(TRACTS).to_csv(index=False))


if __name__ == "__main__":
    build()
