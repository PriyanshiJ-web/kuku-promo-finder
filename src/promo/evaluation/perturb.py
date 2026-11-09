"""Perturbations of a promo: a hook buried 8 s earlier, or a cut after the answer."""

from __future__ import annotations

from promo.parse.cuts import cut_at_or_after, cut_at_or_before
from promo.schema import Candidate, Show
from promo.stages.build import make_candidate, make_segment

EARLY_BY = 8.0
LATE_DIALOGUE_LINES = 2


def _rebuild(
    c: Candidate, show: Show, spans: list[tuple[int, float, float]], tag: str
) -> Candidate:
    segs = [make_segment(show.ep(e), a, b, (c.hook_id, c.cliff_id)) for e, a, b in spans]
    fields = c.model_dump()
    return make_candidate(c.kind, c.variant, segs, fields, c.source, [*c.notes, tag], show)


def early(c: Candidate, show: Show) -> Candidate | None:
    """Start of the first segment moved 8 s earlier (snapped), which buries the hook."""
    first = c.segments[0]
    ep = show.ep(first.ep)
    a = cut_at_or_before(ep, max(0.0, first.start - EARLY_BY))
    if a >= first.start - 1e-6:
        return None
    spans = [(s.ep, s.start, s.end) for s in c.segments]
    spans[0] = (first.ep, a, first.end)
    return _rebuild(c, show, spans, "perturb_early")


def late(c: Candidate, show: Show) -> Candidate | None:
    """End of the last segment extended past the next two dialogue lines (snapped)."""
    last = c.segments[-1]
    ep = show.ep(last.ep)
    nxt = [ln for ln in ep.lines if ln.kind == "dialogue" and ln.start >= last.end - 1e-6]
    target = nxt[LATE_DIALOGUE_LINES - 1].end if len(nxt) >= LATE_DIALOGUE_LINES else ep.duration
    b = cut_at_or_after(ep, target)
    if b <= last.end + 1e-6:
        return None
    spans = [(s.ep, s.start, s.end) for s in c.segments]
    spans[-1] = (last.ep, last.start, b)
    return _rebuild(c, show, spans, "perturb_late")
