### HANDOFF fixes (review of phases 4 to 8)

All numbers below are read from files in outputs/duke/ (eval.json, eval_perturb_groq_judge.json,
rank.json, promos.json, propose.json) or from run_log.jsonl. One commit per fix, plus a docs commit
for this file, the README and docs/assumptions.md.

#### 1) Variant collapse (commit 183d5f2)
- The report stage now keeps only the best-scoring variant (highest final, ties to the better rank)
  of each `source` and renumbers the ranks. rank.json still holds every variant for audit.
- Result: 120 ranked candidates became 73 promos in promos.json, promos.csv and report.md, 73 unique
  sources, 47 variants collapsed. The top 10 now holds ten different moments.
- Tests: `collapse_variants` unit test (best variant wins, renumbering, input not modified) and a
  pipeline test that promos.json and promos.csv never hold two variants of one source.
- Note: outputs/duke__alt was produced before this change, so its promos.json still has both variants.

#### 2) The 0.60 versus 0.4444 mismatch (commit 909902c)
- Cause: the single_prompt baseline is a model reply. Its first reply did not pass validation
  (missing ep, start_id and so on) and an invalid reply is never cached (by design, review item from
  phase 2). The repair call then returned a valid set. The second eval run asked the model again,
  got a different reply, and after validation and dedupe ended with 9 clips instead of 10, so the
  win rate was 4 of 9 pairs (0.4444, split 0.4444) instead of 6 of 10 (0.60, split 0.4). run_log.jsonl
  shows two separate single_prompt calls at 22:42 and 22:59, each with an invalid first reply.
- Fix: the single_prompt clips are now saved in outputs/duke/eval_baselines.json and reused between
  eval runs (`--refresh-baselines` asks again). The clip set from the first two runs is not
  recoverable. A test checks that a second eval makes no new single_prompt call.
- docs/handoff-phase7.md now quotes eval.json only. My earlier claim in chat and in that handoff that
  the tool "beats every baseline" was wrong for eval.json as it stood: 0.4444 against single_prompt
  is below 0.5.

#### 3) Structured judge (commit 4b454ec)
- prompts/judge_pairwise.md now shows each clip as OPENING (lines starting in the first 3.0 s), FULL
  CLIP and LAST LINES (lines on screen in the final 5 s). The model returns hook, ending and clarity
  as "A" or "B" plus a reason, the code takes the majority and the hook vote breaks a tie. Both orders
  are kept. rank.json pairs now store which clip won each question.
- Rerun of rank and eval: every upstream stage was skipped (0 calls). Rank made 100 provider calls,
  132,113 tokens, 390 s.
- Position bias (pairs whose two orders disagree): 10 of 48 = 20.8%, down from 19 of 48 = 39.6% with
  the single-question judge. Top 10 overlap with the previous ranking (before collapse): 6 of 10.
- eval.json, structured judge, judge profile gemini_lite (tool top 10, 1 win, 0.5 split, 0 loss):

  | baseline | pairs judged | tool win rate | split rate |
  |---|---|---|---|
  | random_45 | 10 of 10 | 0.85 | 0.1 |
  | last_45 | 10 of 10 | 0.75 | 0.3 |
  | heuristic | 10 of 10 | 0.70 | 0.4 |
  | single_prompt | 10 of 10 | 0.65 | 0.1 |

  The single_prompt set is a new reply (10 clips, now frozen), so this row is not comparable with the
  earlier 0.60 or 0.4444.
- Perturbation (original preferred): early 0.35 over 10 of 10 pairs, late 0.3571 over 7 of 8 pairs.
  Still far below the 0.8 target.
- Other: diversity 0.9965, ten different episodes in the top 10, hook types threat 4, reveal 2,
  violence 2, twist 1, humiliation 1, swap overlap 0.2 (against the old alt run).
