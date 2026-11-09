## What this tool does

A promo is a 20 to 60 second clip taken from a show and posted on Instagram, TikTok or YouTube to
pull in new viewers. A good one opens on a **hook** in the first 3 seconds (a slap, a reveal, a
shocking line), ends on an unresolved **cliffhanger**, and makes sense to someone who has never
seen the show. This tool reads the screenplay of every episode and returns the best promos,
ranked, with the episode and start and end time of every segment, the hook, the cliffhanger and
one line on why it works. It works from the script text only: it cannot see picture, hear music or
judge acting.

## The steps, and who does each one

| Step | Done by | What it does |
|---|---|---|
| 1. Parse | Code | Reads the timestamped script. Finds the **safe cut points**: moments that fall inside no spoken line. |
| 2. Story bible | AI, 1 call | Summarises the show: who the characters are (merging labels like "LADY IN GOLD" into one name), the open questions, and the twist lines that would be spoilers. |
| 3. Propose | AI, a few calls | Reads the episodes in chunks and suggests moments. It answers with **line IDs only**, never times. A separate call suggests multi-segment montages. |
| 4. Build | Code | Turns line IDs into seconds. Snaps every start and end to a safe cut, keeps the hook within 3 s, fixes the length to 20 to 60 s, removes duplicates. |
| 5. Checks | Code | Hard checks (length, clean cuts, hook within 3 s, segments in order) and soft checks (the show answers the question later, no spoiler line inside). |
| 6. Cold viewer | AI | A model that sees **only the clip text** says who the people are, what is at stake and what it wants to know next, and scores hook, clarity and curiosity from 1 to 5. |
| 7. Rank | AI + code | Combines the scores, then compares the best 24 clips pair by pair, in both orders, and fits a ranking. A final step keeps the top list varied. |
| 8. Polish | Code | A clip that opens on the tail of a sentence starts on the hook instead; a clip that contains the line answering its own question is cut before it; near-duplicates are dropped. |
| 9. Report | Code | Writes `promos.csv`, `promos.json` and `report.md`. |

Why the split: models are good at judging drama and bad at arithmetic, so they only choose
moments. Everything that has a rule (length, hook window, clean cuts) is code, so it cannot be
wrong by a few seconds.

## How promos are ranked

1. **Score for each clip:** 40% hook, 25% curiosity, 20% clarity (from the cold viewer), 15% the
   proposing model's confidence, minus small penalties for a spoiler line or a question the show
   never answers, plus a small bonus for episodes 1 to 10 (the free episodes).
2. **Head to head:** the top 24 are compared in pairs by a judge model, each pair twice with the
   order swapped. If the two answers disagree it counts as a draw, which cancels position bias.
3. **Final order:** the pair results are turned into a ranking (Bradley-Terry), mixed with the
   score, and re-ordered so the top of the list is not ten copies of one moment.

## Where a human stays in the loop

The Promos tab is built for an editor: open a promo, read the transcript, then approve, trim the
start or end, or reject. Decisions are saved to `review.csv`. The tool finds candidates and the
editor makes the final call.

## What it costs, and how it scales

A 50-episode show (about 100 minutes of script) takes about 150 model calls and 400,000 tokens:
roughly 12 minutes and $0 on free tiers, about $0.20 to $0.50 at paid prices. A new show is one
command, and any model that speaks the OpenAI API can be swapped in with one config block. A run
with no model at all (**offline mode**) uses keyword rules and finishes in seconds; it is the
fallback when an API is down, not a replacement.
