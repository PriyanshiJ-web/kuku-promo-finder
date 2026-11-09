"""Baseline candidate sets in the same schema as the tool's output (spec section 6)."""

from __future__ import annotations

import random

from promo.heuristics import propose_offline
from promo.parse.cuts import cut_at_or_after, cut_at_or_before
from promo.parse.render import compact
from promo.schema import Candidate, Show
from promo.stages.build import BuildParams, build_single, make_candidate, make_segment
from promo.stages.gates import code_gates
from promo.stages.propose import ProposeOut, validate

WINDOW = 45.0
N = 10


def window_candidate(show: Show, ep_num: int, start: float, prm: BuildParams, source: str):
    """A WINDOW-second clip from `start`, both ends snapped to cuts, kept within the length rule."""
    ep = show.ep(ep_num)
    a = cut_at_or_before(ep, max(0.0, start))
    b = cut_at_or_after(ep, a + WINDOW)
    if b - a > prm.max_len:
        b = cut_at_or_before(ep, a + prm.max_len)
    rows = [ln for ln in ep.lines if ln.kind != "camera" and a <= ln.start < b]
    if b - a < prm.min_len or not rows:
        return None
    fields = {
        "hook_id": rows[0].id,
        "cliff_id": rows[-1].id,
        "hook_type": "other",
        "confidence": 0.0,
    }
    seg = make_segment(ep, a, b, (rows[0].id, rows[-1].id))
    return make_candidate("single", "tight", [seg], fields, source, [], show)


def random_45(show: Show, prm: BuildParams, n: int = N, seed: int = 0) -> list[Candidate]:
    rng = random.Random(seed)
    out: list[Candidate] = []
    for _ in range(n * 20):
        if len(out) >= n:
            break
        ep = rng.choice(show.episodes)
        c = window_candidate(
            show, ep.num, rng.uniform(0, max(ep.duration - WINDOW, 0)), prm, "random_45"
        )
        if c and c.id not in {x.id for x in out}:
            out.append(c)
    return out


def last_45(show: Show, prm: BuildParams, n: int = N, seed: int = 0) -> list[Candidate]:
    rng = random.Random(seed)
    eps = rng.sample(show.episodes, min(n, len(show.episodes)))
    out = [window_candidate(show, e.num, e.duration - WINDOW, prm, "last_45") for e in eps]
    return [c for c in out if c]


def heuristic(show: Show, prm: BuildParams, max_props: int, n: int = N) -> list[Candidate]:
    props = sorted(propose_offline(show, max_props), key=lambda p: (-p.confidence, p.hook_id))
    out: list[Candidate] = []
    for p in props:
        cands, _ = build_single(p, show, prm)
        if not cands:
            continue
        c = cands[0]
        ok = all(g.passed for g in code_gates(c, show, prm) if g.gate in ("G1", "G2"))
        if ok and c.id not in {x.id for x in out}:
            out.append(c)
        if len(out) >= n:
            break
    return out


async def single_prompt(
    llm, show: Show, prm: BuildParams, n: int = N
) -> tuple[list[Candidate], int]:
    """One LLM call over the whole compact show. Returns candidates and how many were dropped."""
    script = "\n\n".join(f"## EPISODE {e.num}\n{compact(e)}" for e in show.episodes)
    out = await llm.call_json(
        "propose", "single_prompt", {"script": script}, ProposeOut, max_tokens=6000
    )
    if out is None:
        return [], 0
    nums = {e.num for e in show.episodes}
    kept, drops = validate(out.proposals, show, nums, n)
    cands: list[Candidate] = []
    for p in sorted(kept, key=lambda x: -x.confidence):
        built, _ = build_single(p, show, prm)
        if built and built[0].id not in {c.id for c in cands}:
            cands.append(built[0])
    return cands[:n], sum(drops.values())
