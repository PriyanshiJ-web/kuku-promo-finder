"""`promo eval`: judge the tool's top 10 against baselines and perturbations, write eval.json/md."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from promo.evaluation import baselines, metrics, perturb
from promo.runner import Ctx, write_atomic, write_json
from promo.schema import Candidate, Promo
from promo.stages.build import BuildParams
from promo.stages.pairwise import judge_pair

LABELS = Path("eval/labels/labels.csv")


def load_top(path: Path, n: int = 10) -> list[Candidate]:
    promos = [Promo.model_validate(x) for x in json.loads(path.read_text())]
    return [p.candidate for p in sorted(promos, key=lambda p: p.rank)[:n]]


async def duel(ctx: Ctx, left: list[Candidate], right: list[Candidate]) -> dict:
    """Pair by rank, judge both orders. Score is the left side's mean (1 win, 0.5 split, 0 loss)."""
    pairs = list(zip(left, right, strict=False))
    ctx.ledger.check_budget(2 * len(pairs), 3000 * len(pairs))
    res = await asyncio.gather(*(judge_pair(ctx.llm, a, b, ctx.full_show) for a, b in pairs))
    scored = [r for r in res if r.valid]
    both = [r for r in scored if r.valid == 2]
    return {
        "pairs": len(pairs),
        "judged": len(scored),
        "left_win_rate": metrics.mean([r.score_a for r in scored]),
        "split_rate": metrics.mean([0.0 if r.agree else 1.0 for r in both]),
    }


async def frozen_single_prompt(ctx: Ctx, show, prm, refresh: bool):
    """The single_prompt baseline is a model reply, and an invalid first reply is never cached, so
    asking again can give a different clip set. It is therefore saved and reused."""
    path = ctx.out_dir / "eval_baselines.json"
    if path.exists() and not refresh:
        saved = json.loads(path.read_text())
        return [Candidate.model_validate(c) for c in saved["single_prompt"]], saved["dropped"]
    cands, dropped = await baselines.single_prompt(ctx.llm, show, prm)
    if cands:
        write_json(path, {"single_prompt": [c.model_dump() for c in cands], "dropped": dropped})
    return cands, dropped


async def perturbation_eval(ctx: Ctx, top: list[Candidate]) -> dict:
    """Judge each original against a buried-hook version (early) and an answer-shown one (late)."""
    res: dict = {}
    for kind, fn in (("early", perturb.early), ("late", perturb.late)):
        pairs = [(c, fn(c, ctx.full_show)) for c in top]
        pairs = [(a, b) for a, b in pairs if b is not None]
        d = await duel(ctx, [a for a, _ in pairs], [b for _, b in pairs])
        res[kind] = {**d, "original_preferred": d["left_win_rate"]}
    return res


def compare_with_main(ctx: Ctx, diag: dict) -> dict | None:
    """Numbers of the main eval.json next to this run's, for the same top 10 and perturbations."""
    path = ctx.out_dir / "eval.json"
    if not path.exists():
        return None
    main = json.loads(path.read_text())
    return {"judge_profile": main.get("judge_profile", "unknown"),
            "judge_model": main.get("judge_model", "unknown"),
            "perturbation": main["perturbation"]}  # fmt: skip


def perturbation_markdown(o: dict) -> str:
    out = [
        f"# Perturbation diagnostic: {o['show']}",
        "",
        "Same top 10 and the same perturbed clips, judged by two different models.",
        "",
    ]
    cmp = o.get("compared_with")
    cols = [("this run", o["judge_profile"], o["judge_model"], o["perturbation"])]
    if cmp:
        main = ("main eval", cmp["judge_profile"], cmp["judge_model"], cmp["perturbation"])
        cols.insert(0, main)
    head = [
        "judge",
        "profile",
        "model",
        "perturbation",
        "pairs judged",
        "original preferred",
        "split rate",
    ]
    out += [_row(head), _row(["---"] * len(head))]
    for label, prof, model, pert in cols:
        for kind, d in pert.items():
            out.append(_row([label, prof, model, kind, f"{d['judged']} of {d['pairs']}",
                             d["original_preferred"], d["split_rate"]]))  # fmt: skip
    if o["failures"]:
        out += ["", "## Failures", ""] + [f"- {f['error']}" for f in o["failures"]]
    return "\n".join(out) + "\n"


