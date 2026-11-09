"""montage stage: one call that joins beats from propose into 2-3 segment promos."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field, model_validator

from promo.llm.templates import load_template
from promo.parse.render import est_tokens, fmt_time, render_line
from promo.runner import Ctx
from promo.schema import MontageProposal, Show
from promo.stages.bible import digest

NAME = "montage"
MAX_TOKENS = 4000
CONTEXT_LINES = 3


class MontageOut(BaseModel):
    montages: list[MontageProposal] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _bare_list(cls, data):
        return {"montages": data} if isinstance(data, list) else data


def inputs(ctx: Ctx) -> dict:
    p = ctx.cfg.profile_for("montage")
    if ctx.offline:
        return {"offline": True}
    return {"prompt": load_template("montage").sha, "profile": [p.name, p.model, p.extra_body]}


def check_ready(ctx: Ctx) -> None:
    ctx.cfg.check_ready(["montage"])


def beats_text(show: Show, beats: list[dict]) -> str:
    """Each beat with a few non-camera lines around it, so the model can pick real bounds."""
    blocks = []
    for b in beats:
        ln = show.line(b["line_id"])
        ep = show.ep(ln.ep)
        rows = [x for x in ep.lines if x.kind != "camera"]
        pos = next(i for i, x in enumerate(rows) if x.id == ln.id) if ln.kind != "camera" else None
        if pos is None:
            continue
        lo, hi = max(0, pos - CONTEXT_LINES), pos + CONTEXT_LINES + 1
        lines = [
            ("" if x.id != ln.id else ">> ") + render_line(x, fmt_time(x.start))
            for x in rows[lo:hi]
        ]
        blocks.append(f"[{b['hook_type']}] {b['text']}\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def validate(ms: list[MontageProposal], show: Show) -> tuple[list[MontageProposal], Counter]:
    drops: Counter[str] = Counter()
    kept = []
    for m in ms:
        if not 2 <= len(m.segments) <= 3:
            drops["bad_segment_count"] += 1
            continue
        try:
            segs = [(show.line(s.start_id), show.line(s.end_id)) for s in m.segments]
            hook, cliff = show.line(m.hook_id), show.line(m.cliff_id)
        except KeyError:
            drops["unknown_id"] += 1
            continue
        if any(a.ep != b.ep or a.idx > b.idx for a, b in segs):
            drops["bad_segment"] += 1
            continue
        inside = lambda ln, seg: seg[0].ep == ln.ep and seg[0].idx <= ln.idx <= seg[1].idx  # noqa: E731
        if not inside(hook, segs[0]):
            drops["hook_not_in_first_segment"] += 1
            continue
        if not inside(cliff, segs[-1]):
            drops["cliff_not_in_last_segment"] += 1
            continue
        kept.append(m)
    return kept, drops


async def run(ctx: Ctx) -> dict:
    if ctx.offline:
        return {"meta": {"raw": 0, "kept": 0, "drops": {}, "mode": "offline"}, "items": [],
                "failures": []}  # fmt: skip
    n0 = len(ctx.llm.failures)
    beats = ctx.artifacts["propose"].get("beats", [])
    if not beats:
        return {
            "meta": {"raw": 0, "kept": 0, "drops": {}, "note": "no beats from propose"},
            "items": [],
            "failures": [],
        }
    text = beats_text(ctx.full_show, beats)
    v = {"digest": digest(ctx.bible), "beats": text}
    ctx.ledger.check_budget(1, est_tokens(text + v["digest"]) + 800 + MAX_TOKENS // 2)
    out = await ctx.llm.call_json("montage", "montage", v, MontageOut, max_tokens=MAX_TOKENS)
    if out is None:
        return {
            "meta": {"raw": 0, "kept": 0, "drops": {"call_failed": 1}},
            "items": [],
            "failures": ctx.llm.failures[n0:],
        }
    kept, drops = validate(out.montages, ctx.full_show)
    meta = {"raw": len(out.montages), "kept": len(kept), "drops": dict(drops)}
    return {
        "meta": meta,
        "items": [m.model_dump() for m in kept],
        "failures": ctx.llm.failures[n0:],
    }


def load(ctx: Ctx, artifact: dict) -> None:
    return None
