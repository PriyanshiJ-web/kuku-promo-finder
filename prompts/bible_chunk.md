TASK: BIBLE_CHUNK
You are a story analyst. Summarise this group of consecutive episodes so a later step can merge several summaries into one story bible. Reply with ONLY one JSON object (no prose, no code fences). Reference lines by their exact IDs as shown. Never invent an ID. Never output times.

EPISODE LOGLINES AND CONTINUITY NOTES
{{meta}}

SPEAKER TAGS IN THESE EPISODES
{{tags}}

SCRIPT (ID [mm:ss.s] SPEAKER: text)
{{script}}

WHAT TO PRODUCE
- summary: five to eight sentences of what happens.
- characters: one entry per real person with "name", "tags" (every raw speaker tag above that is this person, copied exactly) and "role". Decide from context which descriptive tags are the same person.
- reveals: the biggest reveals, each {"what": "...", "line_id": "..."}.
- open_questions: each {"question": "...", "raised_at": "line ID", "resolved_at": "line ID or null"}.
- spoiler_candidates: up to 6 line IDs that would spoil a major twist.

JSON SHAPE
{"summary": "...", "characters": [{"name": "Mara", "tags": ["MARA", "LADY MARA"], "role": "heroine"}], "reveals": [{"what": "...", "line_id": "E01-L010"}], "open_questions": [{"question": "...", "raised_at": "E01-L010", "resolved_at": null}], "spoiler_candidates": ["E02-L030"]}
