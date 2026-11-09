"""Load and validate config/models.yaml and config/pipeline.yaml (spec section 4)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"


class ConfigError(Exception):
    pass


class Profile(BaseModel):
    name: str = ""
    provider: Literal["openai", "fake"] = "openai"
    base_url: str = ""
    api_key_env: str | None = None
    model: str = "FILL_ME"
    rpm: int = 10
    tpm: int = 100_000
    rpd: int = 500
    tpd: int | None = None  # tokens per day, None means not limited
    reset_tz: str = "UTC"  # timezone where the provider's daily quota resets
    max_input_tokens: int = 100_000
    max_output_tokens: int = 4096
    json_mode: bool = True
    supports_seed: bool = False
    price_in_per_m: float = 0.0
    price_out_per_m: float = 0.0
    fallback: str | list[str] | None = None  # one profile name or an ordered list
    concurrency: int = 2
    extra_body: dict = Field(default_factory=dict)  # passed through, e.g. thinking controls

    @property
    def fallbacks(self) -> list[str]:
        if not self.fallback:
            return []
        return [self.fallback] if isinstance(self.fallback, str) else list(self.fallback)

    def resolved(self) -> Profile:
        """Copy with env:NAME values filled in. Raises ConfigError with a clear message."""
        upd = {}
        for f in ("base_url", "model"):
            val = getattr(self, f)
            if val.startswith("env:"):
                var = val[4:]
                if not os.environ.get(var):
                    raise ConfigError(f"profile '{self.name}': env var {var} is not set")
                upd[f] = os.environ[var]
        return self.model_copy(update=upd)

    def api_key(self) -> str:
        if self.provider == "fake" or not self.api_key_env:
            return "none"
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ConfigError(
                f"profile '{self.name}': env var {self.api_key_env} is not set (see .env.example)"
            )
        return key


class Params(BaseModel):
    min_len: float = 20
    max_len: float = 60
    hook_window: float = 3.0
    min_segment: float = 3.0
    free_eps: int = 10
    chunk_eps: int = 5
    max_props_per_ep: int = 6
    coldview_batch: int = 6
    top_k_pairwise: int = 24
    swiss_rounds: int = 4
    mmr_lambda: float = 0.7
    top_n_report: int = 20
    max_calls: int = 400
    max_tokens: int = 2_000_000


class Config(BaseModel):
    profiles: dict[str, Profile]
    stages: dict[str, str] = Field(default_factory=dict)
    params: Params = Field(default_factory=Params)

    def profile_for(self, stage: str) -> Profile:
        try:
            return self.profiles[self.stages[stage]]
        except KeyError as e:
            raise ConfigError(f"no profile configured for stage '{stage}'") from e

    def check_ready(self, stages: list[str] | None = None) -> None:
        """Fail early, with a clear message, if a profile actually used cannot run."""
        for st in stages or list(self.stages):
            p = self.profile_for(st)
            if p.provider == "fake":
                continue
            if p.model.strip() == "FILL_ME":
                raise ConfigError(
                    f"profile '{p.name}' (stage '{st}'): model is FILL_ME, set it in "
                    "config/models.yaml"
                )
            p.resolved().api_key()


def parse_profile_map(text: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ConfigError(f"bad --profile-map entry '{part}', expected stage=profile")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load_config(
    config_dir: Path | None = None, profile_map: str | dict[str, str] | None = None
) -> Config:
    d = Path(config_dir or os.environ.get("PROMO_CONFIG_DIR") or CONFIG_DIR)
    models = yaml.safe_load((d / "models.yaml").read_text()) or {}
    pipe = yaml.safe_load((d / "pipeline.yaml").read_text()) or {}
    profiles = {
        k: Profile(**{**(v or {}), "name": k}) for k, v in (models.get("profiles") or {}).items()
    }
    cfg = Config(
        profiles=profiles, stages=pipe.get("stages") or {}, params=pipe.get("params") or {}
    )
    pmap = parse_profile_map(profile_map) if isinstance(profile_map, str | None) else profile_map
    for stage, prof in pmap.items():
        if stage not in cfg.stages:
            raise ConfigError(f"--profile-map: unknown stage '{stage}' ({sorted(cfg.stages)})")
        cfg.stages[stage] = prof
    for stage, prof in cfg.stages.items():
        if prof not in cfg.profiles:
            raise ConfigError(f"stage '{stage}' uses unknown profile '{prof}' ({sorted(profiles)})")
    for p in profiles.values():
        for fb in p.fallbacks:
            if fb not in profiles:
                raise ConfigError(f"profile '{p.name}': unknown fallback '{fb}'")
    return cfg


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader. Existing environment variables win."""
    p = Path(path or ".env")
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
