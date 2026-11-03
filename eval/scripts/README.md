# Scripted agent turns

Each file eval/scripts/<golden_id>.yaml scripts the model side of one golden question for --agent scripted, which only runs with --mode replay. The script replays through the real agent loop, so the tools run against the store, submit_answer goes through build_answer, and a rejected submit_answer gets the same retry message a live model would see. No API call is made and usage is recorded as zero.

Format: a list of assistant turns. Each turn is a list of content blocks, and each block is one of:

- type: text, with text (a string)
- type: tool_use, with name (a tool or submit_answer) and input (a mapping of its arguments)

Write fact refs such as f1 in the order the tools will hand them out in that session. A turn without a tool_use block counts as a plain-text reply and gets the nudge to use the tools. The script fails as a recording error when it runs out before the loop finishes or has turns left after the answer is accepted.

Example: find_district-001.yaml resolves House District 18, fetches its total population and submits the answer.

Run: uv run python -m klear.eval run --mode replay --agent scripted --only find_district-001

