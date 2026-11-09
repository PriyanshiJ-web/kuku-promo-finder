"""build stage (code only): turn proposals into candidate clips (spec section 5, build)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from promo.parse.cuts import cut_at_or_after, cut_at_or_before
from promo.parse.render import clip_lines
from promo.runner import Ctx, sha_bytes
from promo.schema import Candidate, Episode, Line, MontageProposal, Proposal, Segment, Show

NAME = "build"
EPS = 1e-6
FIRST_SEG_TARGET = (
    30.0  # when a clip is too long, the first of its two segments runs about this long
)


@dataclass(frozen=True)
class BuildParams:
    min_len: float = 20.0
    max_len: float = 60.0
    hook_window: float = 3.0
    min_segment: float = 3.0

    @classmethod
    def from_ctx(cls, ctx: Ctx) -> BuildParams:
        p = ctx.params
        return cls(p.min_len, p.max_len, p.hook_window, p.min_segment)


def has_dialogue(ep: Episode, a: float, b: float, tol: float = 0.1) -> bool:
    return any(ln.kind == "dialogue" and ln.end > a + tol and ln.start < b - tol for ln in ep.lines)


def seg_ids(ep: Episode, a: float, b: float, fallback: tuple[str, str]) -> tuple[str, str]:
    rows = [ln for ln in ep.lines if ln.kind != "camera" and a - 0.05 <= ln.start < b]
    return (rows[0].id, rows[-1].id) if rows else fallback


def make_segment(ep: Episode, a: float, b: float, fallback: tuple[str, str]) -> Segment:
    s, e = seg_ids(ep, a, b, fallback)
    return Segment(ep=ep.num, start=round(a, 2), end=round(b, 2), start_id=s, end_id=e)


def snap_start(ep: Episode, hook: Line, start_line: Line, prm: BuildParams):
    """Earliest cut in the hook window at or after start_id, else the latest cut in the window."""
    lo = max(0.0, hook.start - prm.hook_window)
    window = [c for c in ep.cuts if lo - EPS <= c <= hook.start + EPS]
    after = [c for c in window if c >= start_line.start - EPS]
    if after:
        return min(after), []
    if window:
        return max(window), []
    return hook.start, ["start_not_on_shot_boundary"]


def extend_end(ep: Episode, start: float, end: float, prm: BuildParams) -> float:
    """Walk to later cuts, adding only intervals that hold no dialogue, up to max_len."""
    for c in ep.cuts:
        if c <= end + EPS:
            continue
        if c - start > prm.max_len + EPS or has_dialogue(ep, end, c):
            break
        end = c
        if end - start >= prm.min_len - EPS:
            break
    return end


def split_long(ep: Episode, start: float, end: float, hook: Line, cliff: Line, prm: BuildParams):
    """Two segments: [start, c1] then [c2, end] with the cliff inside the second."""
    firsts = [c for c in ep.cuts if start + EPS < c <= start + FIRST_SEG_TARGET + EPS]
    if not firsts:
        return None
    c1 = max(firsts)
    if hook.start >= c1 - EPS:
        return None
    budget = prm.max_len - (c1 - start)
    ok = [
        c
        for c in ep.cuts
        if c >= c1 - EPS
        and prm.min_segment - EPS <= end - c <= budget + EPS
        and cliff.start >= c - EPS
        and cliff.end <= end + EPS
    ]
    if not ok:
        return None
    c2 = min(ok)
    if (c1 - start) + (end - c2) < prm.min_len - EPS:
        return None
    return [(start, c1), (c2, end)]


def make_candidate(kind, variant, segments, fields, source, notes, show) -> Candidate:
    key = json.dumps([(s.ep, s.start, s.end) for s in segments])
    return Candidate(
        id=hashlib.sha1(key.encode()).hexdigest()[:12],
        kind=kind,
        variant=variant,
        segments=segments,
        hook_id=fields["hook_id"],
        cliff_id=fields["cliff_id"],
        duration=round(sum(s.end - s.start for s in segments), 2),
        hook_type=fields["hook_type"],
        hook_text=fields.get("hook_text", ""),
        cliffhanger_text=fields.get("cliffhanger_text", ""),
        open_question=fields.get("open_question", ""),
        question_ref=fields.get("question_ref"),
        why=fields.get("why", ""),
        confidence=fields.get("confidence", 0.5),
        source=source,
        notes=notes,
    )


def build_single(p: Proposal, show: Show, prm: BuildParams) -> tuple[list[Candidate], str | None]:
    ep = show.ep(p.ep)
    hook, cliff = show.line(p.hook_id), show.line(p.cliff_id)
    start_line, end_line = show.line(p.start_id), show.line(p.end_id)
    start, notes = snap_start(ep, hook, start_line, prm)
    end = cut_at_or_after(ep, max(cliff.end, end_line.end))
    if end <= start + EPS:
        return [], "bad_span"
    if end - start < prm.min_len - EPS:
        end = extend_end(ep, start, end, prm)
        if end - start < prm.min_len - EPS:
            return [], "too_short"
        notes = [*notes, "extended_end"]
    spans = [(start, end)]
    if end - start > prm.max_len + EPS:
        spans = split_long(ep, start, end, hook, cliff, prm)
        if spans is None:
            return [], "too_long"
        notes = [*notes, "split_in_two"]
    fb = (p.start_id, p.end_id)
    fields = p.model_dump()
    source = f"propose:{p.hook_id}"
    tight = make_candidate(
        "single",
        "tight",
        [make_segment(ep, a, b, fb) for a, b in spans],
        fields,
        source,
        notes,
        show,
    )
    out = [tight]
    la, lb = spans[0][0], spans[-1][1]
    prev = [c for c in ep.cuts if c < la - EPS]
    if prev and hook.start - prev[-1] <= prm.hook_window + EPS:
        la = prev[-1]
    nxt = [c for c in ep.cuts if c > lb + EPS]
    if nxt and not has_dialogue(ep, lb, nxt[0]):
        lb = nxt[0]
    total = sum(b - a for a, b in spans) + (spans[0][0] - la) + (lb - spans[-1][1])
    if (la, lb) != (spans[0][0], spans[-1][1]) and total <= prm.max_len + EPS:
        loose_spans = [(la, spans[0][1]), *spans[1:]] if len(spans) > 1 else [(la, lb)]
        if len(spans) > 1:
            loose_spans[-1] = (loose_spans[-1][0], lb)
        out.append(
            make_candidate(
                "single",
                "loose",
                [make_segment(ep, a, b, fb) for a, b in loose_spans],
                fields,
                source,
                [*notes, "loose"],
                show,
            )
        )
    return out, None


def build_montage(m: MontageProposal, show: Show, prm: BuildParams, i: int):
    hook = show.line(m.hook_id)
    spans: list[tuple[int, float, float, tuple[str, str]]] = []
    for k, s in enumerate(m.segments):
        a_line, b_line = show.line(s.start_id), show.line(s.end_id)
        ep = show.ep(a_line.ep)
        a = cut_at_or_before(ep, a_line.start)
        if k == 0 and hook.ep == ep.num and hook.start - a > prm.hook_window + EPS:
            a, _ = snap_start(ep, hook, a_line, prm)
        b = cut_at_or_after(ep, b_line.end)
        spans.append((ep.num, a, b, (s.start_id, s.end_id)))
    for (e1, a1, b1, _), (e2, a2, _b2, _) in zip(spans, spans[1:], strict=False):
        if (e2, a2) < (e1, a1) or (e2 == e1 and a2 < b1 - EPS):
            return None, "not_chronological"
    if any(b - a < prm.min_segment - EPS for _, a, b, _ in spans):
        return None, "short_segment"
    total = sum(b - a for _, a, b, _ in spans)
    if total < prm.min_len - EPS:
        return None, "too_short"
    if total > prm.max_len + EPS:
        return None, "too_long"
    segments = [make_segment(show.ep(e), a, b, fb) for e, a, b, fb in spans]
    return make_candidate(
        "montage", "tight", segments, m.model_dump(), f"montage:{i}", [], show
    ), None


# ---- dedupe ------------------------------------------------------------------------------


def _global(c: Candidate, show: Show) -> list[tuple[float, float]]:
    out = []
    for s in c.segments:
        off = show.ep(s.ep).offset
        out.append((off + s.start, off + s.end))
    return sorted(out)


def _union(iv: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for a, b in sorted(iv):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def time_iou(a: Candidate, b: Candidate, show: Show) -> float:
    ua, ub = _union(_global(a, show)), _union(_global(b, show))
    inter = sum(max(0.0, min(x2, y2) - max(x1, y1)) for x1, x2 in ua for y1, y2 in ub)
    union = sum(y - x for x, y in ua) + sum(y - x for x, y in ub) - inter
    return inter / union if union > 0 else 0.0


def words(c: Candidate, show: Show) -> set[str]:
    out: set[str] = set()
    for ln, _ in clip_lines(c, show):
        if ln.kind == "dialogue":
            out |= set(re.findall(r"[a-z']+", ln.text.lower()))
    return out


def jaccard(a: Candidate, b: Candidate, show: Show) -> float:
    wa, wb = words(a, show), words(b, show)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def dedupe(cands: list[Candidate], show: Show) -> tuple[list[Candidate], int]:
    """Greedy: best confidence first, tight before loose. Variants of one source are kept."""
    order = sorted(cands, key=lambda c: (-c.confidence, c.variant != "tight", c.id))
    kept: list[Candidate] = []
    for c in order:
        dup = any(
            k.source != c.source and (time_iou(c, k, show) > 0.5 or jaccard(c, k, show) > 0.6)
            for k in kept
        )
        if not dup:
            kept.append(c)
    keep_ids = {id(c) for c in kept}
    return [c for c in cands if id(c) in keep_ids], len(cands) - len(kept)


# ---- stage -------------------------------------------------------------------------------


def inputs(ctx: Ctx) -> dict:
    return {
        "params": BuildParams.from_ctx(ctx).__dict__,
        "code": sha_bytes(Path(__file__).read_bytes()),
    }


async def run(ctx: Ctx) -> dict:
    prm = BuildParams.from_ctx(ctx)
    show = ctx.full_show
    drops: Counter[str] = Counter()
    cands: list[Candidate] = []
    for item in ctx.artifacts["propose"]["items"]:
        out, why = build_single(Proposal.model_validate(item), show, prm)
        cands += out
        if why:
            drops[why] += 1
    for i, item in enumerate(ctx.artifacts["montage"]["items"]):
        cand, why = build_montage(MontageProposal.model_validate(item), show, prm, i)
        if cand:
            cands.append(cand)
        else:
            drops[why] += 1
    built = len(cands)
    cands, ndup = dedupe(cands, show)
    if ndup:
        drops["duplicate"] += ndup
    hist = Counter(int(c.duration // 5) * 5 for c in cands)
    meta = {
        "built": built,
        "kept": len(cands),
        "drops": dict(drops),
        "by_kind_variant": dict(Counter(f"{c.kind}/{c.variant}" for c in cands)),
        "duration_hist_5s": {f"{k}-{k + 5}": hist[k] for k in sorted(hist)},
    }
    return {"meta": meta, "items": [c.model_dump() for c in cands], "failures": []}


def load(ctx: Ctx, artifact: dict) -> None:
    return None
