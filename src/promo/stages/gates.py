"""gates stage: hard code gates G1, G2, G3, G_seg and soft gates G6, G7 (spec section 5)."""

from __future__ import annotations

from collections import Counter

from promo.parse.render import clip_lines
from promo.runner import Ctx
from promo.schema import Bible, Candidate, GateResult, Show
from promo.stages.build import BuildParams

NAME = "gates"
EPS = 1e-6
G6_PENALTY, G7_PENALTY = 0.1, 0.15


def code_gates(c: Candidate, show: Show, prm: BuildParams) -> list[GateResult]:
    out: list[GateResult] = []
    ok = prm.min_len - EPS <= c.duration <= prm.max_len + EPS
    why = "" if ok else f"length {c.duration:.1f}s outside [{prm.min_len:g}, {prm.max_len:g}]"
    out.append(GateResult(gate="G1", passed=ok, hard=True, reason=why))
    bad = []
    for s in c.segments:
        cuts = show.ep(s.ep).cuts
        for t in (s.start, s.end):
            if not any(abs(t - x) <= 0.011 for x in cuts):
                bad.append(f"E{s.ep:02d}@{t:.1f}")
    why = "" if not bad else "boundary not on a cut: " + ", ".join(bad)
    out.append(GateResult(gate="G2", passed=not bad, hard=True, reason=why))
    first = c.segments[0]
    try:
        hook = show.line(c.hook_id)
        off = hook.start - first.start
        in_first = hook.ep == first.ep and first.start - EPS <= hook.start < first.end
        g3 = in_first and off <= prm.hook_window + EPS
        why = (
            ""
            if g3
            else (
                "hook not in first segment"
                if not in_first
                else f"hook at +{off:.1f}s > {prm.hook_window:g}s"
            )
        )
    except KeyError:
        g3, why = False, "unknown hook line"
    out.append(GateResult(gate="G3", passed=g3, hard=True, reason=why))
    problems = []
    for s in c.segments:
        if s.end - s.start < prm.min_segment - EPS:
            problems.append(f"segment E{s.ep:02d}@{s.start:.1f} too short ({s.end - s.start:.1f}s)")
    for a, b in zip(c.segments, c.segments[1:], strict=False):
        if (b.ep, b.start) < (a.ep, a.start) or (b.ep == a.ep and b.start < a.end - EPS):
            problems.append("segments not chronological")
    out.append(GateResult(gate="G_seg", passed=not problems, hard=True, reason="; ".join(problems)))
    return out


def soft_gates(c: Candidate, show: Show, bible: Bible) -> list[GateResult]:
    q = next((x for x in bible.open_questions if x.idx == c.question_ref), None)
    honest = c.question_ref is not None and q is not None and q.resolved_at is not None
    why = (
        ""
        if honest
        else ("no question_ref" if c.question_ref is None else "payoff not in the show")
    )
    out = [GateResult(gate="G6", passed=honest, hard=False, reason=why)]
    spoil = {ln.id for ln, _ in clip_lines(c, show)} & set(bible.spoiler_line_ids)
    why = "" if not spoil else f"{len(spoil)} spoiler line(s)"
    out.append(GateResult(gate="G7", passed=not spoil, hard=False, reason=why))
    return out


def inputs(ctx: Ctx) -> dict:
    return {"params": BuildParams.from_ctx(ctx).__dict__, "version": 1}


async def run(ctx: Ctx) -> dict:
    prm = BuildParams.from_ctx(ctx)
    items, passed, hard_ok = [], Counter(), 0
    for raw in ctx.artifacts["build"]["items"]:
        c = Candidate.model_validate(raw)
        gates = code_gates(c, ctx.full_show, prm) + soft_gates(c, ctx.full_show, ctx.bible)
        for g in gates:
            passed[g.gate] += g.passed
        ok = all(g.passed for g in gates if g.hard)
        hard_ok += ok
        items.append({"id": c.id, "hard_pass": ok, "gates": [g.model_dump() for g in gates]})
    n = len(items)
    names = ("G1", "G2", "G3", "G_seg", "G6", "G7")
    meta = {"candidates": n, "hard_pass": hard_ok, "pass_counts": {g: passed[g] for g in names}}
    return {"meta": meta, "items": items, "failures": []}


def load(ctx: Ctx, artifact: dict) -> None:
    return None
