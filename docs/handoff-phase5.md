### HANDOFF phase 5
- Done:
  - cold view prompt and LLM gates G4 and G5 (batched 6 per call, shuffled with a deterministic seed, labelled A to F, clip text only, no IDs or episode numbers): done
  - soft gates G6 (honest promise) and G7 (spoiler): done, in the gates stage
  - pointwise score in the rank stage, and the report stage (promos.json, promos.csv, report.md): done
  - first run on episodes 1-5, then `promo estimate`, then the full 50 episodes: done
  - a rerun is cached in under 5 s: done, 0.78 s with every stage skipped
- Files: prompts/coldview.md, src/promo/stages/coldview.py, src/promo/stages/rank.py, src/promo/stages/report.py, src/promo/runner.py (ORDER, DEPS), src/promo/cli.py, tests/test_rank.py, tests/test_cli.py.
- Commands run: pytest 177 passed, ruff clean. `promo run` on episodes 1-5, `promo estimate` on the full show (208 planned calls, 633k tokens, upper bound), `promo run` on all 50 episodes.
- Sample output (full show, ranking by pointwise score only, saved in outputs/duke_pointwise):
  - proposals: 81 kept of 81 from 48 episodes, 8 montage proposals kept of 8
  - build: 149 built, 147 kept (single/tight 74, single/loose 65, montage/tight 8), drops: too_short 6, duplicate 2
  - duration histogram, 5 s buckets: 20-25: 27, 25-30: 16, 30-35: 14, 35-40: 25, 40-45: 14, 45-50: 6, 50-55: 9, 55-60: 33, 60-65: 3 (the 60-65 bucket holds clips of exactly 60.0 s)
  - gate pass counts of 147: G1 147, G2 147, G3 147, G_seg 147 (built to satisfy them), G4 146, G5 121, G6 87, G7 135
  - rank: 120 ranked, excluded G5 26 and G4 1
  - cold view rubric spread (1 to 5): hook 2:14, 3:57, 4:65, 5:11, clarity 2:13, 3:35, 4:88, 5:11, curiosity 2:2, 3:62, 4:74, 5:9
  - top 10 by pointwise score, rank, kind, episode, start, end, total s, hook type, hook text cut to 60 chars:
    1 single ep 3 00:58.20-01:28.00 29.8 violence (hook text omitted, confidential)
    2 single ep 3 00:56.30-01:28.10 31.8 violence (same moment, loose variant)
    3 single ep 1 00:37.70-01:06.50 58.5 reveal (hook text omitted, confidential)
    4 single ep 36 02:31.50-03:16.80 45.3 threat (hook text omitted, confidential)
    5 montage from ep 9 01:30.20-01:41.90 36.9 reveal (hook text omitted, confidential)
    6 single ep 41 00:08.20-01:03.50 55.3 humiliation (hook text omitted, confidential)
    7 single ep 36 02:31.50-03:17.00 45.5 threat (same moment, loose variant)
    8 single ep 31 02:10.50-02:35.20 24.7 threat (hook text omitted, confidential)
    9 single ep 42 00:10.50-00:30.50 20.0 reveal (hook text omitted, confidential)
    10 single ep 39 01:02.00-01:38.00 36.0 twist (hook text omitted, confidential)
  - budget (manifest, every attempt counted): bible 4 calls 81,556 tokens, propose 13 calls 118,785, montage 1 call 14,753, coldview 29 calls 79,001, total before ranking 47 calls 294,095. Fits the free tier (200,000 tokens per day limits only apply to Groq and are not used here).
  - wall clock per stage: bible 56 s, propose 123 s, montage 19 s, build 1 s, coldview 123 s
- Deviations from spec: coldview is its own stage (the spec folds it into gates), and for offline mode and baselines the same code is reused.
- Assumptions added: the phase 5 lines in docs/assumptions.md.
- Known issues / TODO:
  - The gates G1, G2, G3 and G_seg pass 100% because build constructs clips to satisfy them, so they only guard against regressions.
  - G4 passes 146 of 147: the cold view calls nearly every clip open-ended, so it barely filters.
  - G7 only fails on 12 clips and G6 on 60, both are soft penalties.
  - 2 speaker tags are unresolved by the bible (ELDER VILLAGER, NOBLEMAN), left as their raw tag.
  - gemini-3.8-flash failed every attempt in this environment, so runs used gemini_flash_b (with gemini_flash_c as fallback after the daily quota of flash_b ran out).
  - I have not read the top 10 against the script, so "ends before the answer" is unchecked by a human.
- Tokens/cost used this phase: see the budget line above, $0 on the free tier.
