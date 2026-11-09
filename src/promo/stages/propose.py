"""propose stage: parallel chunks of episodes, validation of every ID, drop counts by reason."""

from __future__ import annotations

import asyncio
from collections import Counter

from pydantic import BaseModel, Field, model_validator

from promo.heuristics import propose_offline
from promo.llm.templates import load_template
from promo.parse.render import compact, est_tokens
from promo.runner import Ctx
from promo.schema import HookType, Proposal, Show
from promo.stages.bible import digest

NAME = "propose"
MAX_TOKENS = 6000


class Beat(BaseModel):
    line_id: str
    hook_type: HookType = "other"
    text: str = ""


class ProposeOut(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _bare_list(cls, data):
        return {"proposals": data} if isinstance(data, list) else data


def inputs(ctx: Ctx) -> dict:
    p = ctx.cfg.profile_for("propose")
    if ctx.offline:
        return {"offline": True, "max_props": ctx.params.max_props_per_ep, "version": 1}
    return {
        "prompt": load_template("propose").sha,
        "profile": [p.name, p.model, p.extra_body],
        "chunk_eps": ctx.params.chunk_eps,
        "max_props": ctx.params.max_props_per_ep,
        "episodes": [e.num for e in ctx.show.episodes],
    }


def check_ready(ctx: Ctx) -> None:
    ctx.cfg.check_ready(["propose"])


def make_chunks(show: Show, n: int) -> list[list[int]]:
    nums = [e.num for e in show.episodes]
    return [nums[i : i + n] for i in range(0, len(nums), n)]


def validate(
    props: list[Proposal], show: Show, allowed_eps: set[int], max_props: int
) -> tuple[list[Proposal], Counter]:
    """Drop unknown IDs, cross-episode, bad order, outside the chunk, and over-cap proposals."""
    drops: Counter[str] = Counter()
    kept: list[Proposal] = []
    for p in props:
        try:
            ids = [show.line(i) for i in (p.start_id, p.hook_id, p.cliff_id, p.end_id)]
        except KeyError:
            drops["unknown_id"] += 1
            continue
        if p.ep not in allowed_eps:
            drops["outside_chunk"] += 1
            continue
        if any(ln.ep != p.ep for ln in ids):
            drops["cross_episode"] += 1
            continue
        s, h, c, e = (ln.idx for ln in ids)
        if not (s <= h <= c <= e):
            drops["bad_order"] += 1
            continue
        kept.append(p)
    per_ep: Counter[int] = Counter()
    final: list[Proposal] = []
    for p in sorted(kept, key=lambda x: -x.confidence):
        per_ep[p.ep] += 1
        if per_ep[p.ep] > max_props:
            drops["over_limit"] += 1
        else:
            final.append(p)
    return sorted(final, key=lambda x: (x.ep, x.hook_id)), drops


async def run(ctx: Ctx) -> dict:
    if ctx.offline:
        props = propose_offline(ctx.show, ctx.params.max_props_per_ep)
        meta = {"mode": "offline", "raw_proposals": len(props), "kept": len(props), "drops": {},
                "per_episode": dict(Counter(str(p.ep) for p in props)), "n_beats": 0}  # fmt: skip
        return {"meta": meta, "items": [p.model_dump() for p in props], "beats": [], "failures": []}
    n0 = len(ctx.llm.failures)
    chunks = make_chunks(ctx.show, ctx.params.chunk_eps)
    dig = digest(ctx.bible)
    jobs = []
    for nums in chunks:
        script = "\n\n".join(f"## EPISODE {n}\n{compact(ctx.full_show.ep(n))}" for n in nums)
        nxt = nums[-1] + 1
        has_next = any(e.num == nxt for e in ctx.full_show.episodes)
        look = (
            f"## EPISODE {nxt} (READ-ONLY LOOKAHEAD)\n{compact(ctx.full_show.ep(nxt))}"
            if has_next
            else "(none, this is the last episode)"
        )
        ep_list = ", ".join(str(n) for n in nums)
        v = {
            "digest": dig,
            "script": script,
            "lookahead": look,
            "max_props": ctx.params.max_props_per_ep,
            "ep_list": ep_list,
        }
        jobs.append((nums, v))
    planned = sum(
        est_tokens(v["script"] + v["lookahead"] + dig) + 1200 + MAX_TOKENS // 2 for _, v in jobs
    )
    ctx.ledger.check_budget(len(jobs), planned)
    outs = await asyncio.gather(
        *(
            ctx.llm.call_json("propose", "propose", v, ProposeOut, max_tokens=MAX_TOKENS)
            for _, v in jobs
        )
    )
    items: list[Proposal] = []
    beats: list[dict] = []
    drops: Counter[str] = Counter()
    raw = 0
    for (nums, _), out in zip(jobs, outs, strict=True):
        if out is None:
            drops["chunk_failed"] += 1
            continue
        raw += len(out.proposals)
        kept, d = validate(out.proposals, ctx.full_show, set(nums), ctx.params.max_props_per_ep)
        items += kept
        drops.update(d)
        for b in out.beats:
            try:
                ln = ctx.full_show.line(b.line_id)
            except KeyError:
                drops["beat_unknown_id"] += 1
                continue
            if ln.ep in nums:
                beats.append(b.model_dump())
            else:
                drops["beat_outside_chunk"] += 1
    per_ep = Counter(p.ep for p in items)
    meta = {
        "chunks": [list(n) for n, _ in jobs],
        "raw_proposals": raw,
        "kept": len(items),
        "drops": dict(drops),
        "per_episode": {str(k): per_ep[k] for k in sorted(per_ep)},
        "n_beats": len(beats),
    }
    return {
        "meta": meta,
        "items": [p.model_dump() for p in items],
        "beats": beats,
        "failures": ctx.llm.failures[n0:],
    }


def load(ctx: Ctx, artifact: dict) -> None:
    return None
