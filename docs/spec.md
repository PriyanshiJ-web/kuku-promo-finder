# promo-finder: technical spec v1

## 0. Input format (verified on the sample show)
Each episode file:
  EPISODE NUMBER: Episode <n>
  EPISODE LOGLINE: <text>
  CONTINUITY NOTES: <text>
  --- SCRIPT START ---
  SCENE <nn>
  Scene heading: <text>            # case varies: "SCENE HEADING:" also occurs
  Scene start: mm:ss.cc
  Scene end: mm:ss.cc              # may be missing on some files
  Actions and dialogue:            # case varies
  **<TAG>:**  or  **<TAG> (Emotion, Emotion):**  or  **<TAG> (O.S.) (Emotion):**
  Start: (mm:ss.cc) | End: (mm:ss.cc)
  <one text line>
  ... (blocks separated by blank lines)
  --- SCRIPT END ---
TAG ∈ {CAMERA, ACTION, SOUND FX} or a speaker name (any caps string). Times are relative to
the episode. CAMERA/ACTION/SFX/dialogue tracks OVERLAP in time. The same character may appear
under several tags (e.g. a descriptive tag early, real name later).
Parser must be tolerant: case-insensitive headers, optional fields, tolerate `m:ss`,
`mm:ss`, `mm:ss.c`, `h:mm:ss.cc`, extra whitespace, and multi-line text (join lines until the
next blank line or block header). Episode number: header → else filename regex
`(\d+)` first match → else sorted order. Unknown lines are counted in the parse report,
not fatal.

## 1. Data model (src/promo/schema.py, pydantic v2)
Kind = Literal["dialogue","action","camera","sfx"]
Line: id:str ("E{ep:02d}-L{idx:03d}"; widths grow if needed), ep:int, scene:int, idx:int,
      kind:Kind, speaker_raw:str|None, speaker:str|None (canonical, filled after bible),
      emotions:list[str], voice:Literal["OS","VO"]|None, start:float, end:float,
      gstart:float, gend:float (global = episode offset + local), text:str
Scene: num:int, heading:str, start:float|None, end:float|None
Episode: num:int, file:str, logline:str|None, continuity:str|None, duration:float
        (max(scene end, max line end)), offset:float, scenes:list[Scene], lines:list[Line],
        cuts:list[float]
ParseReport: episodes:int, blocks_by_kind:dict, lines_missing_time:int, end_before_start:int,
        out_of_scene:int, unknown_lines:int, coverage:float (parsed blocks / detected block
        headers), total_seconds:float, est_tokens_full:int, est_tokens_compact:int,
        warnings:list[str]
Show: name:str, episodes:list[Episode], report:ParseReport
  helpers: show.line(id)->Line, show.ep(n)->Episode

Bible: premise:str, characters:list[Character{canonical, aliases:list[str], role, first_ep}],
       arcs:list[{name, eps:list[int]}], reveals:list[{what, line_id}],
       open_questions:list[{idx:int, question, raised_at:line_id, resolved_at:line_id|None}],
       spoiler_line_ids:list[str]
Proposal (LLM output): ep:int, start_id, hook_id, cliff_id, end_id, hook_type:HookType,
       hook_text, cliffhanger_text, open_question, question_ref:int|None, why, confidence:float(0-1)
HookType = Literal["violence","reveal","humiliation","shocking_line","romance","threat","twist","other"]
MontageProposal: segments:list[{start_id,end_id}] (2-3), hook_id, cliff_id, hook_type,
       hook_text, cliffhanger_text, open_question, question_ref, why, confidence
Segment: ep:int, start:float, end:float, start_id:str, end_id:str   # local episode seconds
Candidate: id:str (stable hash of segments), kind:Literal["single","montage"],
       variant:Literal["tight","loose"], segments:list[Segment], hook_id, cliff_id,
       duration:float, hook_type, hook_text, cliffhanger_text, open_question, question_ref,
       why, confidence, source:str (proposal ref), notes:list[str]
