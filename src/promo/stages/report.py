"""report stage: promos.json, promos.csv and report.md (spec section 5, report)."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path

from promo.parse.render import clip_text
from promo.runner import Ctx, load_manifest, sha_bytes, write_atomic, write_json
from promo.schema import Bible, Promo
from promo.stages.polish import polish

NAME = "report"
CSV_COLS = ["rank", "promo_id", "kind", "seg_no", "episode", "start", "end", "start_s", "end_s",
            "duration_total", "hook_type", "hook", "cliffhanger", "why", "post_first_reason",
            "gates_failed"]  # fmt: skip


def mmss(t: float) -> str:
    cs = int(round(t * 100))
    m, rem = divmod(cs, 6000)
    return f"{m:02d}:{rem // 100:02d}.{rem % 100:02d}"


def collapse_variants(promos: list[Promo]) -> list[Promo]:
    """Keep only the best-scoring variant (tight or loose) of each source proposal, renumbered."""
    best: dict[str, Promo] = {}
    for p in promos:
        cur = best.get(p.candidate.source)
        if cur is None or (p.final, -p.rank) > (cur.final, -cur.rank):
            best[p.candidate.source] = p
    keep = {id(p) for p in best.values()}
    out = [p.model_copy(deep=True) for p in promos if id(p) in keep]
    for i, p in enumerate(out, 1):
        p.rank = i
    return out


def inputs(ctx: Ctx) -> dict:
    polish_src = sha_bytes((Path(__file__).parent / "polish.py").read_bytes())
    return {"top_n": ctx.params.top_n_report, "version": 3, "polish": polish_src}


def csv_text(promos: list[Promo]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLS)
    for p in promos:
        c = p.candidate
        failed = ",".join(g.gate for g in p.gates if not g.passed)
        for i, s in enumerate(c.segments, 1):
            w.writerow([p.rank, c.id, c.kind, i, s.ep, mmss(s.start), mmss(s.end), f"{s.start:.2f}",
                        f"{s.end:.2f}", f"{c.duration:.2f}", c.hook_type, c.hook_text,
                        c.cliffhanger_text, c.why, p.post_first_reason, failed])  # fmt: skip
    return buf.getvalue()


def _usage_by_stage(ctx: Ctx) -> dict[str, dict]:
    use: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "hits": 0, "failed": 0}
    )
    path = ctx.out_dir / "run_log.jsonl"
    if not path.exists():
        return use
    for raw in path.read_text().splitlines():
        r = json.loads(raw)
        u = use[r["stage"]]
        if r["cache_hit"]:
            u["hits"] += 1
        elif r["profile"] and not str(r["error"]).startswith(
            ("invalid output", "call failed", "skipped")
        ):
            u["calls"] += 1
            u["in"] += r["in_tokens"]
            u["out"] += r["out_tokens"]
            u["cost"] += r["cost_usd"]
            u["failed"] += 0 if r["ok"] else 1
    return use


def markdown(ctx: Ctx, promos: list[Promo]) -> str:
    art = ctx.artifacts
    man = load_manifest(ctx.out_dir)["stages"]
    top = promos[: ctx.params.top_n_report]
    pm, mm, bm, gm = (art[k]["meta"] for k in ("propose", "montage", "build", "gates"))
    out = [f"# Promo report: {ctx.name}", "", "## Summary", ""]
    out += [
        f"- episodes analysed: {len(ctx.show.episodes)} of {len(ctx.full_show.episodes)}",
        f"- proposals kept: {pm['kept']}, montage proposals kept: {mm['kept']}",
        f"- candidates built: {bm['kept']}, passing code gates: {gm['hard_pass']}",
        f"- ranked promos (all hard gates passed): {len(promos)}, one variant per proposal",
        f"- cut polish: {art.get('report_polish', {})}",
        "",
        f"## Top {len(top)}",
        "",
    ]
    for p in top:
        c = p.candidate
        segs = "; ".join(f"E{s.ep:02d} {mmss(s.start)} to {mmss(s.end)}" for s in c.segments)
        soft = ", ".join(g.gate for g in p.gates if not g.passed) or "none"
        out += [
            f"### {p.rank}. {c.kind}/{c.variant}, {c.duration:.1f}s, {c.hook_type}",
            f"- segments: {segs}",
            f"- hook: {c.hook_text}",
            f"- cliffhanger: {c.cliffhanger_text}",
            f"- why: {c.why}",
            f"- post first because: {p.post_first_reason or 'n/a'}",
            f"- scores: pointwise {p.pointwise:.3f}, final {p.final:.3f}",
            f"- gates failed: {soft}",
            "",
            "```",
            clip_text(c, ctx.full_show),
            "```",
            "",
        ]
    out += ["## Gates", "", "| gate | passed |", "|---|---|"]
    n = gm["candidates"]
    out += [f"| {g} | {v} of {n} |" for g, v in gm["pass_counts"].items()]
    items = art["coldview"]["items"]
    for g in ("G4", "G5"):
        ok = sum(1 for it in items for x in it["gates"] if x["gate"] == g and x["passed"])
        out.append(f"| {g} | {ok} of {len(items)} |")
    out += ["", "## Drop reasons", ""]
    for name in ("propose", "montage", "build", "coldview"):
        out.append(f"- {name}: {art[name]['meta'].get('drops') or 'none'}")
    out.append(f"- rank exclusions: {art['rank']['meta']['excluded_by_gate'] or 'none'}")
    out += ["", "## Failures", ""]
    fails = [
        (s, f) for s in ("bible", "propose", "montage", "coldview") for f in art[s]["failures"]
    ]
    out += [f"- {s}: {f['error']}" for s, f in fails] or ["- none"]
    out += ["", "## Tokens, cost and time per stage", ""]
    out += ["| stage | calls | in tokens | out tokens | cost usd | cache hits | failed | seconds |"]
    out += ["|---|---|---|---|---|---|---|---|"]
    use = _usage_by_stage(ctx)
    for s in ("bible", "propose", "montage", "coldview", "judge"):
        u = use.get(s)
        if u:
            secs = man.get(s, {}).get("seconds", "-")
            cells = [s, u["calls"], f"{u['in']:,}", f"{u['out']:,}", f"{u['cost']:.4f}",
                     u["hits"], u["failed"], secs]  # fmt: skip
            out.append("| " + " | ".join(str(x) for x in cells) + " |")
    out += ["", "## Profiles and models", ""]
    for st, prof in ctx.cfg.stages.items():
        out.append(f"- {st}: {prof} ({ctx.cfg.profiles[prof].model})")
    cm = art["coldview"]["meta"]
    out.append(f"- cold view answered {cm['answered']} of {cm['clips']} clips")
    return "\n".join(out) + "\n"


async def run(ctx: Ctx) -> dict:
    ranked = [Promo.model_validate(x) for x in ctx.artifacts["rank"]["items"]]
    promos = collapse_variants(ranked)  # promos.json, promos.csv and report.md list one variant
    bible = Bible.model_validate(ctx.artifacts["bible"]["items"][0])
    promos, pstats = polish(promos, ctx.full_show, bible)
    ctx.artifacts["report_polish"] = pstats
    write_json(ctx.out_dir / "promos.json", [p.model_dump() for p in promos])
    write_atomic(ctx.out_dir / "promos.csv", csv_text(promos).encode())
    write_atomic(ctx.out_dir / "report.md", markdown(ctx, promos).encode())
    meta = {"promos": len(promos), "variants_collapsed": len(ranked) - len(promos),
            "polish": pstats,
            "files": ["promos.json", "promos.csv", "report.md"]}  # fmt: skip
    return {"meta": meta, "items": [], "failures": []}  # fmt: skip


def load(ctx: Ctx, artifact: dict) -> None:
    return None
