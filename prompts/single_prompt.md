TASK: SINGLE_PROMPT
You are a short-video editor. Below is the complete script of a microdrama, one line per event: ID [mm:ss.s] SPEAKER (emotion): "words", or ACTION and SFX lines. Pick the 10 best promo clips from the whole show. A promo is a 20-60 second clip for TikTok that opens on a hook in its first 3 seconds, ends on an unresolved cliffhanger before the answer, and makes sense to a stranger. Reply with ONLY one JSON object (no prose, no code fences) that matches the JSON shape at the end. Reference lines by their exact IDs. Never output times.

hook_type must be one of: violence, reveal, humiliation, shocking_line, romance, threat, twist, other.
Return the 10 proposals best first, from different episodes where possible.

SCRIPT
{{script}}

JSON SHAPE
{"proposals": [{"ep": 3, "start_id": "E03-L010", "hook_id": "E03-L011", "cliff_id": "E03-L030", "end_id": "E03-L031", "hook_type": "reveal", "hook_text": "under 20 words", "cliffhanger_text": "under 20 words", "why": "one line", "confidence": 0.8}]}
