# promo-finder

Kuku AI Generalist assignment, Problem 1 (Find the Promos). Priyanshi Jain, roll 220823,
Aerospace Engineering.

## Quick start (2 minutes, no API key, no real show data)

The repo ships with an invented 5-episode demo show in `demo/show`, so everything below runs
without any confidential content. Needs Python 3.11 or newer.

```bash
python -m venv .venv
pip install -r requirements.txt
pip install -e .
streamlit run app/review.py
```

Activate the venv first (Windows PowerShell: `.\.venv\Scripts\Activate.ps1`, Mac or Linux:
`source .venv/bin/activate`). The app opens at http://localhost:8501 on the invented demo show.
On Windows, `.\scripts\run_app.ps1` does all of this in one go. The app's Run tab analyses any
folder of episode `.txt` files (offline mode needs no key). To run the full pipeline with models:
copy `.env.example` to `.env`, add a key, then `promo run <folder> --show NAME`. The real
50-episode run described in the solution doc is not in this repo, because the input pack is
confidential.

Reads a microdrama screenplay folder (one `.txt` per episode with timestamped CAMERA, ACTION,
SOUND FX and dialogue blocks) and writes a ranked list of 20 to 60 second promo clips: the
episode and start/end of every segment, the hook, the cliffhanger, a one-line reason and the gate
results. Models only pick line IDs, code turns them into times and enforces length, the 3 second
hook window, cut points and segment order. The model behind each step is swapped by config only.

## Setup

```bash
uv sync
cp .env.example .env      # then put your keys in .env (it is git-ignored)
```

`.env` holds `GEMINI_API_KEY`, `GROQ_API_KEY` and, for any other OpenAI-compatible endpoint,
`CUSTOM_BASE_URL`, `CUSTOM_API_KEY`, `CUSTOM_MODEL`. Only the keys for profiles that are actually
used are needed. Prompts and clips are sent to the configured providers, and free tiers may use
traffic for training, so use a paid or private endpoint for confidential shows.

## Run on a new show

(Commands below are written with `uv run promo ...`. If you installed with pip as above, just
type `promo ...`.)

```bash
uv run promo run path/to/show_folder --show myshow
```

Check a new folder first, it takes a second: `uv run promo inspect path/to/show_folder` prints the
parse coverage, duration and token counts. `uv run promo estimate path/to/show_folder` prints the
planned calls and tokens per stage against each profile's daily quota. Add `--episodes 1-5` for a
quick sample run. Everything is cached and hash-skipped: running the same command again takes under
a second, and changing a prompt or parameter reruns only the stages it affects.
`uv run promo eval --show myshow` compares the result with baselines (see Eval).

## Swap or add a model

Profiles live in `config/models.yaml`. Add a block, no code change:

```yaml
profiles:
  my_model:
    base_url: https://example.invalid/v1      # any OpenAI-compatible endpoint
    api_key_env: MY_MODEL_API_KEY
    model: some-model-id
    rpm: 30
    tpm: 200000
    rpd: 1000
    max_input_tokens: 100000
    max_output_tokens: 8000
    json_mode: false        # turn off if the endpoint rejects response_format
    extra_body: {}          # passed through as-is (reasoning controls and similar)
    fallback: [gemini_lite] # tried in order when this profile fails
```

`config/pipeline.yaml` maps each stage (bible, propose, montage, coldview, judge) to a profile.
Override at run time without editing files:

```bash
uv run promo run folder --show myshow --profile-map propose=my_model,judge=my_model
uv run promo run folder --show myshow --out-suffix alt --profile-map propose=my_model
```

`--out-suffix alt` writes to `outputs/myshow__alt/`, and `promo eval` then reports how much the
two top 10 lists overlap. The `custom` profile reads its URL, key and model from the environment,
so an interviewer's endpoint needs no file edits. Profiles that are too small for a request (input
or tokens-per-minute limit) are skipped automatically, quota and rate limits are tracked per
profile, and a daily quota hit moves straight to the next fallback.

## Outputs

