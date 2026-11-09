"""run_log.jsonl ledger and budget guard. Logs ids, counts and costs only, never script text."""

from __future__ import annotations

import json
import time
from pathlib import Path


class BudgetExceeded(Exception):
    pass


FIELDS = (
    "ts", "stage", "profile", "model", "prompt_name", "in_tokens", "out_tokens", "cost_usd",
    "latency_s", "cache_hit", "attempt", "ok", "error",
)  # fmt: skip


class Ledger:
    def __init__(
        self, path: str | Path | None, max_calls: int | None = None, max_tokens: int | None = None
    ):
        self.path = Path(path) if path else None
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.calls = 0  # provider calls made by this process (cache hits excluded)
        self.tokens = 0
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, count: bool = True, **kw) -> dict:
        rec = {
            "ts": round(time.time(), 3), "stage": "", "profile": "", "model": "",
            "prompt_name": "", "in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0,
            "latency_s": 0.0, "cache_hit": False, "attempt": 1, "ok": True, "error": None,
        }  # fmt: skip
        rec.update(kw)
        if count and not rec["cache_hit"]:
            self.calls += 1
            self.tokens += rec["in_tokens"] + rec["out_tokens"]
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({k: rec[k] for k in FIELDS}) + "\n")
        return rec

    def check_budget(self, planned_calls: int, planned_tokens: int = 0) -> None:
        if self.max_calls is not None and self.calls + planned_calls > self.max_calls:
            raise BudgetExceeded(
                f"planned {planned_calls} calls on top of {self.calls} used exceeds "
                f"max_calls={self.max_calls}"
            )
        if self.max_tokens is not None and self.tokens + planned_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"planned {planned_tokens} tokens on top of {self.tokens} used exceeds "
                f"max_tokens={self.max_tokens}"
            )


# Failed attempts that never reached quota accounting (nothing was served)
NO_QUOTA_ERRORS = {"connection_error", "internal_server_error", "service_unavailable",
                   "bad_gateway", "gateway_timeout"}  # fmt: skip


def is_provider_call(rec: dict) -> bool:
    """True for ledger lines that represent a real request to a provider (not annotations)."""
    if rec.get("cache_hit") or not rec.get("profile"):
        return False
    err = rec.get("error") or ""
    if err in NO_QUOTA_ERRORS:
        return False
    return not err.startswith(("invalid output", "call failed", "skipped"))
