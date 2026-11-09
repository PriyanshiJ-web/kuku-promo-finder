"""coldview stage: batched, shuffled, labelled clips seen as text only. Gives G4, G5 and rubric."""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import string
from collections import Counter

from pydantic import BaseModel, Field, model_validator

from promo.llm.templates import load_template
from promo.parse.render import clip_text, est_tokens
from promo.runner import Ctx
from promo.schema import Candidate, ColdView, GateResult

NAME = "coldview"
MAX_TOKENS = 3000


class ColdOut(BaseModel):
    clips: list[ColdView] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _bare_list(cls, data):
        return {"clips": data} if isinstance(data, list) else data


def survivors_of_code_gates(ctx: Ctx) -> list[Candidate]:
    ok = {x["id"] for x in ctx.artifacts["gates"]["items"] if x["hard_pass"]}
    return [Candidate.model_validate(c) for c in ctx.artifacts["build"]["items"] if c["id"] in ok]


def make_batches(cands: list[Candidate], size: int) -> list[list[Candidate]]:
    """Deterministic shuffle (so reruns hit the cache), then chunks of `size` (max 26)."""
    ordered = sorted(cands, key=lambda c: c.id)
    seed = hashlib.sha256("".join(c.id for c in ordered).encode()).hexdigest()
    random.Random(seed).shuffle(ordered)
    size = min(size, len(string.ascii_uppercase))
    return [ordered[i : i + size] for i in range(0, len(ordered), size)]


def render_batch(batch: list[Candidate], show) -> str:
    return "\n\n".join(
        f"=== CLIP {string.ascii_uppercase[i]} ===\n{clip_text(c, show)}"
        for i, c in enumerate(batch)
    )


def gates_for(cv: ColdView | None) -> list[GateResult]:
    if cv is None:
        return [
            GateResult(gate=g, passed=False, hard=True, reason="no cold view") for g in ("G4", "G5")
        ]
    g4 = GateResult(
        gate="G4",
        passed=cv.open_ending,
        hard=True,
        reason="" if cv.open_ending else "ending is resolved or trails off",
    )
    ok5 = cv.makes_sense and bool(cv.who.strip()) and bool(cv.stakes.strip())
    g5 = GateResult(
        gate="G5",
        passed=ok5,
        hard=True,
        reason="" if ok5 else "viewer could not state who and stakes",
    )
    return [g4, g5]


def inputs(ctx: Ctx) -> dict:
    p = ctx.cfg.profile_for("coldview")
    if ctx.offline:
        return {"offline": True}
    return {
        "prompt": load_template("coldview").sha,
        "profile": [p.name, p.model, p.extra_body],
        "batch": ctx.params.coldview_batch,
    }


def check_ready(ctx: Ctx) -> None:
    ctx.cfg.check_ready(["coldview"])


async def _pass(ctx: Ctx, cands: list[Candidate], drops: Counter) -> dict[str, ColdView]:
    batches = make_batches(cands, ctx.params.coldview_batch)
    texts = [render_batch(b, ctx.full_show) for b in batches]
    tpl = load_template("coldview")
    ctx.ledger.check_budget(
        len(batches),
        sum(est_tokens(t) for t in texts) + len(batches) * (est_tokens(tpl.text) + MAX_TOKENS // 2),
    )
    outs = await asyncio.gather(
        *(
            ctx.llm.call_json("coldview", "coldview", {"clips": t}, ColdOut, max_tokens=MAX_TOKENS)
            for t in texts
        )
    )
    got: dict[str, ColdView] = {}
    for batch, out in zip(batches, outs, strict=True):
        if out is None:
            drops["batch_failed"] += 1
            continue
        by_label = {string.ascii_uppercase[i]: c for i, c in enumerate(batch)}
        for cv in out.clips:
            c = by_label.get(cv.clip_ref.strip().upper())
            if c is None:
                drops["unknown_clip_ref"] += 1
            elif c.id not in got:
                got[c.id] = cv
    return got


async def run(ctx: Ctx) -> dict:
    n0 = len(ctx.llm.failures)
    cands = survivors_of_code_gates(ctx)
    if ctx.offline:  # no model, so no cold view: G4 and G5 are not evaluated
        items = [{"id": c.id, "coldview": None, "gates": []} for c in cands]
        meta = {"clips": len(cands), "answered": 0, "missing": 0, "calls_planned": 0,
                "drops": {}, "mode": "offline"}  # fmt: skip
        return {"meta": meta, "items": items, "failures": []}
    drops: Counter[str] = Counter()
    got = await _pass(ctx, cands, drops) if cands else {}
    missing = [c for c in cands if c.id not in got]
    if missing:  # one more pass for clips the model skipped or whose batch failed
        drops["retried_missing"] = len(missing)
        got.update(await _pass(ctx, missing, drops))
    items = []
    for c in cands:
        cv = got.get(c.id)
        items.append(
            {
                "id": c.id,
                "coldview": cv.model_dump() if cv else None,
                "gates": [g.model_dump() for g in gates_for(cv)],
            }
        )
    meta = {
        "clips": len(cands),
        "answered": len(got),
        "missing": len(cands) - len(got),
        "calls_planned": math.ceil(len(cands) / max(ctx.params.coldview_batch, 1)),
        "drops": dict(drops),
    }
    return {"meta": meta, "items": items, "failures": ctx.llm.failures[n0:]}


def load(ctx: Ctx, artifact: dict) -> None:
    return None
