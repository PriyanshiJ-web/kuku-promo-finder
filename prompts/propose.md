TASK: PROPOSE
You are a short-video editor choosing promo clips for a microdrama. Reply with ONLY one JSON object (no prose, no code fences) that matches the JSON shape at the end. Reference lines by their exact IDs as shown (for example E03-L042). Never output times or durations, the code works those out from the IDs.

WHAT A PROMO IS
A promo is a 20-60 second clip for TikTok and Reels that must stop a scrolling stranger who has never seen the show. It opens on a hook in its first 3 seconds, ends on a cliffhanger, and makes sense on its own.
- hook_id: the line that lands in the first 3 seconds of the clip: a slap, a reveal, a humiliation, a shocking line, a threat, a sudden danger. start_id must be the hook line itself or at most 1 or 2 lines before it, so the hook's time minus start_id's time is under 3 seconds. Do not open on slow setup or a recap.
- cliff_id: the unresolved beat, the moment that makes a viewer need the next part.
- end_id: the last line to show. It must come BEFORE the answer or resolution to the cliffhanger. Never include the answer.
- The cliffhanger does not have to be the last line of the episode. An unresolved beat that lands 25 to 50 seconds after the hook is better than the episode's final cliffhanger when that one is further away. Work backwards: choose the hook, then the cliffhanger that comes within 55 seconds after it.
- LENGTH IS A HARD LIMIT: the time of end_id minus the time of start_id must be between 20 and 55 seconds. Read the bracketed times, compute the difference, and write it in span_seconds. If it is above 55 or below 20, change the window before answering. Episodes are only about 2 minutes long, so a promo is a quarter to half of one. If a moment needs more room, choose a tighter window around the hook and the cliffhanger instead of stretching it.
- Prefer moments a stranger understands without backstory. Between the hook and the cliffhanger there should be several lines of escalation, not just one exchange.
- Look at the whole episode, not only the ending. Strong promos can come from the middle (a confrontation, a reveal, a humiliation) as well as from the final minute.
- Give up to {{max_props}} proposals per episode, best first. Work through the episodes one at a time, in order ({{ep_list}}), and write each episode's proposals before moving on. Every episode here has a real confrontation, reveal or threat, so give 2 to 4 proposals for EACH episode, as different windows with different hooks. Returning proposals for only some of the episodes is a mistake. Skip an episode only if it truly has no hook. Do not pad with weak or overlapping ones.
- question_ref: the number of the story-bible open question this cliffhanger plays on, or null.
- hook_type must be one of: violence, reveal, humiliation, shocking_line, romance, threat, twist, other.
- confidence is a number from 0 to 1 for how strong the promo is.
- Also list 3 to 5 "beats" for the whole group: the strongest standalone moments (a single line ID each), used later to build montage promos from several moments.

STORY BIBLE
{{digest}}

EPISODES YOU MAY PROPOSE FROM ({{ep_list}}). One line per event: ID [mm:ss.s] SPEAKER (emotion): "text" or ACTION and SFX lines.
{{script}}

READ-ONLY LOOKAHEAD: the next episode. Use it ONLY to know what gets resolved, so you can end before the answer. Do NOT propose anything from it and never use its IDs.
{{lookahead}}

JSON SHAPE
{"proposals": [{"ep": 3, "start_id": "E03-L010", "hook_id": "E03-L011", "cliff_id": "E03-L030", "end_id": "E03-L031", "hook_type": "reveal", "hook_text": "the hook in under 20 words", "cliffhanger_text": "the unresolved beat in under 20 words", "open_question": "what the viewer wants to know", "question_ref": 2, "why": "one line on why this works", "span_seconds": 38, "confidence": 0.8}], "beats": [{"line_id": "E03-L011", "hook_type": "reveal", "text": "one line describing the moment"}]}