GateResult: gate:str, passed:bool, hard:bool, reason:str
ColdView (LLM): clip_ref:str, who:str, stakes:str, next_question:str, makes_sense:bool,
       open_ending:bool, hook:int(1-5), clarity:int(1-5), curiosity:int(1-5)
Promo (final): rank:int, candidate:Candidate, gates:list[GateResult], coldview:ColdView|None,
       pointwise:float, bt:float|None, final:float, post_first_reason:str

## 2. Safe cut points (src/promo/parse/cuts.py)
For each episode: B = {start and end of every block of any kind} ∪ {0, duration}.
cuts = sorted(t ∈ B such that there is NO dialogue line d with d.start + 0.1 < t < d.end - 0.1).
Helpers: cut_at_or_before(ep, t), cut_at_or_after(ep, t), cuts_between(ep, a, b).

## 3. Prompt rendering (src/promo/parse/render.py)
compact(ep, include_camera=False):
  "E07-L042 [01:12.4] MARA (gloating): \"The harbor is mine now.\""
  "E07-L043 [01:13.0] ACTION: Tobin staggers back."
  "E07-L044 [01:13.5] SFX: ..."
Speaker uses the canonical name when available. Camera lines are omitted by default.
clip_text(candidate, show): what a cold viewer "sees". Lines inside the segments (all kinds
except camera), canonical names, times relative to clip start "[03.2s]", segment breaks
marked "--- CUT ---". NO episode numbers, NO line IDs.
Token estimate = ceil(chars / 4).

