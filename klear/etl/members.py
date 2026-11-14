# Reads the Legislature's member listings by district from the Legislative Reference Bureau's Public Access Room.
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from klear.etl.fetch import fetch
from klear.store.db import MEMBER_COLS

PAR = "https://lrb.hawaii.gov/par"
PAGE_URL = f"{PAR}/current-legislature/"
PUBLISHER = "Hawaiʻi Legislative Reference Bureau, Public Access Room"
LICENSE = "Public record of the State of Hawaiʻi"
# chamber: source_id, listing title, listing PDF, profiles PDF, expected seats
CHAMBERS = {
    "house": (
        "lrb_par_house",
        "Representatives at a Glance",
        f"{PAR}/wp-content/uploads/sites/2/2026/01/2026-House-Info-Glance-A.pdf",
        f"{PAR}/wp-content/uploads/sites/2/2026/10/2026-House-Pictures-Profiles-Interim-A.pdf",
        51,
    ),
    "senate": (
        "lrb_par_senate",
        "Senators at a Glance",
        f"{PAR}/wp-content/uploads/sites/2/2025/12/2026-Senators-Info-Glance-A.pdf",
        f"{PAR}/wp-content/uploads/sites/2/2026/10/2026-Senate-Pictures-Profiles-Interim-A.pdf",
        25,
    ),
}
HST = timedelta(hours=-10)
# a listing row: district, party letter (not kept), then the name and the office columns
ROW = re.compile(r"^(\d{1,2})\s+[A-Z]\s+(\S.*)$")
# where the name ends: a committee acronym, a room or phone number, or a leadership title in parentheses
NAME_END = re.compile(r"^(?:[A-Z]{3},?|\d{3}|\d{3}-\d{4}|\()")
FOOTER_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
TITLE = re.compile(r"(Thirty-\w+ Legislature),\s*(\d{4})\D(\d{4})")
MEMBER_PAGE = "memberpage.aspx"


def _text(path: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


def clean_name(text: str) -> str:
    """The name as listed, with the PDF's stray spaces before punctuation and doubled spaces removed."""
    return re.sub(r"\s+([,.])", r"\1", " ".join(text.split()))


def parse_listing(path: Path) -> pd.DataFrame:
    """District and name of each member in an at-a-Glance listing. The party column is read past, never kept."""
    rows = []
    for line in _text(path).splitlines():
        m = ROW.match(line.strip())
        if m is None or "," not in m.group(2):
            continue
        surname, _, rest = m.group(2).partition(",")
        given = []
        for word in rest.split():
            if NAME_END.match(word):
                break
            given.append(word)
        rows.append({"district": int(m.group(1)), "name": clean_name(f"{surname}, {' '.join(given)}")})
    return pd.DataFrame(rows, columns=["district", "name"])


def listing_date(path: Path) -> date:
    """The date printed at the foot of the listing, or the PDF's modified date when none is printed."""
    for line in reversed(_text(path).splitlines()):
        if m := FOOTER_DATE.search(line.strip()):
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    stamp = str(PdfReader(path).metadata.get("/ModDate", ""))
    if m := re.match(r"D:(\d{4})(\d{2})(\d{2})", stamp):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    raise ValueError(f"{path.name} prints no date")


def legislature(path: Path) -> str:
    """The Legislature and years the listing covers, such as Thirty-Third Legislature, 2025-2026."""
    m = TITLE.search(_text(path))
    if m is None:
        raise ValueError(f"{path.name} does not name its Legislature")
    return f"{m.group(1)}, {m.group(2)}-{m.group(3)}"


def surname_key(name: str) -> str:
    """Letters of the surname before the comma, lower case, for matching the two listings."""
    return re.sub(r"[^a-z]", "", name.split(",")[0].casefold())


def profile_links(path: Path) -> dict[str, str]:
    """Each member page link in the profiles PDF, keyed by the surname printed under it. A surname printed
    under two different links is left out, so it can never point at the wrong member."""
    found: dict[str, set[str]] = {}
    for page in PdfReader(path).pages:
        frags: list[tuple[float, float, str]] = []

        def visit(text, cm, tm, _font, _size, frags=frags):
            if text.strip():
                frags.append((tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5], text))

        page.extract_text(visitor_text=visit)
        rects: dict[str, list[tuple[float, ...]]] = {}
        for annot in page.get("/Annots") or []:
            a = annot.get_object()
            uri = str((a.get("/A") or {}).get("/URI", ""))
            box = tuple(float(z) for z in a.get("/Rect", (0, 0, 0, 0)))
            if MEMBER_PAGE in uri.casefold() and box[2] - box[0] > 2:
                rects.setdefault(uri, []).append(box)
        for uri, boxes in rects.items():
            text = " ".join(
                t
                for x, y, t in frags
                if any(x0 - 2 <= x <= x1 and y0 - 2 <= y <= y1 + 2 for x0, y0, x1, y1 in boxes)
            )
            if "," in text:
                found.setdefault(surname_key(text), set()).add(uri)
    return {k: next(iter(v)) for k, v in found.items() if len(v) == 1 and k}


def _retrieved(path: Path) -> date:
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text(encoding="utf-8"))
    return (datetime.fromisoformat(meta["retrieved_at"]).astimezone(UTC) + HST).date()


def members_frame(chamber: str, listing: Path, profiles: Path, retrieved: date) -> pd.DataFrame:
    """Store rows for one chamber: each listed member with the official page linked from the profiles PDF, or
    that PDF itself when it links no page for the member."""
    source_id, _, _, profiles_url, _ = CHAMBERS[chamber]
    df = parse_listing(listing)
    links = profile_links(profiles)
    return pd.DataFrame(
        {
            "geo_type": chamber,
            "geo_id": [f"{d:03d}" for d in df["district"]],
            "name": df["name"],
            "profile_url": [links.get(surname_key(n), profiles_url) for n in df["name"]],
            "listed_on": listing_date(listing).isoformat(),
            "retrieved_on": retrieved.isoformat(),
            "source_id": source_id,
        }
    )[MEMBER_COLS]


def load_members(client=None) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Members of both chambers, and for each chamber its source row: name, url and vintage."""
    frames, sources = [], {}
    for chamber, (source_id, title, url, profiles_url, _) in CHAMBERS.items():
        listing = fetch(url, f"members/{Path(url).name}", client=client)
        profiles = fetch(profiles_url, f"members/{Path(profiles_url).name}", client=client)
        frames.append(members_frame(chamber, listing, profiles, _retrieved(listing)))
        when = listing_date(listing)
        sources[source_id] = {
            "name": f"{title}, {legislature(listing)}",
            "url": url,
            "vintage": f"Listing dated {when:%B} {when.day}, {when.year}",
        }
    return pd.concat(frames, ignore_index=True), sources
