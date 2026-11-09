"""parse stage (code only): writes parse.json and parse_report.json, aborts on low coverage."""

from __future__ import annotations

import hashlib
from pathlib import Path

from promo.runner import Ctx, StageAbort, write_json

NAME = "parse"
MIN_COVERAGE = 0.95


def _file_shas(folder: Path) -> dict[str, str]:
    return {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(folder.glob("*.txt"))
    }


def inputs(ctx: Ctx) -> dict:
    return {"files": _file_shas(ctx.folder), "episodes": ctx.episodes_spec}


async def run(ctx: Ctx) -> dict:
    rep = ctx.full_show.report
    if rep.coverage < MIN_COVERAGE:
        warns = "; ".join(rep.warnings[:10]) or "no warnings recorded"
        raise StageAbort(
            f"parse coverage {rep.coverage:.3f} is below {MIN_COVERAGE}. Warnings: {warns}"
        )
    write_json(ctx.out_dir / "parse_report.json", rep.model_dump())
    items = [
        {
            **e.model_dump(exclude={"lines", "cuts"}),
            "n_lines": len(e.lines),
            "n_cuts": len(e.cuts),
            "selected": e.num in {x.num for x in ctx.show.episodes},
        }
        for e in ctx.full_show.episodes
    ]
    return {
        "meta": {
            "show": ctx.name,
            "episodes_spec": ctx.episodes_spec,
            "coverage": rep.coverage,
            "n_episodes": rep.episodes,
        },
        "items": items,
        "failures": [],
    }


def load(ctx: Ctx, artifact: dict) -> None:
    return None
