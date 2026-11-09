"""Pairwise ranking pieces: Swiss pairing, both-order judging, Bradley-Terry, MMR."""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from promo.parse.render import clip_blocks
from promo.schema import Candidate, Show
from promo.stages.build import jaccard, time_iou

JUDGE_MAX_TOKENS = 400


class Verdict(BaseModel):
    hook: Literal["A", "B"]
    ending: Literal["A", "B"]
    clarity: Literal["A", "B"]
    reason: str = ""

    @property
    def winner(self) -> Literal["A", "B"]:
        return decide({"hook": self.hook, "ending": self.ending, "clarity": self.clarity})


def decide(votes: dict[str, str]) -> Literal["A", "B"]:
    """Majority of the per-question votes, the hook vote breaks a tie."""
    a = sum(1 for v in votes.values() if v == "A")
    b = len(votes) - a
    if a != b:
        return "A" if a > b else "B"
    return votes["hook"]  # type: ignore[return-value]


@dataclass
class PairResult:
    a: str
    b: str
    score_a: float  # 1, 0.5 or 0 for a
    agree: bool
    valid: int  # how many of the two orders produced a verdict
    reasons: dict[str, list[str]] = field(default_factory=dict)  # winner id -> reasons
    detail: list[dict] = field(default_factory=list)  # per order: question -> winning clip id


def swiss_pairs(
    order: list[str],
    wins: dict[str, float],
    played: set[frozenset],
    pointwise: dict[str, float],
    first_round: bool,
) -> list[tuple[str, str]]:
    """Round 1: neighbours in pointwise order. Later: by wins then pointwise. No rematches."""
    if first_round:
        seq = sorted(order, key=lambda i: (-pointwise[i], i))
    else:
        seq = sorted(order, key=lambda i: (-wins.get(i, 0.0), -pointwise[i], i))
    used: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for i, x in enumerate(seq):
        if x in used:
            continue
        for y in seq[i + 1 :]:
            if y not in used and frozenset((x, y)) not in played:
                pairs.append((x, y))
                used.update((x, y))
                break
    return pairs


async def judge_pair(llm, cx: Candidate, cy: Candidate, show: Show) -> PairResult:
    bx, by = clip_blocks(cx, show), clip_blocks(cy, show)

    async def once(first: tuple[str, str, str], second: tuple[str, str, str]):
        v = {f"{k}_{side}": t for side, blk in (("a", first), ("b", second))
             for k, t in zip(("opening", "full", "last"), blk, strict=True)}  # fmt: skip
        return await llm.call_json(
            "judge", "judge_pairwise", v, Verdict, max_tokens=JUDGE_MAX_TOKENS
        )

    r1, r2 = await asyncio.gather(once(bx, by), once(by, bx))
    votes: list[tuple[str, str]] = []  # (winner id, reason)
    if r1:
        votes.append((cx.id if r1.winner == "A" else cy.id, r1.reason))
    if r2:
        votes.append((cy.id if r2.winner == "A" else cx.id, r2.reason))
    detail = [
        {q: (a_id if getattr(r, q) == "A" else b_id) for q in ("hook", "ending", "clarity")}
        for r, (a_id, b_id) in ((r1, (cx.id, cy.id)), (r2, (cy.id, cx.id)))
        if r
    ]
    reasons: dict[str, list[str]] = {}
    if len(votes) == 2:
        agree = votes[0][0] == votes[1][0]
        if agree:  # reasons from a split (position-biased) decision are not evidence of a win
            reasons = {votes[0][0]: [votes[0][1], votes[1][1]]}
        score = (1.0 if votes[0][0] == cx.id else 0.0) if agree else 0.5
    elif len(votes) == 1:
        agree, score = True, 1.0 if votes[0][0] == cx.id else 0.0
        reasons = {votes[0][0]: [votes[0][1]]}
    else:
        agree, score = True, 0.5
    return PairResult(cx.id, cy.id, score, agree, len(votes), reasons, detail)


def bradley_terry(
    ids: list[str], results: list[PairResult], iters: int = 200, tol: float = 1e-10
) -> dict[str, float]:
    """MM updates with a 0.5 pseudo-win against a phantom opponent of strength 1."""
    p = dict.fromkeys(ids, 1.0)
    wins = dict.fromkeys(ids, 0.0)
    games: dict[str, dict[str, int]] = {i: {} for i in ids}
    for r in results:
        if r.valid == 0:
            continue
        wins[r.a] += r.score_a
        wins[r.b] += 1.0 - r.score_a
        games[r.a][r.b] = games[r.a].get(r.b, 0) + 1
        games[r.b][r.a] = games[r.b].get(r.a, 0) + 1
    for _ in range(iters):
        new = {}
        for i in ids:
            den = sum(n / (p[i] + p[j]) for j, n in games[i].items()) + 1.0 / (p[i] + 1.0)
            new[i] = (wins[i] + 0.5) / den
        gm = math.exp(sum(math.log(v) for v in new.values()) / len(new))
        new = {i: v / gm for i, v in new.items()}
        delta = max(abs(new[i] - p[i]) for i in ids)
        p = new
        if delta < tol:
            break
    return p


def norm_log(p: dict[str, float]) -> dict[str, float]:
    logs = {i: math.log(v) for i, v in p.items()}
    lo, hi = min(logs.values()), max(logs.values())
    return {i: (v - lo) / (hi - lo) if hi > lo else 0.5 for i, v in logs.items()}


def similarity(a: Candidate, b: Candidate, show: Show) -> float:
    base = max(time_iou(a, b, show), jaccard(a, b, show))
    same_ep = show.line(a.hook_id).ep == show.line(b.hook_id).ep
    same_type = a.hook_type == b.hook_type
    return min(1.0, base + 0.3 * same_ep + 0.2 * same_type)


def mmr_order(cands: list[Candidate], final: dict[str, float], lam: float, show: Show) -> list[str]:
    """Greedy re-rank: lam * final - (1 - lam) * max similarity to what is already selected."""
    left = sorted(cands, key=lambda c: (-final[c.id], c.id))
    chosen: list[Candidate] = []
    while left:

        def score(c: Candidate) -> float:
            sim = max((similarity(c, s, show) for s in chosen), default=0.0)
            return lam * final[c.id] - (1 - lam) * sim

        best = max(left, key=lambda c: (score(c), -left.index(c)))
        chosen.append(best)
        left.remove(best)
    return [c.id for c in chosen]


def condense(reasons: list[str]) -> str:
    """One line from the judge reasons: the one sharing the most words with the others."""
    reasons = [r.strip() for r in reasons if r.strip()]
    if not reasons:
        return ""
    if len(reasons) == 1:
        return reasons[0]
    sets = [set(re.findall(r"[a-z']+", r.lower())) for r in reasons]

    def overlap(i: int) -> float:
        return sum(
            len(sets[i] & s) / max(len(sets[i] | s), 1) for j, s in enumerate(sets) if j != i
        )

    return reasons[max(range(len(reasons)), key=lambda i: (overlap(i), -i))]


def tally(results: list[PairResult]) -> dict:
    valid = [r for r in results if r.valid == 2]
    split = sum(1 for r in valid if not r.agree)
    return {
        "pairs": len(results),
        "both_orders": len(valid),
        "split": split,
        "position_bias_rate": round(split / len(valid), 3) if valid else 0.0,
        "reason_counts": dict(Counter(r.valid for r in results)),
    }
