TASK: MONTAGE
You are a short-video editor building montage promos for a microdrama: a 20-60 second clip joined from 2 or 3 short moments, from one episode or several. Reply with ONLY one JSON object (no prose, no code fences) that matches the JSON shape at the end. Reference lines by their exact IDs as shown. Never output times or durations.

HOW A MONTAGE PROMO WORKS
- It runs setup, then escalation, then a cliffhanger.
- The first segment must open on a hook that lands in the first 3 seconds.
- Each segment is a short run of consecutive lines inside ONE episode: start_id and end_id are both line IDs from the same episode, start_id first. Segments can only use lines shown below.
- Segments run in story order unless you have a strong reason.
- hook_id is a line inside the FIRST segment. cliff_id is a line inside the LAST segment and must be an unresolved moment. The last segment must end before the answer.
- The whole promo must make sense to a stranger who has never seen the show: it should be clear who the people are and what is at stake.
- hook_type must be one of: violence, reveal, humiliation, shocking_line, romance, threat, twist, other.
- question_ref: the number of the story-bible open question the cliffhanger plays on, or null.
- Give 5 to 10 montages, best first.

STORY BIBLE
{{digest}}

CANDIDATE MOMENTS. Each block shows a key moment (marked >>) with the lines around it. ID [mm:ss.s] SPEAKER: text.
{{beats}}

JSON SHAPE
{"montages": [{"segments": [{"start_id": "E01-L010", "end_id": "E01-L016"}, {"start_id": "E04-L020", "end_id": "E04-L026"}], "hook_id": "E01-L011", "cliff_id": "E04-L025", "hook_type": "reveal", "hook_text": "under 20 words", "cliffhanger_text": "under 20 words", "open_question": "what the viewer wants to know", "question_ref": 1, "why": "one line", "confidence": 0.7}]}
