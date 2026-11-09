"""Stage runner: manifest, hash-skip, atomic writes (spec section 5)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from promo.config import Config, Params
from promo.llm.cache import Cache
from promo.llm.client import LLM
from promo.llm.ledger import Ledger
from promo.llm.providers import Provider
from promo.parse import parse_folder
from promo.schema import Bible, Show

ORDER = ["parse", "bible", "propose", "montage", "build", "gates", "coldview", "rank", "report"]
DEPS = {
    "parse": [], "bible": ["parse"], "propose": ["parse", "bible"], "montage": ["bible", "propose"],
    "build": ["parse", "propose", "montage"], "gates": ["build", "bible"],
    "coldview": ["build", "gates"], "rank": ["build", "gates", "coldview"],
    "report": ["rank", "propose", "montage", "bible", "gates", "coldview", "build"],
}  # fmt: skip


class StageAbort(Exception):
    """A stage cannot continue, with a message for the human."""


def parse_episode_spec(spec: str | None) -> set[int] | None:
    """'1-5', '3', '1,4,6-8' -> set of episode numbers. None means all."""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_json(obj) -> str:
    return sha_bytes(json.dumps(obj, sort_keys=True, default=str).encode())


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json(path: Path, obj) -> str:
    data = (json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()
    write_atomic(path, data)
    return sha_bytes(data)


@dataclass
class Ctx:
    name: str
    folder: Path
    out_dir: Path
    full_show: Show  # every episode, used for lookahead
    show: Show  # the selected episodes (--episodes)
    cfg: Config
    llm: LLM
    ledger: Ledger
    episodes_spec: str | None = None
    offline: bool = False
    artifacts: dict[str, dict] = field(default_factory=dict)
    bible: Bible | None = None
    alias_map: dict[str, str] = field(default_factory=dict)

    @property
    def params(self) -> Params:
        return self.cfg.params


@dataclass
class StageReport:
    name: str
    status: str  # ran | skipped
    seconds: float = 0.0
    calls: int = 0
    tokens: int = 0
    failures: int = 0


def make_ctx(
    folder: str | Path,
    name: str,
    cfg: Config,
    episodes_spec: str | None = None,
    *,
    providers: dict[str, Provider] | None = None,
    outputs_root: str | Path = "outputs",
    cache_path: str | Path = ".cache/llm.sqlite",
    prompts_dir: Path | None = None,
    offline: bool = False,
    out_suffix: str | None = None,
    **llm_kw,
) -> Ctx:
    full = parse_folder(folder, name)
    sel = parse_episode_spec(episodes_spec)
    eps = [e for e in full.episodes if sel is None or e.num in sel]
    if not eps:
        raise StageAbort(f"--episodes {episodes_spec!r} matches no episode in {folder}")
    out_dir = Path(outputs_root) / (f"{name}__{out_suffix}" if out_suffix else name)
    ledger = Ledger(out_dir / "run_log.jsonl", cfg.params.max_calls, cfg.params.max_tokens)
    llm = LLM(cfg, ledger, Cache(cache_path), providers, prompts_dir=prompts_dir, **llm_kw)
    return Ctx(name, Path(folder), out_dir, full, full.model_copy(update={"episodes": eps}),
               cfg, llm, ledger, episodes_spec, offline)  # fmt: skip


def _stage_module(name: str):
    from promo.stages import bible, build, coldview, gates, montage, parse, propose, rank, report

    mods = {
        "parse": parse,
        "bible": bible,
        "propose": propose,
        "montage": montage,
        "build": build,
        "gates": gates,
        "coldview": coldview,
        "rank": rank,
        "report": report,
    }
    return mods[name]


def load_manifest(out_dir: Path) -> dict:
    p = out_dir / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {"stages": {}}


async def run_pipeline(
    ctx: Ctx, stages: list[str] | None = None, force: set[str] | None = None
) -> list[StageReport]:
    force = force or set()
    wanted = stages or ORDER
    manifest = load_manifest(ctx.out_dir)
    manifest.update(folder=str(ctx.folder), episodes=ctx.episodes_spec, offline=ctx.offline,
                    name=ctx.name)  # fmt: skip
    reports: list[StageReport] = []
    for name in ORDER:
        if name not in wanted:
            continue
        mod = _stage_module(name)
        path = ctx.out_dir / f"{name}.json"
        upstream = {d: manifest["stages"].get(d, {}).get("artifact_sha") for d in DEPS[name]}
        h = sha_json({"inputs": mod.inputs(ctx), "upstream": upstream})
        entry = manifest["stages"].get(name, {})
        if entry.get("hash") == h and path.exists() and name not in force:
            ctx.artifacts[name] = json.loads(path.read_text())
            mod.load(ctx, ctx.artifacts[name])
            reports.append(StageReport(name, "skipped"))
            continue
        if hasattr(mod, "check_ready") and not ctx.offline:
            mod.check_ready(ctx)
        calls0, toks0, t0 = ctx.ledger.calls, ctx.ledger.tokens, time.monotonic()
        art = await mod.run(ctx)
        art_sha = write_json(path, art)
        ctx.artifacts[name] = art
        mod.load(ctx, art)
        secs = round(time.monotonic() - t0, 2)
        manifest["stages"][name] = {
            "hash": h, "artifact_sha": art_sha, "seconds": secs,
            "calls": ctx.ledger.calls - calls0, "tokens": ctx.ledger.tokens - toks0,
        }  # fmt: skip
        write_json(ctx.out_dir / "manifest.json", manifest)
        reports.append(
            StageReport(
                name, "ran", secs, ctx.ledger.calls - calls0, ctx.ledger.tokens - toks0,
                len(art.get("failures", [])),
            )
        )  # fmt: skip
    write_json(ctx.out_dir / "manifest.json", manifest)
    return reports
