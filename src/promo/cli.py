"""promo command line (phase 1: inspect, estimate)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from promo.config import ConfigError, load_config, load_dotenv
from promo.evaluation.run import run_eval
from promo.llm.client import LLM
from promo.llm.ledger import BudgetExceeded, Ledger
from promo.llm.usage import DayUsage, usage_today
from promo.parse import compact, est_tokens, parse_folder
from promo.parse.render import render_text
from promo.plan import planned_usage
from promo.runner import ORDER, StageAbort, load_manifest, make_ctx, run_pipeline

app = typer.Typer(add_completion=False, help="Find ranked promo clips in a microdrama.")
console = Console()


def _mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


@app.command()
def inspect(folder: Path) -> None:
    """Parse a show folder and print the parse report."""
    show = parse_folder(folder)
    r = show.report
    t = Table(title=f"Parse report: {show.name}")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("episodes", str(r.episodes))
    t.add_row("total duration", f"{_mmss(r.total_seconds)} ({r.total_seconds / 60:.1f} min)")
    for k, v in sorted(r.blocks_by_kind.items()):
        t.add_row(f"blocks: {k}", str(v))
    t.add_row("coverage", f"{r.coverage:.4f}")
    t.add_row("lines missing time", str(r.lines_missing_time))
    t.add_row("end before start", str(r.end_before_start))
    t.add_row("out of scene range", str(r.out_of_scene))
    t.add_row("unknown lines", str(r.unknown_lines))
    t.add_row("est tokens full", f"{r.est_tokens_full:,}")
    t.add_row("est tokens compact", f"{r.est_tokens_compact:,}")
    console.print(t)
    for w in r.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
    if r.coverage < 0.95:
        console.print("[red]coverage below 0.95: the run stage would abort[/red]")
        raise typer.Exit(1)


@app.command()
def estimate(
    folder: Path,
    profile_map: Annotated[
        str | None, typer.Option(help="stage=profile,... override of config/pipeline.yaml")
    ] = None,
) -> None:
    """Token counts per episode and planned LLM calls per stage (upper bounds)."""
    show = parse_folder(folder)
    try:
        cfg = load_config(profile_map=profile_map)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    p = cfg.params.model_dump()
    t = Table(title="Tokens per episode")
    for c in ("ep", "full", "compact"):
        t.add_column(c, justify="right")
    for ep in show.episodes:
        t.add_row(
            str(ep.num),
            f"{est_tokens(render_text(ep, True)):,}",
            f"{est_tokens(compact(ep)):,}",
        )
    t.add_row("TOTAL", f"{show.report.est_tokens_full:,}", f"{show.report.est_tokens_compact:,}")
    console.print(t)
    raw = sum(len(f.read_text(errors="replace")) for f in Path(folder).glob("*.txt"))
    console.print(f"raw files: {est_tokens('x' * raw):,} tokens (chars/4, includes timing lines)")
    plan = planned_usage(show, cfg)
    c = Table(title="Planned usage per stage (rough upper bounds)")
    for col, right in (("stage", False), ("profile", False), ("calls", True), ("tokens", True)):
        c.add_column(col, justify="right" if right else "left")
    for stage, (calls, toks) in plan.items():
        c.add_row(stage, cfg.stages.get(stage, "-"), str(calls), f"{toks:,}")
    tot_calls = sum(v[0] for v in plan.values())
    tot_toks = sum(v[1] for v in plan.values())
    c.add_row("TOTAL", "", str(tot_calls), f"{tot_toks:,}")
    console.print(c)
    console.print(f"budget: max_calls={p.get('max_calls')} max_tokens={p.get('max_tokens'):,}")
    _daily_table(cfg, plan)


def _daily_table(cfg, plan) -> None:
    used = usage_today(cfg)
    by_prof: dict[str, list[int]] = {}
    for stage, (calls, toks) in plan.items():
        acc = by_prof.setdefault(cfg.stages[stage], [0, 0])
        acc[0] += calls
        acc[1] += toks
    d = Table(title="Daily quota check (informational, nothing is blocked)")
    for col in (
        "profile",
        "planned calls",
        "used today",
        "rpd",
        "planned tokens",
        "used today ",
        "tpd",
    ):
        d.add_column(col, justify="right")
    for name, (calls, toks) in by_prof.items():
        pr, u = cfg.profiles[name], used.get(name, DayUsage())
        over_c = pr.rpd and u.calls + calls > pr.rpd
        over_t = pr.tpd and u.tokens + toks > pr.tpd
        cs, ts = ("red" if over_c else "green"), ("red" if over_t else "green")
        d.add_row(
            name, f"[{cs}]{calls}[/{cs}]", str(u.calls), str(pr.rpd or "-"),
            f"[{ts}]{toks:,}[/{ts}]", f"{u.tokens:,}", f"{pr.tpd:,}" if pr.tpd else "-",
        )  # fmt: skip
    console.print(d)
    console.print(
        "[dim]limits come from config/models.yaml, a dash means no limit configured[/dim]"
    )


class _Ping(BaseModel):
    ok: bool
    word: str = ""


@app.command()
def ping(profile: Annotated[str, typer.Option(help="profile name from models.yaml")]) -> None:
    """One tiny JSON call to check a profile works. Prints latency and tokens."""
    load_dotenv()
    try:
        cfg = load_config()
        if profile not in cfg.profiles:
            raise ConfigError(f"unknown profile '{profile}' ({sorted(cfg.profiles)})")
        cfg.stages["ping"] = profile
        cfg.check_ready(["ping"])
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    ledger = Ledger("outputs/_ping/run_log.jsonl")
    p = cfg.profiles[profile]
    note = ""
    t0 = time.monotonic()
    llm = LLM(cfg, ledger)  # no cache: a ping must hit the network
    out = asyncio.run(llm.call_json("ping", "ping", {"word": "kuku"}, _Ping, max_tokens=200))
    if out is None and p.extra_body:
        reasons = [f["error"] for f in llm.failures]
        cfg.profiles[profile] = p.model_copy(update={"extra_body": {}})
        llm = LLM(cfg, ledger)
        out = asyncio.run(llm.call_json("ping", "ping", {"word": "kuku"}, _Ping, max_tokens=200))
        note = (
            f" extra_body {p.extra_body} FAILED ({reasons[:1]}), worked without it: "
            "remove extra_body from this profile in models.yaml"
            if out
            else ""
        )
    dt = time.monotonic() - t0
    if out is None:
        console.print(f"[red]ping failed:[/red] {llm.failures}")
        raise typer.Exit(1)
    served = cfg.profiles[llm.last_profile]
    if served.name != profile:
        note += f" WARNING: {profile} failed, the reply came from fallback {served.name}"
    console.print(
        f"[green]ok[/green] profile={served.name} model={served.model} latency={dt:.2f}s "
        f"calls={ledger.calls} tokens={ledger.tokens}{note}"
    )


def _fail(e: Exception) -> typer.Exit:
    console.print(f"[red]{type(e).__name__}:[/red] {e}")
    return typer.Exit(1)


def _remaining(cfg, stages: list[str]) -> None:
    used = usage_today(cfg)
    t = Table(title="Remaining quota today, per profile used")
    for c in ("profile", "stages", "requests left", "tokens left"):
        t.add_column(c, justify="right")
    names: dict[str, list[str]] = {}
    for st in stages:
        if st in cfg.stages:
            names.setdefault(cfg.stages[st], []).append(st)
    for name, sts in names.items():
        p, u = cfg.profiles[name], used.get(name, DayUsage())
        rleft = f"{max(p.rpd - u.calls, 0)} of {p.rpd}" if p.rpd else "no limit"
        tleft = f"{max(p.tpd - u.tokens, 0):,} of {p.tpd:,}" if p.tpd else "no limit"
        t.add_row(name, ",".join(sts), rleft, tleft)
    console.print(t)


def _print_reports(reports, ctx) -> None:
    t = Table(title=f"Run: {ctx.name}")
    for c in ("stage", "status", "seconds", "calls", "tokens", "failures"):
        t.add_column(c, justify="right" if c not in ("stage", "status") else "left")
    for r in reports:
        t.add_row(
            r.name, r.status, f"{r.seconds:.1f}", str(r.calls), f"{r.tokens:,}", str(r.failures)
        )
    console.print(t)
    bible = ctx.artifacts.get("bible")
    if bible:
        m = bible["meta"]
        console.print(
            f"bible: {m['n_characters']} characters, {m['n_tags']} raw tags, "
            f"{len(m['unresolved_tags'])} unresolved tags, degraded={m['degraded']}"
        )
    prop = ctx.artifacts.get("propose")
    if prop:
        m = prop["meta"]
        console.print(f"propose: {m['kept']} kept of {m['raw_proposals']}, drops={m['drops']}")
    bld = ctx.artifacts.get("build")
    if bld:
        m = bld["meta"]
        console.print(
            f"build: {m['kept']} kept of {m['built']}, {m['by_kind_variant']}, drops={m['drops']}"
        )
        console.print(f"durations (5 s buckets): {m['duration_hist_5s']}")
    gts = ctx.artifacts.get("gates")
    if gts:
        m = gts["meta"]
        console.print(
            f"gates: hard pass {m['hard_pass']} of {m['candidates']}, passes={m['pass_counts']}"
        )
    cv = ctx.artifacts.get("coldview")
    if cv:
        m = cv["meta"]
        console.print(f"coldview: answered {m['answered']} of {m['clips']}, drops={m['drops']}")
    rk = ctx.artifacts.get("rank")
    if rk:
        console.print(
            f"rank: {rk['meta']['ranked']} ranked, excluded={rk['meta']['excluded_by_gate']}"
        )
    mont = ctx.artifacts.get("montage")
    if mont:
        m = mont["meta"]
        console.print(f"montage: {m['kept']} kept of {m['raw']}, drops={m['drops']}")


def _go(ctx, stages, force) -> None:
    try:
        if not ctx.offline:
            _remaining(ctx.cfg, stages)
        reports = asyncio.run(run_pipeline(ctx, stages, force))
    except (StageAbort, ConfigError, BudgetExceeded) as e:
        raise _fail(e) from e
    _print_reports(reports, ctx)
    _check_not_empty(ctx, stages)


def _check_not_empty(ctx, stages) -> None:
    """A run that ends with no promos must not look like a success."""
    f = ctx.out_dir / "promos.json"
    if "report" not in stages or not f.exists() or json.loads(f.read_text()):
        return
    fails = [ctx.artifacts.get(s, {}).get("failures", []) for s in ("bible", "propose")]
    why = [x["error"] for group in fails for x in group]
    console.print("[red]No promos were produced.[/red]")
    for line in dict.fromkeys(why):
        console.print(f"  model failure: {line[:160]}")
    console.print(
        "Check the keys in .env and the profile limits (`promo ping --profile NAME`), "
        "or add --offline to run with keyword rules only."
    )
    raise typer.Exit(1)


@app.command()
def run(
    folder: Path,
    show: Annotated[str, typer.Option(help="name for outputs/<show>/")],
    episodes: Annotated[str | None, typer.Option(help="e.g. 1-5 or 1,3,7-9")] = None,
    offline: Annotated[bool, typer.Option(help="no LLM: keyword heuristics only")] = False,
    profile_map: Annotated[str | None, typer.Option(help="stage=profile,... override")] = None,
    force: Annotated[list[str] | None, typer.Option(help="rerun this stage, repeatable")] = None,
    out_suffix: Annotated[
        str | None, typer.Option(help="write to outputs/<show>__<suffix>/ (model swap runs)")
    ] = None,
) -> None:
    """Run the pipeline stages in order (hash-skipped when nothing changed)."""
    load_dotenv()
    try:
        cfg = load_config(profile_map=profile_map)
        ctx = make_ctx(folder, show, cfg, episodes, offline=offline, out_suffix=out_suffix)
    except (ConfigError, StageAbort) as e:
        raise _fail(e) from e
    _go(ctx, ORDER, set(force or []))


@app.command()
def stage(
    name: Annotated[str, typer.Argument(help=f"one of {ORDER}")],
    show: Annotated[str, typer.Option(help="show name used by `promo run`")],
    profile_map: Annotated[str | None, typer.Option(help="stage=profile,... override")] = None,
) -> None:
    """Force-rerun one stage (upstream stages are loaded from their saved artifacts)."""
    load_dotenv()
    if name not in ORDER:
        console.print(f"[red]unknown stage '{name}', choose from {ORDER}[/red]")
        raise typer.Exit(2)
    man = load_manifest(Path("outputs") / show)
    if not man.get("folder"):
        console.print(f"[red]no previous run for show '{show}', use `promo run` first[/red]")
        raise typer.Exit(2)
    try:
        cfg = load_config(profile_map=profile_map)
        ctx = make_ctx(
            man["folder"], show, cfg, man.get("episodes"), offline=bool(man.get("offline"))
        )
    except (ConfigError, StageAbort) as e:
        raise _fail(e) from e
    _go(ctx, ORDER[: ORDER.index(name) + 1], {name})


@app.command()
def polish(
    show: Annotated[str, typer.Option(help="show name used by `promo run`")],
) -> None:
    """Rebuild promos.json, promos.csv and report.md from the saved stages (no model calls)."""
    from promo.stages import report
    from promo.stages.bible import apply_aliases

    out = Path("outputs") / show
    man = load_manifest(out)
    if not man.get("folder") or not (out / "rank.json").exists():
        console.print(f"[red]no finished run for show '{show}', use `promo run` first[/red]")
        raise typer.Exit(2)
    try:
        ctx = make_ctx(man["folder"], show, load_config(), man.get("episodes"))
        for name in ORDER:
            if (out / f"{name}.json").exists():
                ctx.artifacts[name] = json.loads((out / f"{name}.json").read_text())
        apply_aliases(ctx.full_show, ctx.artifacts["bible"]["meta"]["alias_map"])
        art = asyncio.run(report.run(ctx))
    except (StageAbort, ConfigError) as e:
        raise _fail(e) from e
    console.print(f"promos: {art['meta']['promos']}, polish: {art['meta']['polish']}")


@app.command("eval")
def eval_cmd(
    show: Annotated[str, typer.Option(help="show name used by `promo run`")],
    no_llm: Annotated[bool, typer.Option(help="skip judge calls, code metrics only")] = False,
    refresh_baselines: Annotated[
        bool, typer.Option(help="ask the model for a new single_prompt baseline")
    ] = False,
    only: Annotated[
        str | None, typer.Option(help="'perturbation': only that check, saved apart from eval.json")
    ] = None,
    out_name: Annotated[str, typer.Option(help="file name stem for the result")] = "eval",
    profile_map: Annotated[str | None, typer.Option(help="stage=profile,... override")] = None,
) -> None:
    """Compare the tool's top 10 with baselines and perturbations, write eval.json and eval.md."""
    from promo.stages.bible import apply_aliases

    load_dotenv()
    man = load_manifest(Path("outputs") / show)
    if not man.get("folder") or not (Path("outputs") / show / "promos.json").exists():
        console.print(f"[red]no finished run for show '{show}', use `promo run` first[/red]")
        raise typer.Exit(2)
    try:
        cfg = load_config(profile_map=profile_map)
        if not no_llm:
            cfg.check_ready(["judge", "propose"])
        ctx = make_ctx(man["folder"], show, cfg, man.get("episodes"))
        bible = json.loads((ctx.out_dir / "bible.json").read_text())
        apply_aliases(ctx.full_show, bible["meta"]["alias_map"])
        asyncio.run(
            run_eval(
                ctx,
                use_llm=not no_llm,
                refresh_baselines=refresh_baselines,
                only=only,
                out_name=out_name,
            )
        )
    except (StageAbort, ConfigError, BudgetExceeded) as e:
        raise _fail(e) from e
    stem = out_name if only else "eval"
    console.print((ctx.out_dir / f"{stem}.md").read_text())
    console.print(f"eval written to {ctx.out_dir / (stem + '.json')}")
    return None