Everything lands in `outputs/<show>/` (git-ignored, it holds script text):

| file | what it is |
|---|---|
| `promos.csv` | one row per segment: rank, promo id, episode, start, end (`mm:ss.cc` and seconds), hook, cliffhanger, why, post-first reason, failed soft gates |
| `promos.json` | the same with full gate results, cold-view scores and ranking scores |
| `report.md` | summary numbers, the top 20 with clip transcripts, gate table, drop reasons, failures, tokens and time per stage, models used |
| `parse.json`, `bible.json`, `propose.json`, `montage.json`, `build.json`, `gates.json`, `coldview.json`, `rank.json` | one artifact per stage, each with `meta`, `items` and `failures` |
| `run_log.jsonl` | one line per model call: stage, profile, model, tokens, cost, latency, cache hit, ok, error. IDs and counts only, never script text |
| `manifest.json` | input hashes per stage, used to skip unchanged work |
| `eval.md`, `eval.json` | from `promo eval` |

Invented example of two `promos.csv` rows (a two-segment promo):

```
rank,promo_id,kind,seg_no,episode,start,end,start_s,end_s,duration_total,hook_type,hook,cliffhanger,why,post_first_reason,gates_failed
1,a1b2c3d4e5f6,montage,1,3,00:41.20,00:55.00,41.20,55.00,31.30,reveal,"The harbor master is her father","Who opened the vault?","Opens on the reveal, ends before the answer","opens on a reveal and ends unresolved",
1,a1b2c3d4e5f6,montage,2,7,01:02.10,01:19.40,62.10,79.40,31.30,reveal,"The harbor master is her father","Who opened the vault?","Opens on the reveal, ends before the answer","opens on a reveal and ends unresolved",
```

## Pipeline in one paragraph

`parse` (tolerant, case-insensitive parser, safe cut points that fall inside no dialogue) then
`bible` (one long-context call: premise, characters, an alias map that merges descriptive speaker
tags into characters, open questions, spoiler lines) then `propose` (parallel chunks of episodes,
with the next episode as read-only lookahead, plus one `montage` call) then `build` (code: snaps
every start and end to a cut, keeps the hook within 3 s of the start, fixes length by extending
over non-dialogue or splitting in two, makes tight and loose variants, removes duplicates) then
`gates` (code gates on length, cuts, hook window, segments, plus soft gates on honest promise and
spoilers) then `coldview` (a model that has seen only the clip text states who, stakes and the open
question, and scores hook, clarity and curiosity) then `rank` (pointwise score, Swiss pairwise
judging in both orders, Bradley-Terry, diversity re-rank) then `report`. The pairwise judge sees each
clip as three parts (the opening, the full clip, the last lines) and answers three separate
questions, hook, ending and clarity, the code takes the majority. `report` keeps only the best
variant (tight or loose) of each proposal, so a moment appears once in `promos.csv`.

## Defect audit

`promo audit --show NAME` applies the same rule-based checks (opens mid-sentence, no speech in the
first 3 s, ends mid-sentence, contains the answer to an open question, contains a spoiler line) to
the tool's top clips and to every baseline, and writes `audit.md`. It uses no model and no labels,
so it is free, repeatable and cannot be swayed by a judge's taste. It counts broken cuts, it does
not measure how good a clip is. A model-free check of the same kind is the second run with another
model (`--out-suffix`): `audit.md` reports how many of the top 20 the independent run also found.

## Cut polish

The report step (code only, no model call) cleans the final list: a clip that opens on the tail of
a sentence starts on the hook line instead, a clip that contains the line where the bible says its
own question is answered is cut before that line (or flagged G8 and sent to the bottom), and
near-duplicates (time overlap above 0.4 or word overlap above 0.5) are dropped. To apply it to a
finished run without any model call: `promo polish --show NAME`. Cold-view and judge scores were
computed on the unpolished clip and are kept as they were.

Note: stage hashes cover the prompt files, the profile and the input bytes. For a show of 100+ episodes raise `max_calls` and `max_tokens` in `config/pipeline.yaml`
(`promo estimate` prints the planned calls; the guard aborts before any call if they are exceeded).

