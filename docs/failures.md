# Where the tool picks badly

Source: outputs/duke/promos.json after the structured-judge rerun (73 promos), the bible, and the
cold-view ratings. Every item names the promo id (the 12 character `promo_id` in promos.csv), its
rank, the episode and times, and the stage at fault. No script text is quoted.

How these were found: with code checks on the output files and the tool's own ratings, not by
watching or reading the clips. Treat each item as a flagged pick that a human editor should confirm.
The checks only see what the bible and the cold view record, so they find fewer problems than a
reader would. The top 10 came out clean on the automated checks below, the failures sit lower in the
list (that is partly the tool working: the gates and scores pushed them down), and the honest
reading is that the checks are weak where it matters most, at the top.

## 1. Hook is not a real hook

- Four ranked promos got a cold-view hook rating of 2 (the lowest given): rank 67 `95d5cd992225`
  (ep 37, 93.5 to 116.5 s, humiliation), rank 68 `e0ceb1642a24` (ep 14, 47.0 to 87.6 s, shocking
  line), rank 70 `1839f0c25811` (ep 23, two segments) and rank 72 `428aadd46133` (ep 14,
  5.5 to 29.5 s, reveal). In each case the proposer labelled a hook that the cold viewer did not
  find stopping. 23 more promos got a 3.
- Two promos were proposed with the hook type "other" (ranks 21 and 33), meaning the proposer could
  not name what the hook is.
- Stage at fault: propose. The 3 second rule (gate G3) only checks that the hook line is in the first
  3 seconds, not that it is a hook, so a weak line passes every code gate. The cold view and the
  ranking do demote these (all four sit in the bottom 7), so the failure is caught late, not never.

## 2. Cliffhanger resolves too early (the answer is inside the clip)

- Three promos contain the line where the bible says their own question is answered: rank 38
  `169dd44e4863` (ep 23, 76.0 to 131.5 s), rank 70 `1839f0c25811` (ep 23, 1.8 to 31.5 s and
  60.8 to 90.5 s) and rank 73 `4030d245e0cd` (ep 20 and ep 23, 28.2 to 39.1 s and 62.0 to 88.0 s).
  All three have a question reference to the same bible question (number 1).
- The cold view still said all three end on an open ending (gate G4 passed). It cannot know the
  answer, it only sees the clip text.
- Stage at fault: propose chose an end after the answer, and build kept it. Nothing between the two
  checks "does the clip contain the answer", except this after-the-fact comparison with the bible.
  The bible only holds 5 open questions for the whole show, so this check covers very little of the
  show, and a cliffhanger that is answered early but is not one of the five would not be caught.

## 3. Makes no sense without backstory

- Twelve ranked promos got a cold-view clarity rating of 3 (none lower, gate G5 only needs the viewer
  to name who and the stakes). The weakest are those rated 3 on hook, clarity and curiosity together:
  rank 44 `2c8c431df710` (ep 5, 51.8 to 105.5 s, a 53.7 s clip), ranks 48, 54, 56, 65, 66 and 71.
- Rank 17 `22f8deb0935c` is a two-episode montage whose first segment is only 5.4 s (ep 1, 37.7 to
  43.1 s) before a jump to ep 3 (130.4 to 145.8 s). It got clarity 3, the lowest in the top 25.
- A larger risk I cannot measure here: rank 3 `b34e2848b8a7` stitches three episodes that are far
  apart (ep 9, ep 39 and ep 42, segments of 11.7, 9.8 and 15.4 s). The cold view rated its clarity 4,
  but whether a stranger follows a splice across episodes 9, 39 and 42 is the open question.
- Stage at fault: montage (it joins distant scenes) and the cold view (it is a model reading text,
  so it can rate clarity 4 for a splice that needs the show's context).

## 4. Near-duplicate of a better promo (same moment, different cut)

- Ranks 14 `3fe841735d5c` and 29 `64917f0fbfb9` are both two-segment clips from episode 1 with a
  time overlap (IoU) of 0.44 and a dialogue word overlap (Jaccard) of 0.60. The build step only
  drops a duplicate above IoU 0.5 or Jaccard 0.6, so both were kept.
- Ranks 1 `b5f6df132b5c` and 12 `7164fe7b21a5` show the same episode 3 moment (IoU 0.28,
  Jaccard 0.48), one as a single 31.8 s clip and one as a two-segment clip. Ranks 3 and 34 overlap by
  Jaccard 0.56, and ranks 40 and 73 and ranks 70 and 73 repeat episode 20 and 23 material.
- Stage at fault: build (dedupe thresholds, and multi-segment clips dilute the overlap measure) and
  rank (the diversity step only lowers a near-copy, it does not remove it). Before the variant fix, the
  same moment also showed up twice as a tight and a loose cut (rank 1 and rank 7).

## 5. Spoils a major twist for a new viewer

- Seven ranked promos fail the spoiler gate G7, meaning they show a line the bible marked as
  spoiling a big twist: ranks 38, 42, 45, 58, 69, 70 and 73, from episodes 20, 23, 36, 43 and 45.
  Rank 58 `ab4379b6347f` (ep 45, 74.0 to 113.0 s) also fails the honest-promise gate G6.
- G7 is only a soft penalty of 0.15, so these stay in the list instead of being removed, and the
  bible lists only 7 spoiler lines for the entire 50 episodes (in episodes 23, 27, 36, 42, 43, 45).
  A clip from episode 36 or 42 can still reveal something the bible did not list. Rank 4 `d6f80d362cea`
  (ep 36, 151.5 to 197.0 s) and ranks 3 and 13 (episode 42) sit in episodes the bible says hold spoilers
  and passed G7 only because the exact spoiler line is not inside the clip.
- Stage at fault: bible (a short spoiler list) and gates (soft penalty, no removal).

## Also worth knowing

- Honest promise (G6): 26 of the 73 ranked promos have no link to a resolved question, including
  ranks 9, 14, 15 and 16 in the top 16, because the proposer left `question_ref` empty. A soft
  penalty of 0.1 cannot demote them. Stage at fault: propose and the weight of G6.
- Episodes 6 and 8 produced no proposals at all, 3 of the top 10 end exactly at an episode's end
  (the show's built-in cliffhanger), and the perturbation check shows the judge cannot tell a buried
  hook or an answer-included clip from the original (original preferred 0.25 to 0.44). See
  docs/handoff-fixes.md for these numbers.

## Update after the cut polish step (code only, no new model calls)

- Mid-sentence starts: 3 of the top 30 opened on the tail of a sentence. The polish step moved 2
  (ranks 1 and 23) to open on the hook line. The third is not fixed.
- Answer inside the clip: 2 of the 3 promos were cut before the answer line, 1 is flagged G8 and
  sits last. Only the bible's 5 questions are known, so other early answers are still missed.
- Near-duplicates: stricter thresholds dropped 2 promos (73 became 71).
- Not changed: the cold-view scores and the judge ranking were computed before polishing.
  The top 10 ids are the same, so the eval in eval.md still describes them, but 2 of those clips
  now start a few seconds later than the clips the judge saw.
