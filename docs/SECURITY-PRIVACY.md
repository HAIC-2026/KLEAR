# KLEAR security and privacy

## Text for the submission form

KLEAR holds only public, aggregated data: Census, federal and State tables for districts, counties and the state, and the locations of public help sites. It has no accounts and no personal records. It answers about communities, never individuals, and declines personal and partisan requests.

What leaves the server: in assistant mode, the question, with the visitor's last two questions in that tab, and the public figures the assistant looks up go to the AI provider, Anthropic (Claude Haiku 5.5). The note under the question box says so and asks people not to type personal details. A street address goes to the U.S. Census Bureau geocoder only to find its Census block. The operator can turn address lookup off, and without an AI key KLEAR uses free lookups with no model call.

What is stored: nothing a visitor types. The server keeps no request logs, writes no question or address to disk and opens the database read-only. Chat history stays in the visitor's browser tab. API keys stay on the server.

Controls: a content security policy allows only KLEAR's own scripts, styles and images and blocks framing. The server returns only a fixed list of files, refuses chat requests from other sites, allows 6 questions a minute per address and 3 answers at a time, and stops model calls at a daily cap of 5 dollars.

Prompt injection: the model never writes a number. It writes text with markers, and code fills every figure, margin of error and source from the database, then rejects any answer with an unsourced number or an unasked place. A hostile question can change wording, not data.

Production would add State-approved hosting behind an HTTPS proxy with per-visitor limits, keys in a secret manager with rotation, a review of the provider's data retention terms, abuse monitoring and a spend limit set outside the app.

## Where each control lives

- No request logs: Handler.log_message in klear/web/__main__.py.
- Content security policy, nosniff and no framing: Handler.end_headers in klear/web/__main__.py.
- Fixed file list: PUBLIC in klear/web/__main__.py. Any other file path gets a 404.
- Cross-site chat requests refused: Handler.do_POST compares the Origin header with the Host header.
- 6 questions a minute and 3 answers at a time: PER_MINUTE and BUSY in klear/web/__main__.py.
- Daily cap: DailyBudget in klear/web/assistant.py, 5 dollars per day in Hawaiʻi time, then the free lookup.
- Read-only database: every web connection opens klear.duckdb with read_only=True.
- Keys: klear/config.py reads them from the environment or a .env file on the server.
- Chat history: sessionStorage in klear/web/static/app.js, one browser tab only.
- Address lookup: klear/web/geocode.py sends the address to geocoding.geo.census.gov and nowhere else. The --no-address-lookup option turns it off.
- Numbers filled by code and answer checks: build_answer in klear/agent/answer.py and answer_problems in klear/eval/scorers.py.