A saved run only
hash-skips when those are unchanged, so after editing a prompt (or on another machine whose
profile or data bytes differ) `promo run` calls the models again. Use `promo polish` and
`promo eval` on a saved run instead of `promo run`.

## Review app

A small Streamlit app for browsing and approving the result:

```bash
streamlit run app/review.py
```

It lists the ranked promos with filters, shows every segment, the hook, cliffhanger, reason, gate
results, cold-view scores and the timed transcript a viewer would see, and appends approve, trim or
reject decisions to `outputs/<show>/review.csv`. A second tab shows the eval and failure notes, a
third runs the pipeline on a folder (offline mode needs no API key). It reads local files only, so
do not deploy it with the show data: run it on your own machine.

## Offline mode

`uv run promo run folder --show myshow --offline` needs no API key and makes no model call. It
uses a keyword and emotion lexicon to find hooks, the same build and code gates, and orders by the
heuristic score. It is meant as insurance when an API is down and as a baseline, not as a
replacement: no cold view and no pairwise judging.

## Eval

`uv run promo eval --show myshow` judges the tool's top 10 against four baselines (random 45 s,
last 45 s of an episode, one prompt over the whole show, the offline heuristic), checks that the
judge prefers the original over versions with a buried hook or a cut after the answer, reports
diversity and hook type and episode spread, and, if `eval/labels/labels.csv` exists (columns in
`eval/labels/template.csv`, filled by hand and never by the tool), precision@10. `--no-llm` runs
the code metrics only. The single-prompt baseline is a model reply, so it is saved in
`outputs/<show>/eval_baselines.json` and reused (use `--refresh-baselines` to ask again).
`--only perturbation --out-name NAME` runs just the perturbation check into `NAME.json` and
`NAME.md` next to the main result, which is how a second judge model is compared on the same clips.

## Cost and speed

Measured on the 50-episode sample show (about 103 minutes of screenplay, 64 speaker tags), on
free-tier keys, so the money cost was $0. A full run is 139 model calls and about 389,000 tokens
(in plus out, counting every attempt): bible 4 calls, propose 13, montage 1, cold view 29, pairwise
judging 92. Wall clock was about 12 minutes, 6 of them in the pairwise stage, which is limited by
requests per minute rather than tokens. `promo eval` adds about 110 judge calls. A rerun with
nothing changed finishes in under a second (every stage is hash-skipped), and changing one prompt
or parameter reruns only the stages after it, with the rest served from the cache.

`promo estimate` prints the same numbers before a run, as upper bounds, next to each profile's
requests and tokens left today (the quota resets at Pacific midnight for Gemini and UTC for Groq).
The prices in `config/models.yaml` are left at 0, so the cost column in `run_log.jsonl` and
`report.md` shows $0 until you fill in real per-million-token prices.

## Known failure modes

- The cold view and the judge only see text, so a promo that reads well can still be weak on
  screen: picture, music and performance are not judged.
- Open endings are cheap to get: the cold view called almost every clip open-ended, so that gate
  barely filters. The pairwise judge and the ranking do the real selecting.
- The model proposing a window can pick a cliffhanger that is already answered a few seconds
  later. The proposal is told to end before the answer and the lookahead episode is shown, but a
  human should read the top clips.
- Spoiler control is only as good as the bible's list of spoiler lines, and it is a soft penalty.
- Speaker names matter. If the bible fails to merge descriptive tags, the clip text shows several
  names for one person, which lowers the cold-view score. Unresolved tags are listed in the run
  output.
- Free-tier limits and outages are the main run-time risk. A model that fails every attempt costs
  minutes of retries before its fallback is used.
- Hand labels are the only ground truth. Judge win rates measure agreement with an LLM, not with
  an editor.

## Development

```bash
uv run pytest -q
uv run ruff check .
```

Tests use an invented three-episode show and a scripted fake provider, no network and no real
script text. Design notes and decisions are in `docs/spec.md` and `docs/assumptions.md`.
