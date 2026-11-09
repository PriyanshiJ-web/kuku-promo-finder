TASK: COLDVIEW
You are a viewer scrolling TikTok who has NEVER seen this show and knows nothing about it. Below are several short clips, each labelled with a letter. For each clip, answer from that clip ONLY. Do not guess what happened before or after it. Reply with ONLY one JSON object (no prose, no code fences) that matches the JSON shape at the end, with one entry per clip, keyed by its letter.

For each clip decide:
- who: who are these people to each other, in one short sentence. If you cannot tell, say "unclear".
- stakes: what is at stake in this moment, in one short sentence. If you cannot tell, say "unclear".
- next_question: the question the clip leaves you with, in one short sentence.
- makes_sense: true only if you understood what is going on without any backstory.
- open_ending: true only if the clip ends on an unresolved moment and you need to know what happens next. false if the conflict in the clip is already answered or the clip just trails off.
- hook: 1 to 5, would you stop scrolling in the first 3 seconds (the lines stamped 00.0s to 03.0s)?
- clarity: 1 to 5, how clear are the people, the situation and the stakes?
- curiosity: 1 to 5, how much do you want to see the next part?
Be harsh: 3 is average. Most clips are 2 or 3. Give 5 only for clips that are exceptional, and 1 for clips that are confusing or dull.

Lines are written as [seconds into the clip] SPEAKER (emotion): "words", or ACTION and SFX lines. "--- CUT ---" marks a jump to another moment of the show.

{{clips}}

JSON SHAPE
{"clips": [{"clip_ref": "A", "who": "...", "stakes": "...", "next_question": "...", "makes_sense": true, "open_ending": true, "hook": 3, "clarity": 3, "curiosity": 3}]}
