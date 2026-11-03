# KLEAR eval

The eval asks the agent every question in eval/golden and checks each answer.

Run it with: uv run python -m klear.eval run [--mode replay|live] [--agent stub|recorded|klear|scripted] [--model ID] [--only ID_CATEGORY_OR_TAG]

Agents:

- stub: simple keyword answers that test the checks themselves, in either mode.
- recorded: plays back a saved live run, replay mode only (see below).
- klear: the real agent, live mode only. It needs --model, and that one model sets both the calls and the cost estimate.
- scripted: plays the saved model replies in eval/scripts/<golden_id>.yaml through the real agent with a fake client, replay mode only. The tools and answer checks run exactly as in a live run, with no API call and no cost. Set the folder with --scripts. The format is in eval/scripts/README.md.

Ask one question: uv run python -m klear.agent "question" [--district house:NNN] [--model ID] [--store PATH]. It shows the most the question can cost and the money spent so far, asks for y (or the word continue when the spend total is uncertain), then prints the answer, the refusal type and the actual cost. It never prints the API key.

Before choosing a model or running the demo, read the compare and demo answers yourself. The automatic checks cannot judge meaning.

Live runs save every answer and its cost under eval/recordings/<model>-<timestamp>/. After a fix to the checks, rescore a saved run for free with --mode replay --agent recorded --recording <model>-<timestamp>. The JSON report also keeps each structured answer.

A run that stops early (a declined prompt, the spend cap, or a call that cost more than expected) exits 2.

## What the system prompt must teach

- No digits, unit words or number words in the text: every number, vintage and place comes from a marker.
- Use share, not percent.
- Bullets start with * or -.
- Every {vintage:REF} shares a block or caveat with its {fact:REF}, and every source shown gets its vintage at least once.
- Name only allowed places: the district, its counties, the state and the places the question names.
- Find the district even when declining, unless no district is involved.
- Spell Hawaiian place names with ʻokina and kahakō.
- Use {label:REF} for definitions such as age thresholds.
- Say unreliable for any shown estimate with CV above 0.30, name the programs asked for in the text, and say higher, lower or within the margin of error in comparisons.
- When declining partisan questions, never repeat party names or advocacy phrases.
- The agent's max_call_cost comes from the API max_tokens limit plus its counted input tokens.

## Spending cap

Live runs are capped at 60.00 dollars per user, across every run and every copy of this repository.

- Spending is logged in ~/.config/klear/eval_spend.jsonl, with a lock file ~/.config/klear/eval_spend.lock beside it. No option or environment variable moves them.
- A second copy of the total is kept in ~/.config/klear/eval_spend_total, and the lock file keeps the highest total ever seen. The cap uses the largest of the three, and live runs refuse to start if the log or the copy goes missing after money was spent.
- Each call first holds its worst-case cost, then records the actual cost. Held amounts count against the cap, so a crashed run still shows as spent.
- Cached input is counted at the full input price, so the log can show more than the provider bill. That is on purpose and does not mean caching failed.
- If a call returns usage numbers that make no sense, it is recorded at its full worst case and the spend total is marked uncertain. The next live run warns and needs the word continue. Delete ~/.config/klear/eval_spend.uncertain only after checking the provider dashboard.

Limitation: the cap trusts the worst-case cost each agent reports. An agent that reports too low a cost can still overspend, so also set a spend limit on the provider account.

## Unreliable estimates

An estimate with a margin of error is unreliable when its coefficient of variation CV = (MOE / 1.645) / estimate is above 0.30 (the Census standard). Facts that carry a range instead of an MOE are exempt.