async def run_eval(
    ctx: Ctx,
    use_llm: bool = True,
    refresh_baselines: bool = False,
    only: str | None = None,
    out_name: str = "eval",
) -> dict:
    prm = BuildParams.from_ctx(ctx)
    show = ctx.full_show
    top = load_top(ctx.out_dir / "promos.json")
    jp = ctx.cfg.profile_for("judge")
    out: dict = {
        "show": ctx.name,
        "top_n": len(top),
        "judge_profile": jp.name,
        "judge_model": jp.model,
    }
    n0 = len(ctx.llm.failures)
    if only == "perturbation":
        out["perturbation"] = await perturbation_eval(ctx, top)
        out["compared_with"] = compare_with_main(ctx, out)
        out["failures"] = ctx.llm.failures[n0:]
        write_json(ctx.out_dir / f"{out_name}.json", out)
        write_atomic(ctx.out_dir / f"{out_name}.md", perturbation_markdown(out).encode())
        return out
    sets = {
        "random_45": baselines.random_45(show, prm),
        "last_45": baselines.last_45(show, prm),
        "heuristic": baselines.heuristic(show, prm, ctx.params.max_props_per_ep),
    }
    if use_llm:
        sets["single_prompt"], dropped = await frozen_single_prompt(
            ctx, show, prm, refresh_baselines
        )
        out["single_prompt_dropped_proposals"] = dropped
    out["baseline_sizes"] = {k: len(v) for k, v in sets.items()}
    out["win_rate_vs_baselines"] = {}
    out["perturbation"] = {}
    if use_llm:
        for name, cands in sets.items():
            out["win_rate_vs_baselines"][name] = await duel(ctx, top, cands)
        out["perturbation"] = await perturbation_eval(ctx, top)
    else:
        out["note"] = "LLM judging skipped (--no-llm), code metrics only"
    out["precision_at_10"] = metrics.precision_at_k(top, metrics.load_labels(LABELS))
    out["diversity"] = metrics.diversity(top, show)
    out["spread"] = metrics.spread(top, show)
    out["swap_overlap"] = {}
    for alt in sorted(ctx.out_dir.parent.glob(f"{ctx.name}__*/promos.json")):
        out["swap_overlap"][alt.parent.name] = metrics.swap_overlap(top, load_top(alt), show)
    out["failures"] = ctx.llm.failures[n0:]
    write_json(ctx.out_dir / "eval.json", out)
    write_atomic(ctx.out_dir / "eval.md", markdown(out).encode())
    return out


def _row(cells: list) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def markdown(o: dict) -> str:
    out = [
        f"# Eval: {o['show']}",
        "",
        f"Tool top {o['top_n']} against baselines (judge, both orders).",
        "",
    ]
    if o["win_rate_vs_baselines"]:
        out += ["## Win rate of the tool's top 10", ""]
        out += [
            _row(["baseline", "size", "pairs judged", "tool win rate", "split rate"]),
            _row(["---"] * 5),
        ]
        for k, d in o["win_rate_vs_baselines"].items():
            out.append(_row([k, o["baseline_sizes"][k], f"{d['judged']} of {d['pairs']}",
                             d["left_win_rate"], d["split_rate"]]))  # fmt: skip
        out += ["", "## Perturbation (the judge should prefer the original)", ""]
        out += [_row(["perturbation", "pairs judged", "original preferred"]), _row(["---"] * 3)]
        for k, d in o["perturbation"].items():
            out.append(_row([k, f"{d['judged']} of {d['pairs']}", d["original_preferred"]]))
        out.append("")
    else:
        out += [o.get("note", ""), ""]
    out += [
        "## Other metrics",
        "",
        f"- precision@10 against hand labels: {o['precision_at_10']}",
        f"- diversity (1 minus mean pairwise IoU of the top 10): {o['diversity']}",
        f"- hook types in the top 10: {o['spread']['hook_types']}",
        f"- episodes in the top 10: {o['spread']['episodes']}",
    ]
    for k, v in o["swap_overlap"].items():
        out.append(f"- model swap overlap with {k}: {v}")
    if not o["swap_overlap"]:
        out.append("- model swap overlap: no swap run found")
    if o["failures"]:
        out += ["", "## Failures", ""] + [f"- {f['error']}" for f in o["failures"]]
    return "\n".join(out) + "\n"
