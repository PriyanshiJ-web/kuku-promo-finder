"""Final cut polish and near-duplicate removal, applied by the report stage (code only, no model).

Three rules, each one targets a flaw found in docs/failures.md:
1. A clip must not open on the tail of a sentence (text starting "..." or lower case): the start
   moves forward to the first clean cut at or before the hook line.
2. A clip must not contain the line where the bible says its own question is answered: the end
   moves back to the last clean cut before that line, or the promo is flagged (gate G8) and sent
   to the bottom when it would become too short.
3. Near-duplicates the build stage let through (time overlap > 0.4 or word overlap > 0.5) are
   dropped, keeping the higher ranked one.
Scores from the cold view and the judge were computed on the unpolished clip and are kept as is.
"""

from __future__ import annotations

import re

from promo.parse.render import in_segment
from promo.schema import Bible, GateResult, Line, Promo, Show
from promo.stages.build import jaccard, make_segment, time_iou

EPS = 1e-6
MIN_LEN, MAX_LEN, HOOK_WINDOW = 20.0, 60.0, 3.0
DUP_IOU, DUP_JACCARD = 0.4, 0.5
FRAGMENT = re.compile(r'^[\s"“]*(\.\.\.|…|[a-z])')


def is_fragment(text: str) -> bool:
    return bool(FRAGMENT.match(text))


def _first_dialogue(show: Show, ep: int, a: float, b: float) -> Line | None:
    rows = [ln for ln in show.ep(ep).lines if ln.kind == "dialogue" and in_segment(ln, a, b)]
    return rows[0] if rows else None


def fix_start(p: Promo, show: Show) -> str | None:
    """Move the first segment's start past a sentence fragment, if the hook stays inside."""
    c = p.candidate
    seg = c.segments[0]
    first = _first_dialogue(show, seg.ep, seg.start, seg.end)
    hook = show.line(c.hook_id)
    if first is None or not is_fragment(first.text) or first.id == hook.id or hook.ep != seg.ep:
        return None
    ep = show.ep(seg.ep)
    ok = [x for x in ep.cuts if first.end - EPS <= x <= hook.start + EPS and x > seg.start + EPS]
    if not ok or c.duration - (min(ok) - seg.start) < MIN_LEN - EPS:
        return "fragment_start_kept"
    seg2 = make_segment(ep, min(ok), seg.end, (seg.start_id, seg.end_id))
    c.segments[0] = seg2
    return "start_after_fragment"


def fix_end(p: Promo, show: Show, bible: Bible) -> str | None:
    """Cut before the line that answers the promo's own question, when that line is inside."""
    c = p.candidate
    qs = {q.idx: q for q in bible.open_questions}
    q = qs.get(c.question_ref) if c.question_ref is not None else None
    if q is None or not q.resolved_at:
        return None
    ans = show.line(q.resolved_at)
    for i, seg in enumerate(c.segments):
        if ans.ep != seg.ep or not (seg.start - 0.05 <= ans.start < seg.end):
            continue
        ep = show.ep(seg.ep)
        before = [x for x in ep.cuts if seg.start + 3.0 <= x <= ans.start + EPS]
        new = max(before) if before else None
        cut_total = c.duration - (seg.end - new) if new else 0.0
        later = c.segments[i + 1 :]
        if new is None or later or cut_total < MIN_LEN - EPS:
            why = "the line that answers its question is inside"
            p.gates.append(GateResult(gate="G8", passed=False, hard=False, reason=why))
            return "answer_inside_flagged"
        c.segments[i] = make_segment(ep, seg.start, new, (seg.start_id, seg.end_id))
        return "end_before_answer"
    return None


def polish(promos: list[Promo], show: Show, bible: Bible) -> tuple[list[Promo], dict]:
    stats = {"start_after_fragment": 0, "fragment_start_kept": 0, "end_before_answer": 0,
             "answer_inside_flagged": 0, "near_duplicates_dropped": 0}  # fmt: skip
    for p in promos:
        for note in (fix_start(p, show), fix_end(p, show, bible)):
            if note:
                stats[note] += 1
                p.candidate.notes = [*p.candidate.notes, note]
        c = p.candidate
        c.duration = round(sum(s.end - s.start for s in c.segments), 2)
    kept: list[Promo] = []
    for p in promos:
        dup = any(
            time_iou(p.candidate, k.candidate, show) > DUP_IOU
            or jaccard(p.candidate, k.candidate, show) > DUP_JACCARD
            for k in kept
        )
        if dup:
            stats["near_duplicates_dropped"] += 1
        else:
            kept.append(p)
    # promos whose answer is inside the clip go below everything that is not flagged
    kept.sort(key=lambda p: any(g.gate == "G8" for g in p.gates))
    for i, p in enumerate(kept, 1):
        p.rank = i
    return kept, stats
