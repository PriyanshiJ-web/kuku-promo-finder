"""bible stage: one long-context call (map-reduce when too big), alias map applied by code."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from pydantic import BaseModel, Field

from promo.llm.templates import load_template
from promo.parse.render import compact, est_tokens
from promo.runner import Ctx
from promo.schema import Bible, Character, OpenQuestion, Show

NAME = "bible"
MAX_TOKENS, CHUNK_MAX_TOKENS = 12000, 4000


class _Q(BaseModel):
    idx: int | None = None
    question: str
    raised_at: str
    resolved_at: str | None = None


class BibleOut(BaseModel):
    premise: str = ""
    characters: list[Character] = Field(default_factory=list)
    arcs: list[dict] = Field(default_factory=list)
    reveals: list[dict] = Field(default_factory=list)
    open_questions: list[_Q] = Field(default_factory=list)
    spoiler_line_ids: list[str] = Field(default_factory=list)


class ChunkOut(BaseModel):
    summary: str = ""
    characters: list[dict] = Field(default_factory=list)
    reveals: list[dict] = Field(default_factory=list)
    open_questions: list[_Q] = Field(default_factory=list)
    spoiler_candidates: list[str] = Field(default_factory=list)


def norm(tag: str) -> str:
    return " ".join(tag.upper().split())


def tag_stats(show: Show) -> list[tuple[str, int, int]]:
    """(tag, line count, first episode) for every raw speaker tag, most frequent first."""
    cnt: Counter[str] = Counter()
    first: dict[str, int] = {}
    for ep in show.episodes:
        for ln in ep.lines:
            if ln.kind == "dialogue" and ln.speaker_raw:
                t = norm(ln.speaker_raw)
                cnt[t] += 1
                first.setdefault(t, ep.num)
    return [(t, n, first[t]) for t, n in cnt.most_common()]


def _tags_text(show: Show) -> str:
    return "\n".join(f"- {t} ({n} lines, first in E{e:02d})" for t, n, e in tag_stats(show))


def _meta_text(show: Show) -> str:
    rows = []
    for ep in show.episodes:
        rows.append(
            f"E{ep.num:02d} LOGLINE: {ep.logline or '-'}\n"
            f"E{ep.num:02d} CONTINUITY: {ep.continuity or '-'}"
        )
    return "\n".join(rows)


def _script_text(show: Show) -> str:
    return "\n\n".join(f"## EPISODE {ep.num}\n{compact(ep)}" for ep in show.episodes)


def _chunks(show: Show, n: int) -> list[Show]:
    return [
        show.model_copy(update={"episodes": show.episodes[i : i + n]})
        for i in range(0, len(show.episodes), n)
    ]


def inputs(ctx: Ctx) -> dict:
    p = ctx.cfg.profile_for("bible")
    if ctx.offline:
        return {"offline": True, "version": 1}
    return {
        "prompts": [load_template(n).sha for n in ("bible", "bible_chunk")],
        "profile": [p.name, p.model, p.extra_body, p.max_input_tokens],
        "chunk_eps": ctx.params.chunk_eps,
    }


def check_ready(ctx: Ctx) -> None:
    ctx.cfg.check_ready(["bible"])


def postprocess(out: BibleOut, show: Show) -> tuple[Bible, dict]:
    """Apply the model's bible: keep only aliases that are real tags and IDs that exist."""
    tags = {norm(t) for t, _, _ in tag_stats(show)}
    ids = {ln.id for ep in show.episodes for ln in ep.lines}
    alias_map: dict[str, str] = {}
    dropped = {"aliases": 0, "alias_conflicts": 0, "line_ids": 0}
    chars: list[Character] = []
    for ch in out.characters:
        kept = []
        for name in dict.fromkeys([ch.canonical, *ch.aliases]):
            n = norm(name)
            if n not in tags:
                if name != ch.canonical:
                    dropped["aliases"] += 1
                continue
            if n in alias_map and alias_map[n] != ch.canonical:
                dropped["alias_conflicts"] += 1
                continue
            alias_map[n] = ch.canonical
            kept.append(name)
        chars.append(ch.model_copy(update={"aliases": kept}))
    reveals = [r for r in out.reveals if r.get("line_id") in ids]
    dropped["line_ids"] += len(out.reveals) - len(reveals)
    questions: list[OpenQuestion] = []
    for q in out.open_questions:
        if q.raised_at not in ids:
            dropped["line_ids"] += 1
            continue
        res = q.resolved_at if q.resolved_at in ids else None
        dropped["line_ids"] += int(q.resolved_at is not None and res is None)
        questions.append(
            OpenQuestion(
                idx=len(questions), question=q.question, raised_at=q.raised_at, resolved_at=res
            )
        )
    spoilers = [i for i in out.spoiler_line_ids if i in ids]
    dropped["line_ids"] += len(out.spoiler_line_ids) - len(spoilers)
    bible = Bible(
        premise=out.premise,
        characters=chars,
        arcs=out.arcs,
        reveals=reveals,
        open_questions=questions,
        spoiler_line_ids=spoilers,
    )
    unresolved = sorted(tags - set(alias_map))
    return bible, {"alias_map": alias_map, "unresolved_tags": unresolved, "dropped": dropped}


