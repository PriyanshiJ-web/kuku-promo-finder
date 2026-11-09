"""rank stage: pointwise score, Swiss pairwise judging, Bradley-Terry and MMR diversity."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from promo.llm.templates import load_template
from promo.parse.render import clip_text, est_tokens
from promo.runner import Ctx
from promo.schema import Candidate, ColdView, GateResult, Promo
from promo.stages.pairwise import (
    JUDGE_MAX_TOKENS,
    PairResult,
    bradley_terry,
    condense,
    judge_pair,
    mmr_order,
    norm_log,
    swiss_pairs,
    tally,
)

NAME = "rank"
W_HOOK, W_CURIOSITY, W_CLARITY, W_CONF = 0.4, 0.25, 0.2, 0.15
PENALTY = {"G6": 0.1, "G7": 0.15}
FREE_BOOST = 0.05


def scale(x: int) -> float:
    """1..5 rubric score to 0..1."""
    return (x - 1) / 4


def pointwise(
    cv: ColdView, c: Candidate, gates: list[GateResult], hook_ep: int, free_eps: int
) -> float:
    score = (
        W_HOOK * scale(cv.hook) + W_CURIOSITY * scale(cv.curiosity) + W_CLARITY * scale(cv.clarity)
        + W_CONF * c.confidence
    )  # fmt: skip
    score -= sum(PENALTY.get(g.gate, 0.0) for g in gates if not g.passed and not g.hard)
    if hook_ep <= free_eps:
        score += FREE_BOOST
    return round(score, 6)


def inputs(ctx: Ctx) -> dict:
    p = ctx.cfg.profile_for("judge")
    if ctx.offline:
        return {"offline": True, "free_eps": ctx.params.free_eps, "version": 1}
    q = ctx.params
    return {
        "prompt": load_template("judge_pairwise").sha,
        "profile": [p.name, p.model, p.extra_body],
        "params": [q.free_eps, q.top_k_pairwise, q.swiss_rounds, q.mmr_lambda],
        "version": 2,
    }


def check_ready(ctx: Ctx) -> None:
    ctx.cfg.check_ready(["judge"])


def collect(ctx: Ctx) -> tuple[list[Promo], dict]:
    """All candidates that pass every hard gate, scored, best first. Also exclusion counts."""
    gates = {
        x["id"]: [GateResult.model_validate(g) for g in x["gates"]]
        for x in ctx.artifacts["gates"]["items"]
    }
    cold = {x["id"]: x for x in ctx.artifacts["coldview"]["items"]}
    out: list[Promo] = []
    excluded: dict[str, int] = {}
    for raw in ctx.artifacts["build"]["items"]:
        c = Candidate.model_validate(raw)
        g = gates[c.id]
        cv_item = cold.get(c.id)
        if cv_item is not None:
            g = g + [GateResult.model_validate(x) for x in cv_item["gates"]]
        failed = [x.gate for x in g if x.hard and not x.passed]
        if ctx.offline:
            if failed:
                for f in failed:
                    excluded[f] = excluded.get(f, 0) + 1
                continue
            sc = round(c.confidence, 6)  # offline: pointwise is the heuristic confidence
            out.append(Promo(rank=0, candidate=c, gates=g, pointwise=sc, final=sc))
            continue
        if failed or cv_item is None or cv_item["coldview"] is None:
            for f in failed or ["no_coldview"]:
                excluded[f] = excluded.get(f, 0) + 1
            continue
        cv = ColdView.model_validate(cv_item["coldview"])
        hook_ep = ctx.full_show.line(c.hook_id).ep
        p = pointwise(cv, c, g, hook_ep, ctx.params.free_eps)
        out.append(Promo(rank=0, candidate=c, gates=g, coldview=cv, pointwise=p, final=p))
    out.sort(key=lambda p: (-p.final, p.candidate.id))
    for i, p in enumerate(out, 1):
        p.rank = i
    return out, excluded


async def swiss(ctx: Ctx, top: list[Promo]) -> list[PairResult]:
    cands = {p.candidate.id: p.candidate for p in top}
    pw = {p.candidate.id: p.pointwise for p in top}
    ids = list(cands)
    wins: dict[str, float] = defaultdict(float)
    played: set[frozenset] = set()
    results: list[PairResult] = []
    tpl = load_template("judge_pairwise")
    for rnd in range(ctx.params.swiss_rounds):
        pairs = swiss_pairs(ids, wins, played, pw, rnd == 0)
        if not pairs:
            break
        toks = sum(
            2
            * (
                est_tokens(tpl.text)
                + est_tokens(
                    clip_text(cands[a], ctx.full_show) + clip_text(cands[b], ctx.full_show)
                )
                + JUDGE_MAX_TOKENS // 4
            )
            for a, b in pairs
        )
        ctx.ledger.check_budget(2 * len(pairs), toks)
        res = await asyncio.gather(
            *(judge_pair(ctx.llm, cands[a], cands[b], ctx.full_show) for a, b in pairs)
        )
        for r in res:
            results.append(r)
            played.add(frozenset((r.a, r.b)))
            if r.valid:
                wins[r.a] += r.score_a
                wins[r.b] += 1.0 - r.score_a
    return results


async def run(ctx: Ctx) -> dict:
    n0 = len(ctx.llm.failures)
    promos, excluded = collect(ctx)
    top_k = ctx.params.top_k_pairwise
    top, rest = promos[:top_k], promos[top_k:]
    pw10 = [p.candidate.id for p in promos[:10]]
    results: list[PairResult] = []
    if len(top) >= 2 and ctx.params.swiss_rounds > 0 and not ctx.offline:
        results = await swiss(ctx, top)
    final: dict[str, float] = {}
    bt: dict[str, float] = {}
    if results:
        bt = bradley_terry([p.candidate.id for p in top], results)
        nl = norm_log(bt)
        for p in top:
            final[p.candidate.id] = 0.7 * nl[p.candidate.id] + 0.3 * p.pointwise
    else:
        final.update({p.candidate.id: p.pointwise for p in top})
    final.update(
        {p.candidate.id: p.pointwise - 1 for p in rest}
    )  # below everything in the top group
    lam = ctx.params.mmr_lambda
    by_id = {p.candidate.id: p for p in promos}
    order = mmr_order([p.candidate for p in top], final, lam, ctx.full_show)
    order += mmr_order([p.candidate for p in rest], final, lam, ctx.full_show)
    reasons: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for winner, rs in r.reasons.items():
            reasons[winner] += rs
    ranked = []
    for i, cid in enumerate(order, 1):
        p = by_id[cid]
        p.rank, p.final, p.bt = i, round(final[cid], 6), (round(bt[cid], 6) if cid in bt else None)
        p.post_first_reason = condense(reasons.get(cid, []))
        ranked.append(p)
    meta = {
        "ranked": len(ranked),
        "excluded_by_gate": excluded,
        "pointwise_top10": pw10,
        "method": "swiss+bt+mmr" if results else "pointwise",
        "judge": tally(results) if results else {},
    }
    pairs = [
        {"a": r.a, "b": r.b, "score_a": r.score_a, "agree": r.agree, "valid": r.valid,
         "reasons": r.reasons, "detail": r.detail}
        for r in results
    ]  # fmt: skip
    return {
        "meta": meta,
        "items": [p.model_dump() for p in ranked],
        "pairs": pairs,
        "failures": ctx.llm.failures[n0:],
    }


def load(ctx: Ctx, artifact: dict) -> None:
    return None
