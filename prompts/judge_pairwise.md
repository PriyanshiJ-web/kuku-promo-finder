TASK: JUDGE
You are choosing which of two short clips from a microdrama to post FIRST on TikTok to pull in NEW viewers who have never seen the show. You see only the two clips below, nothing else. Reply with ONLY one JSON object (no prose, no code fences) that matches the JSON shape at the end.

Each clip is shown in three parts so you can judge each question on its own:
- OPENING: the lines that start in the first 3 seconds.
- FULL CLIP: everything in the clip.
- LAST LINES: the lines in the final 5 seconds.

Answer three separate questions with "A" or "B", always picking one even if they are close:
1. hook: which clip has the opening (OPENING part) that would make a scrolling stranger stop?
2. ending: which clip has the ending (LAST LINES part) that leaves you needing to know what happens next, with the conflict clearly unresolved?
3. clarity: which clip makes more sense to someone who has never seen the show (FULL CLIP part)?

Then give one short reason of at most 20 words for the overall better clip, without naming the letters of the clips.

Lines are written as [seconds into the clip] SPEAKER (emotion): "words", or ACTION and SFX lines. "--- CUT ---" marks a jump to another moment of the show.

=== CLIP A ===
OPENING:
{{opening_a}}

FULL CLIP:
{{full_a}}

LAST LINES:
{{last_a}}

=== CLIP B ===
OPENING:
{{opening_b}}

FULL CLIP:
{{full_b}}

LAST LINES:
{{last_b}}

JSON SHAPE
{"hook": "A", "ending": "B", "clarity": "A", "reason": "opens on a slap and ends before the answer"}