- Caveat: gemini_lite hit its daily quota during the eval (3 daily_quota errors) and calls went to the
  fallback gemini_lite_b, which returned service_unavailable on 3 pair calls, so one late pair has no
  verdict (7 of 8) and eval.json lists 3 failures. The pairs that were judged mostly ran on gemini_lite.
- Reasons sometimes still say "Clip A" or "the first option" although the prompt forbids it.

#### 4) Judge diagnostic (commit ed9d741, run with `--only perturbation --out-name eval_perturb_groq_judge --profile-map judge=groq_judge`)
- Same top 10, same perturbed clips, same structured prompt. Output is outputs/duke/eval_perturb_groq_judge.json
  and .md. eval.json and eval.md were not touched (byte comparison done).

  | judge | perturbation | pairs judged | original preferred | split rate |
  |---|---|---|---|---|
  | gemini_lite (gemini-3.5-flash-lite), structured | early | 10 of 10 | 0.35 | 0.30 |
  | gemini_lite, structured | late | 7 of 8 | 0.3571 | 0.8333 |
  | groq_judge (openai/gpt-oss-120b), structured | early | 10 of 10 | 0.25 | 0.30 |
  | groq_judge, structured | late | 8 of 8 | 0.4375 | 0.875 |
  | gemini_lite, old single-question judge (eval_v1_unstructured_run2.json) | early | 10 of 10 | 0.35 | 0.50 |
  | gemini_lite, old single-question judge | late | 8 of 8 | 0.4375 | 0.875 |

- Reading: both models, and both prompts, prefer the perturbed clip more often than the original in
  every cell. The failure is therefore not specific to one judge model. The late perturbation is mostly
  undecidable (83 to 88% split), the early one is decided more often by the structured prompt (split
  0.5 down to 0.3) but still against the original. I have not tested why. Open possibilities: the
  judges favour longer clips with more context, text alone cannot show that a buried hook is worse, or
  the perturbed clips are not actually worse. Nothing was tuned to change these numbers.

#### 5) Proposals per episode, and clips that end at an episode's end
- Proposals kept per episode (81 in total, 48 episodes with at least one): ep 1-5 two each; 6: 0;
  7: 2; 8: 0; 9-16 two each; 17: 1; 18: 2; 19: 1; 20-25 two each; 26: 1; 27: 2; 28: 1; 29: 1; 30: 1;
  31-39 two each; 40: 1; 41: 2; 42: 1; 43: 1; 44: 1; 45: 1; 46: 2; 47 to 50 one each.
  (Exact per-episode counts are in outputs/duke/propose.json under meta.per_episode.)
- Why episodes 6 and 8 got none: not drops and not failures. propose.json shows raw proposals 81 equal
  to kept 81, drops {} and failures 0, so nothing was dropped or failed and the model returned zero
  for them (both sit in chunk 6 to 10). It is consistent with the content: the offline keyword
  heuristic also finds almost nothing there (episode 8 is the shortest at 78 s with no hook words at
  all, episode 6 scores 0.03 at best against 0.25 to 0.28 in episodes 5 and 7). Limits of this
  answer: per-episode raw counts before validation were not stored and I did not read the model's
  raw reply, only inferred from the totals. The prompt asked for proposals for each episode, so the
  model chose to skip them.
- Share of the top 10 that end at an episode's end (last segment ends within 0.5 s of the episode
  duration): 3 of 10 (ranks 2, 5 and 8, gaps 0.0, 0.41 and 0.0 s). 4 of 10 end within 3 s. Across all
  73 promos, 11 (15%) end at the episode's end. A clip that ends where the episode ends can end on the
  show's own built-in cliffhanger, which is why this share is worth watching against the "last 45 s"
  baseline.

#### State
- pytest 185 passed, ruff clean, no dashes, commits plain and unsigned. Nothing else was started.
- Not done, on purpose: no change to the perturbation or judge beyond what was asked, outputs/duke__alt
  was not regenerated, docs/failures.md is still for the human.
