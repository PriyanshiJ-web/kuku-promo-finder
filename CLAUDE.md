# promo-finder: working rules

## What we're building
A rerunnable Python CLI that reads a microdrama screenplay folder (one .txt per episode,
timestamped CAMERA / ACTION / SOUND FX / dialogue blocks) and outputs ranked 20-60 s promos.
For each promo, every segment has an episode, a start and an end, plus the hook, the
cliffhanger, a one-line "why", gate results and a rank. Reviewers will run it on a NEW show,
possibly with a different OpenAI-compatible model. The full technical spec is in
docs/spec.md. READ IT before every phase. It is the source of truth; if it is ambiguous,
follow the "Ambiguity" rule below.

## Roles
You are the implementer. A separate reviewer checks every phase. Do only the current phase.
Do not start the next phase, and do not "improve" earlier phases unless the prompt says so.

## Hard rules
1. CONFIDENTIAL DATA. data/ and outputs/ are never committed. Never print more than one
   line of script text to the terminal or logs (IDs, counts and times are fine). Test fixtures
   use INVENTED dialogue only. Never copy real lines into tests, docs, README or prompts.
2. MODELS NEVER OUTPUT TIMES. LLMs reference line IDs only (e.g. E07-L042). Code maps IDs to
   seconds and enforces length, the hook window, cut points and segment order.
3. NO GOLD LABELS. Never create, guess or fill eval/labels/*. Only empty templates.
4. NO INVENTED MODEL IDS OR LIMITS. In config/models.yaml, leave `model:` values as
   "FILL_ME" with a comment unless the human provides them. Do not guess rate limits:
   use conservative placeholders marked `# placeholder`.
5. DEPENDENCIES: only those in the Stack section. Ask before adding anything else.
6. NEVER weaken, skip or delete a test to make it pass. If a test is wrong, say so in the
   HANDOFF report.
7. No bare `except:` and no silently swallowed errors. Every LLM failure is recorded in
   run_log.jsonl and in the stage output under `failures`.
8. Keep modules small (aim for under 250 lines). Prefer plain functions and pydantic models over
   class hierarchies. No LangChain or other agent frameworks.

## Stack
Python 3.11+, uv, pydantic>=2, openai (async client with custom base_url), pyyaml, typer,
rich, tenacity (optional; a hand-rolled retry is fine), sqlite3 (stdlib), pytest,
pytest-asyncio, ruff. Optional later: streamlit (review app only).

## Commands (must keep working once each one exists)
uv run promo inspect <folder>
uv run promo estimate <folder> [--profile-map ...]
uv run promo run <folder> --show <name> [--episodes 1-5] [--offline] [--profile-map stage=profile,...] [--force STAGE]
uv run promo stage <stage> --show <name>
uv run promo eval --show <name>
uv run pytest -q   and   uv run ruff check .   must both pass before you report a phase done.

## Ambiguity
Pick the simplest option that satisfies the spec. Write it as one line in
docs/assumptions.md (format: "- [phase N] <decision> - <why>"). Keep going; don't stop to ask
unless it is a hard-rule question.

## Commits
Make small commits with plain messages ("parse: tolerate uppercase scene headers"). Commit at
the end of each phase at minimum.

## HANDOFF report (end every phase with exactly this)
### HANDOFF phase N
- Done: bullets mapping to each "Done when" item, each with ✅ or ❌
- Files: created/changed files, one line each
- Commands run + results: pytest summary line, ruff result, any CLI output (numbers only)
- Sample output: requested artifacts, as counts/IDs/times, no script text beyond 1 line
- Deviations from spec: what and why (or "none")
- Assumptions added: copied from docs/assumptions.md
- Known issues / TODO: honest list
- Tokens/cost used this phase (if any LLM calls)

## Style and git rules (from the human, strict)
- Never use em dashes (or en dashes) in any file, comment, doc, commit message or output. Use commas, colons or hyphens.
- Never add Co-Authored-By or any "generated with" line to commits or docs. Never sign commits (use `git -c commit.gpgsign=false commit`). Keep commit messages plain.
- Keep data/, outputs/, plan.md, plan-v2.md and sonnet-handoff.md out of git (see .gitignore).
