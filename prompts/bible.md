TASK: BIBLE
You are a story analyst preparing notes for a promo editor. Reply with ONLY one JSON object (no prose, no code fences) that matches the JSON shape at the end. Reference lines by their exact IDs as shown (for example E03-L042). Never invent an ID. Never output times.

EPISODE LOGLINES AND CONTINUITY NOTES
{{meta}}

SPEAKER TAGS (every raw tag that speaks in these episodes, with line counts and first appearance)
{{tags}}

{{material_note}}
{{material}}

WHAT TO PRODUCE
1. premise: one sentence a stranger would understand.
2. characters: one entry per real person. "canonical" is a short natural name (for example "Mara"). "aliases" must list EVERY raw speaker tag above that is this same person, copied exactly as written in the tag list. Tags often differ for one person: a title ("LADY MARA" and "MARA"), a descriptive label used before the name is revealed ("TALL STRANGER IN GREY" and later the man's real name), or a role label ("THE HARBOR CLERK"). Decide from the story context which tags are the same person. Only list tags from the tag list. If a tag is a crowd or an unnamed extra, give it its own character with a generic role. Every tag in the tag list should end up in exactly one character's aliases (the canonical name itself may also be a tag). "role" is a few words ("heroine", "villainous sister", "hidden duke"). "first_ep" is the episode number where the character first speaks or appears.
3. arcs: 3 to 6 main story arcs, each {"name": "...", "eps": [episode numbers]}.
4. reveals: the biggest reveals and twists, each {"what": "...", "line_id": "the line where it lands"}.
5. open_questions: the big questions the story raises that a viewer would want answered. Each has "question", "raised_at" (line ID where it is first raised) and "resolved_at" (line ID where it is answered, or null if the answer is not in these episodes). Aim for 6 to 15.
6. spoiler_line_ids: the few line IDs (at most 12) whose content would spoil the biggest twists if shown in a promo for a stranger.

JSON SHAPE
{"premise": "...", "characters": [{"canonical": "Mara", "aliases": ["MARA", "LADY MARA"], "role": "heroine", "first_ep": 1}], "arcs": [{"name": "...", "eps": [1, 2]}], "reveals": [{"what": "...", "line_id": "E01-L010"}], "open_questions": [{"idx": 0, "question": "...", "raised_at": "E01-L010", "resolved_at": null}], "spoiler_line_ids": ["E05-L020"]}