## 4. LLM layer (src/promo/llm/)
config/models.yaml:
  profiles:
    <name>: {base_url, api_key_env, model, rpm, tpm, rpd, max_input_tokens, max_output_tokens,
             json_mode: bool, supports_seed: bool, price_in_per_m, price_out_per_m,
             fallback: <profile>|null, concurrency: int}
  Ship profiles: gemini_flash (base_url https://generativelanguage.googleapis.com/v1beta/openai/),
  groq (https://api.groq.com/openai/v1), custom (all from env: CUSTOM_BASE_URL,
  CUSTOM_API_KEY, CUSTOM_MODEL), fake (tests only).
config/pipeline.yaml:
  stages: {bible: gemini_flash, propose: gemini_flash, montage: gemini_flash,
           coldview: groq, judge: groq}
  params: {min_len: 20, max_len: 60, hook_window: 3.0, min_segment: 3.0, free_eps: 10,
           chunk_eps: 5, max_props_per_ep: 6, coldview_batch: 6, top_k_pairwise: 24,
           swiss_rounds: 4, mmr_lambda: 0.7, top_n_report: 20, max_calls: 400,
           max_tokens: 2_000_000}
  `--profile-map stage=profile` overrides at runtime.
Provider protocol: async complete(messages, *, json_mode, max_tokens, seed) -> (text, usage)
  OpenAIProvider (openai.AsyncOpenAI with base_url) and FakeProvider (scripted responses,
  can raise 429/500, can return invalid JSON). Only send params the profile supports.
Limiter: per-profile async token bucket for rpm and tpm (estimate tokens before call),
  plus asyncio.Semaphore(concurrency). On 429/5xx: exponential backoff with full jitter,
  honour Retry-After if present, max 5 attempts, then try `fallback` profile once, then fail.
Cache: sqlite (WAL) at .cache/llm.sqlite. key = sha256(provider model | prompt template
  sha | rendered messages | params). Store raw text + usage. A cache hit costs 0 and is logged.
Structured calls: `await llm.call_json(stage, prompt_name, vars, schema) -> T | None`
  1) render prompts/<prompt_name>.md (simple {{var}} substitution, no jinja)
  2) call; strip ``` fences; json.loads; pydantic validate
  3) on failure: one retry with the validation error appended ("Fix and return only JSON")
  4) on second failure: log failure, return None. NEVER raise to the stage.
Ledger: outputs/<show>/run_log.jsonl, one line per call: ts, stage, profile, model,
  prompt_name, in_tokens, out_tokens, cost_usd, latency_s, cache_hit, attempt, ok, error.
Budget guard: if planned calls or tokens exceed max_calls/max_tokens, abort before
  the stage starts with a clear message.

## 5. Stages and runner (src/promo/runner.py, src/promo/stages/*)
Each stage: `async def run(ctx) -> Artifact`; writes outputs/<show>/<stage>.json with
  {"meta": {...}, "items": [...], "failures": [...]}.
Runner keeps outputs/<show>/manifest.json: per stage the input hash (hash of upstream
  artifacts + stage params + prompt template shas + profile/model). Same hash → skip,
  unless --force. Writes are atomic (write temp, then rename).
Order: parse → bible → propose (+montage) → build → gates → rank → report.

### parse (code)
Parse the folder → Show. Write parse.json (episodes without lines to keep it small) and
parse_report.json. Abort if coverage < 0.95 with a message listing the warnings.

### bible (1 call; map-reduce if needed)
Input: all loglines + continuity notes + compact script of all episodes.
If est tokens > profile.max_input_tokens * 0.8: summarise chunks of chunk_eps episodes in
parallel (prompt bible_chunk.md), then merge (bible.md receives the chunk summaries).
Post-process: drop line IDs that don't exist; build alias→canonical map; fill Line.speaker.
Fallback when the call fails: Bible with premise="" and characters derived from distinct
speaker tags (no aliasing). Record the failure.

### propose (parallel chunks)
Chunks of chunk_eps episodes. Each prompt receives:
  - bible digest (premise, characters + roles, numbered open_questions): SAME PREFIX for
    all chunks
  - compact script of the chunk's episodes (editable)
  - compact script of the NEXT episode, marked READ-ONLY LOOKAHEAD (do not propose from it)
Returns {"proposals": [Proposal...], "beats": [{line_id, hook_type, text}]}, with up to
max_props_per_ep proposals per episode and 3-5 beats per chunk.
Montage: one call with bible digest + all beats → 5-10 MontageProposal.
Validation: drop proposals whose IDs don't exist, cross episodes (single), or violate
start ≤ hook ≤ cliff ≤ end order. Count drops by reason.

### build (code; the most important stage, test it heavily)
Single proposal → segment:
  hook = line(hook_id). Window W = [hook.start - hook_window, hook.start].
  start = the earliest cut in W that is ≥ line(start_id).start; if none, the latest cut in W;
          if none, hook.start (and add note "start_not_on_shot_boundary").
  end   = cut_at_or_after(max(line(cliff_id).end, line(end_id).end)), capped at duration.
  Length fix:
    if end - start < min_len: extend end cut by cut while the added interval contains NO
      dialogue lines (only action/camera/sfx), stopping at max_len. Still short → drop
      ("too_short").
    if end - start > max_len: make 2 segments: seg1 = [start, c1] with c1 = latest cut ≤
      start + 30; seg2 = [c2, end] with c2 = earliest cut ≥ c1 such that end - c2 ≤ max_len -
      (c1 - start), and require end - c2 ≥ min_segment and seg2 containing the cliff line.
      Impossible → drop ("too_long").
Variants: tight (as above) and loose (start one cut earlier if the hook stays ≤ hook_window
  from start; end one cut later only if that adds no dialogue). Skip loose if identical.
Montage → each segment: start = cut_at_or_before(line(start_id).start),
  end = cut_at_or_after(line(end_id).end). First segment must contain the hook within
  hook_window of its start; each segment ≥ min_segment; total in [min_len, max_len];
  segments chronological (ep, start) unless the proposal says otherwise; a segment never
  spans episodes. Violations → drop with reason.
Dedupe: two candidates are duplicates if global-time IoU (union of segments) > 0.5 OR
  dialogue word-set Jaccard > 0.6. Keep higher confidence (then tight over loose).
Output: candidates.json + drop counts by reason.

### gates
Code gates (hard): G1 length in [min_len, max_len]; G2 every boundary ∈ cuts;
  G3 hook line start - first segment start ≤ hook_window; G_seg segment ≥ min_segment
  and chronological.
LLM gates (batched coldview, coldview_batch clips per call, clips labeled A, B, C…,
  shuffled, clip_text only): G4 open_ending == true (hard); G5 makes_sense and who and
  stakes non-empty (hard).
Soft gates (penalties, not drops): G6 honest promise: question_ref is not None and the
  bible open_question has resolved_at ≠ None → pass; else fail (penalty 0.1).
  G7 spoiler: any clip line ∈ spoiler_line_ids → fail (penalty 0.15).
Only candidates passing all hard code gates go to coldview (saves calls).

### rank
pointwise = 0.4*hook + 0.25*curiosity + 0.2*clarity (each scaled to 0-1)
          + 0.15*confidence − soft-gate penalties + 0.05 if hook ep ≤ free_eps.
Take top_k_pairwise survivors by pointwise. Swiss: round 1 pairs neighbours in pointwise
order; later rounds pair by current wins (ties by pointwise), no rematches. Each pair is
judged TWICE (A/B then B/A). Both orders agree → winner gets 1. Split → 0.5 each
(position bias). Judge sees two clip_texts only and returns {winner:"A"|"B", reason}.
Bradley-Terry via MM: init p=1; repeat 200 iterations or until converged:
  p_i = (W_i + 0.5) / Σ_j n_ij / (p_i + p_j); normalise. Add a 0.5 pseudo-win prior.
final = 0.7*normalised_log_bt + 0.3*pointwise (for candidates outside top_k:
  final = pointwise − 1, so they rank below).
MMR re-rank: greedy; score = λ*final − (1−λ)*max_sim(to selected);
  sim(a,b) = min(1, max(IoU, jaccard) + 0.3*[same ep] + 0.2*[same hook_type]).
post_first_reason: judge reasons from that promo's wins, condensed to one line by code
  (pick the most common phrase / first reason), no extra call.

### report (code)
promos.json (list of Promo), promos.csv (one row per segment: rank, promo_id, kind,
seg_no, episode, start "mm:ss.cc", end "mm:ss.cc", start_s, end_s, duration_total,
hook_type, hook, cliffhanger, why, post_first_reason, gates_failed), report.md: summary
numbers, top top_n_report with clip transcripts (local only, outputs/ is ignored), gate
pass/fail table, drop reasons, failures, tokens/cost/time per stage from run_log.jsonl,
profiles + models used.

### offline mode (src/promo/heuristics.py)
No LLM. Bible = speaker-tag characters. Proposals: for each line whose text/emotions match a
hook lexicon (slap, strike, grab, scream, gasp, reveal, kneel, blood, poison, kiss, "!",
emotions like shocked/furious/horrified), propose start=hook=that line, cliff=end=the last
line before hook.start+45. confidence = lexicon density in the window, plus a bonus if
within the last 30 s of the episode. Same build + code gates. pointwise = confidence.
No coldview or pairwise.

## 6. Eval (eval/)
baselines.py → candidates in the same schema: random_45 (seeded, snapped to cuts),
  last_45 (last 45 s of each episode, snapped), single_prompt (one LLM call on the whole
  compact show: "top 10 promos as line IDs"), heuristic (offline mode).
perturb.py: for the tool's top 10, early = start moved 8 s earlier (snapped), late = end
  extended past the next 2 dialogue lines (snapped). Judge original vs perturbed, both orders.
metrics.py writes outputs/<show>/eval.json + eval.md:
  win-rate of the tool's top 10 vs each baseline's top 10 (pair by rank, both orders)
  perturbation preference rate (original wins)
  precision@10 vs eval/labels/labels.csv if present (hit = overlap ≥ 50% of the shorter span,
    same episode); otherwise print "no labels"
  diversity = 1 − mean pairwise IoU of the top 10; hook_type and episode spread
  swap overlap: if outputs/<show>__<alt>/promos.json exists, top-10 overlap (IoU ≥ 0.5 match)
eval/labels/template.csv header only: episode,start,end,hook_note,cliffhanger_note,quality_1to5
