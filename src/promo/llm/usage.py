"""Today's usage per profile, from every outputs/*/run_log.jsonl (daily quota awareness)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from promo.config import Config
from promo.llm.ledger import is_provider_call


@dataclass
class DayUsage:
    calls: int = 0
    tokens: int = 0


def day_start(now: datetime, tz_name: str) -> datetime:
    """Start of the provider's current day (midnight in its reset timezone), as UTC."""
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def usage_today(
    cfg: Config, outputs_dir: str | Path = "outputs", now: datetime | None = None
) -> dict[str, DayUsage]:
    now = now or datetime.now(UTC)
    starts = {n: day_start(now, p.reset_tz).timestamp() for n, p in cfg.profiles.items()}
    default_start = day_start(now, "UTC").timestamp()
    out: dict[str, DayUsage] = {}
    for log in Path(outputs_dir).glob("*/run_log.jsonl"):
        for raw in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not is_provider_call(rec):
                continue
            if rec.get("ts", 0) < starts.get(rec["profile"], default_start):
                continue
            u = out.setdefault(rec["profile"], DayUsage())
            u.calls += 1
            u.tokens += rec.get("in_tokens", 0) + rec.get("out_tokens", 0)
    return out