def fallback_bible(show: Show) -> tuple[Bible, dict]:
    """No model: one character per distinct speaker tag, no aliasing."""
    chars = [Character(canonical=t, aliases=[t], first_ep=e) for t, _, e in tag_stats(show)]
    return Bible(characters=chars), {
        "alias_map": {norm(c.canonical): c.canonical for c in chars},
        "unresolved_tags": [],
        "dropped": {},
    }


async def _single_or_chunked(ctx: Ctx) -> tuple[BibleOut | None, str, int]:
    show, llm = ctx.show, ctx.llm
    meta, tags, script = _meta_text(show), _tags_text(show), _script_text(show)
    prof = ctx.cfg.profile_for("bible")
    est = est_tokens(meta + tags + script) + 800
    if est <= prof.max_input_tokens * 0.8:
        ctx.ledger.check_budget(1, est + MAX_TOKENS)
        v = {
            "meta": meta,
            "tags": tags,
            "material": script,
            "material_note": "FULL SCRIPT (ID [mm:ss.s] SPEAKER: text)",
        }
        return (
            await llm.call_json("bible", "bible", v, BibleOut, max_tokens=MAX_TOKENS),
            "single",
            1,
        )
    parts = _chunks(show, ctx.params.chunk_eps)
    ctx.ledger.check_budget(len(parts) + 1, est + MAX_TOKENS + len(parts) * CHUNK_MAX_TOKENS)

    async def one(sh: Show):
        v = {"meta": _meta_text(sh), "tags": _tags_text(sh), "script": _script_text(sh)}
        return await llm.call_json("bible", "bible_chunk", v, ChunkOut, max_tokens=CHUNK_MAX_TOKENS)

    sums = await asyncio.gather(*(one(sh) for sh in parts))
    good = [s.model_dump() for s in sums if s is not None]
    v = {
        "meta": meta,
        "tags": tags,
        "material": json.dumps(good),
        "material_note": "CHUNK SUMMARIES (JSON, one per group of consecutive episodes, in order)",
    }
    out = (
        await llm.call_json("bible", "bible", v, BibleOut, max_tokens=MAX_TOKENS) if good else None
    )
    return out, "map_reduce", len(parts) + 1


async def run(ctx: Ctx) -> dict:
    if ctx.offline:  # no model: one character per speaker tag
        bible, info = fallback_bible(ctx.show)
        meta = {"mode": "offline", "degraded": False, "n_tags": len(tag_stats(ctx.show)),
                "n_characters": len(bible.characters), **info}  # fmt: skip
        return {"meta": meta, "items": [bible.model_dump()], "failures": []}
    n0 = len(ctx.llm.failures)
    out, mode, calls = await _single_or_chunked(ctx)
    degraded = out is None
    bible, info = fallback_bible(ctx.show) if out is None else postprocess(out, ctx.show)
    meta = {
        "mode": mode,
        "planned_calls": calls,
        "degraded": degraded,
        "n_tags": len(tag_stats(ctx.show)),
        "n_characters": len(bible.characters),
        **info,
    }
    failures = ctx.llm.failures[n0:]
    return {"meta": meta, "items": [bible.model_dump()], "failures": failures}


def apply_aliases(show: Show, alias_map: dict[str, str]) -> None:
    """Fill Line.speaker from the map. Unmapped tags keep their raw tag."""
    for ep in show.episodes:
        for ln in ep.lines:
            if ln.kind == "dialogue" and ln.speaker_raw:
                ln.speaker = alias_map.get(norm(ln.speaker_raw), ln.speaker_raw)


def digest(bible: Bible) -> str:
    rows = [f"PREMISE: {bible.premise or '(unknown)'}", "CHARACTERS:"]
    rows += [f"- {c.canonical}: {c.role or 'unknown role'}" for c in bible.characters]
    rows.append("OPEN QUESTIONS (use the number as question_ref):")
    rows += [f"[{q.idx}] {q.question}" for q in bible.open_questions]
    return "\n".join(rows)


def load(ctx: Ctx, artifact: dict) -> None:
    ctx.bible = Bible.model_validate(artifact["items"][0])
    ctx.alias_map = artifact["meta"]["alias_map"]
    apply_aliases(ctx.full_show, ctx.alias_map)
