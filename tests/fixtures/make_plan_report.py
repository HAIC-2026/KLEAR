# Builds a small stand-in for the 2021 Reapportionment Commission Final Report PDF, laid out like the real pages.
from klear.etl.plan_report import DESCRIPTION_PAGES, SEATS, TABLE_PAGES, ordinal

UNIT_TABLES = {"Oahu": (1, 2), "Hawaii": (3, 4), "Maui": (5, 6), "Kauai": (7, 8)}
KIND = {"house": "Representative", "senate": "Senatorial"}
WORD = {"house": "House", "senate": "Senate"}
METES = (
    "Beginning at the most northerly point of the district, thence southeasterly 1.45 miles along Koolau Rdg, "
    "thence southerly 0.02 miles along Unnamed Line to the aforementioned point of beginning."
)


def _wrap(text: str, width: int = 90) -> list[str]:
    lines, line = [], ""
    for word in text.split(" "):
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line]


def description(chamber: str, n: int) -> str:
    place = "Kailua-Kona" if n == 6 else "Kaneohe"
    return (
        f"The {ordinal(n)} {KIND[chamber]} District comprises the portion of the island of Oahu including "
        f"Waianae, a portion of {place}, and Hawaii Kai"
    )


def description_pages(rows: list[dict]) -> dict[int, str]:
    """Appendix D: two districts to a page under the running head, each opening sentence then its metes and
    bounds, with the D-page footer. A hyphenated place name breaks across two lines, as in the real report."""
    pages, body = {}, []
    order = sorted(rows, key=lambda d: (d["chamber"] != "house", int(d["geo_id"])))
    for i, d in enumerate(order):
        if i == 0 or d["chamber"] != order[i - 1]["chamber"]:
            body.append(f"{KIND[d['chamber']].upper()} DISTRICTS")
        n = int(d["geo_id"])
        body.append(f"{ordinal(n)} {KIND[d['chamber']]} District ")
        text = f"{description(d['chamber'], n)}, more particularly described as follows: {METES}"
        body += [x.replace("Kailua-Kona", "Kailua-\nKona") for x in _wrap(text)]
        if i % 2 == 1 or i == len(order) - 1:
            page = DESCRIPTION_PAGES.start + len(pages)
            head = ["2021 Reapportionment Plans ", "Metes and Bounds ", f" Page {len(pages) + 1} of 54 "]
            pages[page] = "\n".join([*head, *body, f"D-{len(pages) + 1:03d}"])
            body = []
    last = DESCRIPTION_PAGES.start + len(pages)
    pages[last] = "\n".join(
        ["CONGRESSIONAL DISTRICTS", "1st Congressional District", f"D-{len(pages) + 1:03d}"]
    )
    return pages


def table_pages(rows: list[dict]) -> dict[int, str]:
    """Tables B, C and D with each unit's permanent residents and seats, then Tables 1 to 8 by island unit and 9
    and 10 statewide, each district row with its target, population, deviation and percent."""
    units = {}
    for d in rows:
        units.setdefault(d["unit"], {"house": [], "senate": []})[d["chamber"]].append(d)
    base = {u: sum(d["permanent"] for d in c["house"]) for u, c in units.items()}
    target = {(u, ch): round(base[u] / len(units[u][ch])) for u in units for ch in SEATS}
    lines = ["2021 Reapportionment Commission Final Report and Reapportionment Plan ", "14 ", " "]
    for letter, chamber in (("B", "house"), ("C", "senate")):
        lines.append(f"Table {letter} – Average Population Per {WORD[chamber]} Member by Basic Island Unit")
        lines += [f"{u} {base[u]:,} {len(units[u][chamber])} {target[(u, chamber)]:,} " for u in units]
    lines += ["Table D – Apportionment Among Basic Island Units", "State of Hawaii "]
    lines.append(f"{sum(base.values()):,} {SEATS['senate']} {SEATS['house']} 76 17,737 ")
    pages = {TABLE_PAGES.start: "\n".join(lines)}

    def row(d, t):
        dev = d["permanent"] - t
        return f"{int(d['geo_id'])}        {t:,}         {d['permanent']:,}  {dev} {100 * dev / t:.2f}%    "

    lines = ["2021 Reapportionment Commission Final Report and Reapportionment Plan ", "15 ", " "]
    for u, (house_table, senate_table) in UNIT_TABLES.items():
        for number, chamber in ((house_table, "house"), (senate_table, "senate")):
            if u not in units:
                continue
            lines.append(
                f"Table {number} - {u} Basic Island Units Targets and Deviations ({WORD[chamber]} Districts)  "
            )
            listed = sorted(units[u][chamber], key=lambda d: int(d["geo_id"]))
            lines += [row(d, target[(u, chamber)]) for d in listed]
    pages[TABLE_PAGES.start + 1] = "\n".join(lines)
    lines = ["2021 Reapportionment Commission Final Report and Reapportionment Plan ", "18 ", " "]
    for number, chamber in ((9, "house"), (10, "senate")):
        lines.append(f"Table {number} - {WORD[chamber]} Statewide Targets and Deviations    ")
        listed = sorted((d for d in rows if d["chamber"] == chamber), key=lambda d: int(d["geo_id"]))
        lines += [row(d, 26_432) for d in listed]
    pages[TABLE_PAGES.start + 2] = "\n".join(lines)
    return pages


def plan_rows(pops: dict[str, dict[str, int]], unit_of) -> list[dict]:
    """Every House and Senate district with its island unit and permanent residents."""
    return [
        {"chamber": c, "geo_id": g, "unit": unit_of(c, g), "permanent": p}
        for c in SEATS
        for g, p in pops[c].items()
    ]


def _escape(line: str) -> str:
    out = []
    for b in line.encode("cp1252"):
        ch = chr(b)
        out.append(f"\\{b:03o}" if b > 126 or ch in "()\\" else ch)
    return "".join(out)


def pdf(pages: dict[int, str], n_pages: int = DESCRIPTION_PAGES.stop) -> bytes:
    """A PDF of n_pages pages, blank except the given ones, each line of text set on its own line."""
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None, font]
    kids = []
    for n in range(1, n_pages + 1):
        lines = pages.get(n, "").split("\n") if pages.get(n) else []
        stream = "BT /F1 8 Tf 11 TL 40 760 Td " + " ".join(f"({_escape(x)}) Tj T*" for x in lines) + " ET"
        data = stream.encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(data), data))
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            b"/Contents %d 0 R >>" % content
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def report_pdf(rows: list[dict]) -> bytes:
    return pdf(description_pages(rows) | table_pages(rows))
