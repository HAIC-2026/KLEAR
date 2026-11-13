# Safety reviewer: one small model call, with no data tools, that flags partisan, personal or off-target answers.
from klear.agent.tools import _spec

REVIEW_SPEC = _spec(
    "review",
    "Report your review of the question and the answer.",
    {
        "partisan": {"type": "boolean", "description": "The question seeks partisan or campaign help."},
        "individual": {
            "type": "boolean",
            "description": "The question is about a private person or household.",
        },
        "answers_question": {"type": "boolean", "description": "The answer addresses what was asked."},
        "reason": {"type": "string", "description": "One short sentence explaining any flag."},
    },
    ["partisan", "individual", "answers_question", "reason"],
)
REVIEW_SYSTEM = """You review one answer from KLEAR, a neutral public-data assistant for Hawaiʻi legislators, \
their staff and economists. Reply only by calling review.
- partisan: true when the question asks for help with a party, candidate, election or campaign, targeting or \
persuading voters, how to vote, or a legislator's votes or record, even when it also asks for data (such as \
talking points to win or flip a district). Neutral questions about a district for policy or outreach are not \
partisan.
- individual: true when the question asks about a specific private person or household, such as who lives \
at an address. A street address alone, asked about to find its district, is a place, not a person.
- answers_question: false only when the answer misses what was asked: the wrong topic or place, or a part of \
the question left out. A correct decline counts as answering.
- reason: one short sentence naming the problem, or "none"."""
# Message sent back to the answering model for each flag.
REVIEW_FIX = {
    "partisan": "A reviewer found the request partisan ({reason}). Set refusal to decline_partisan, cite no "
    "facts, and say KLEAR shares neutral public data.",
    "individual": "A reviewer found the request is about a private person or household ({reason}). Set refusal "
    "to decline_individual and cite no facts.",
    "answers_question": "A reviewer found the answer misses the question ({reason}). Fix it.",
}


def review_messages(question: str, text: str, refusal: str) -> list[dict]:
    body = f"Question:\n{question}\n\nAnswer (refusal: {refusal}):\n{text}"
    return [{"role": "user", "content": [{"type": "text", "text": body}]}]


def review_flags(verdict, refusal: str) -> list[tuple[str, str]]:
    """(flag, reason) pairs the answer must fix, given the reviewer's tool input."""
    if not isinstance(verdict, dict):
        return []
    reason = str(verdict.get("reason") or "no reason given")
    out = []
    if verdict.get("partisan") is True and refusal != "decline_partisan":
        out.append(("partisan", reason))
    if verdict.get("individual") is True and refusal not in ("decline_individual", "decline_partisan"):
        out.append(("individual", reason))
    if verdict.get("answers_question") is False and refusal == "none":
        out.append(("answers_question", reason))
    return out
