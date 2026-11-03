# Builds the hand-made CHAS sample zip; every dictionary phrase is provisional.
import zipfile
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "chas_sample" / "2018thru2022-140-csv.zip"
# provisional: confirm against the real 2018-2022 dictionary
BANDS = [
    "household income is less than or equal to 30% of HAMFI",
    "household income is greater than 30% but less than or equal to 50% of HAMFI",
    "household income is greater than 50% but less than or equal to 80% of HAMFI",
]
MEASURES = ["cost burden greater than 30%", "all households"]
# provisional: confirm against the real 2018-2022 dictionary
TRACTS = ["14000US15003008001", "14000US15001021200", "14000US15009030307"]


def build() -> None:
    dic = [("Table 8", "T8_est1", "Total", "Total"), ("Table 8", "T8_est2", "Subtotal", "Owner occupied")]
    n = 3
    for tenure in ("Owner occupied", "Renter occupied"):
        for band in BANDS:
            for measure in MEASURES:
                dic.append(("Table 8", f"T8_est{n}", "Detail", f"{tenure}, {band}, {measure}"))
                n += 1
    dictionary = pd.DataFrame(dic, columns=["Table", "Column Name", "Line Type", "Description"])
    cols = [c for c in dictionary["Column Name"]]
    rows = []
    for i, geoid in enumerate(TRACTS):
        est = {c: 100 + 10 * i + j for j, c in enumerate(cols)}
        moe = {c.replace("_est", "_moe"): 20 + j for j, c in enumerate(cols)}
        rows.append({"geoid": geoid, "name": f"Census Tract {i}", **est, **moe})
    table8 = pd.DataFrame(rows)
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("CHAS data dictionary 18-22.csv", dictionary.to_csv(index=False))
        z.writestr("140/Table8.csv", table8.to_csv(index=False))


if __name__ == "__main__":
    build()
