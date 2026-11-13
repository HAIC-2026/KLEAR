"""Earlier questions from the same browser tab, sent back with a follow-up so it keeps its subject."""

import re
from dataclasses import dataclass

from klear.eval.contract import geo_display, geo_key

# At most this many exchanges, each question at most this long, each with at most this many indicator ids.
MOST = 2
QUESTION_MAX = 500
INDICATORS_MAX = 40
PLACE_TYPES = ("house", "senate", "county", "state")
INDICATOR_ID = re.compile(r"[a-z0-9_]{1,64}")


@dataclass(frozen=True)
class Exchange:
    """One earlier question, the place its answer was about and the measures it showed. Never answer text."""

    question: str
    district: tuple[str, str] | None = None
    indicators: tuple[str, ...] = ()


def parse_earlier(con, raw) -> tuple[Exchange, ...]:
    """Earlier exchanges from a request, checked against the store. A wrong shape or size raises ValueError;
    an indicator or place the store does not hold is dropped, since the store may have been rebuilt since."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MOST:
        raise ValueError("earlier must be a list of at most two exchanges")
    known = {r[0] for r in con.execute("SELECT indicator_id FROM indicator").fetchall()}
    out = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"question", "district", "indicators"}:
            raise ValueError("an earlier exchange holds only question, district and indicators")
        question, place, ids = item.get("question"), item.get("district"), item.get("indicators", [])
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= QUESTION_MAX:
            raise ValueError("an earlier question must be text of up to 500 characters")
        if place is not None and not isinstance(place, str):
            raise ValueError("an earlier district must be text or null")
        if not isinstance(ids, list) or len(ids) > INDICATORS_MAX:
            raise ValueError("earlier indicators must be a list of at most forty ids")
        if not all(isinstance(i, str) and INDICATOR_ID.fullmatch(i) for i in ids):
            raise ValueError("earlier indicators must be indicator ids")
        out.append(
            Exchange(
                question.strip(),
                _place(con, place),
                tuple(i for i in dict.fromkeys(ids) if i in known),
            )
        )
    return tuple(out)


def _place(con, text: str | None) -> tuple[str, str] | None:
    """A stored House or Senate district, county or the state, or None."""
    key = geo_key(text) if text else None
    if key is None or key[0] not in PLACE_TYPES:
        return None
    if key[0] in ("house", "senate") and key[1].isdigit():
        key = (key[0], f"{int(key[1]):03}")
    found = con.execute("SELECT 1 FROM geography WHERE geo_type = ? AND geo_id = ?", list(key)).fetchone()
    return key if found else None


def earlier_block(con, earlier: tuple[Exchange, ...]) -> str:
    """The earlier exchanges as a short block for the model, oldest first, or an empty string."""
    if not earlier:
        return ""
    lines = []
    for x in earlier:
        line = f"- Question: {x.question}"
        if x.district is not None:
            row = con.execute(
                "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", list(x.district)
            ).fetchone()
            shown = geo_display(*x.district, row[0]) if row else ":".join(x.district)
            line += f"\n  Answered for: {shown} ({':'.join(x.district)})"
        if x.indicators:
            line += f"\n  Measures shown: {', '.join(x.indicators)}"
        lines.append(line)
    return "Earlier in this conversation, oldest first:\n" + "\n".join(lines)
