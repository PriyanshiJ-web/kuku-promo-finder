"""Rough planned LLM usage per stage (upper bounds), used by `promo estimate`."""

from __future__ import annotations

import math

from promo.config import Config
from promo.schema import Show


def planned_usage(show: Show, cfg: Config) -> dict[str, tuple[int, int]]:
    """stage -> (calls, tokens). Tokens are in plus out, rough on purpose."""
    p = cfg.params
    n = len(show.episodes)
    total = show.report.est_tokens_compact
    secs = max(show.report.total_seconds, 1.0)
    clip = int(total / secs * 40) + 80  # tokens of a ~40 s clip transcript
    chunks = math.ceil(n / p.chunk_eps)
    budget = int(cfg.profile_for("bible").max_input_tokens * 0.8)
    if total + 2000 <= budget:
        bible = (1, total + 2000 + 4000)
    else:
        parts = math.ceil(total / max(budget // 2, 1))
        bible = (parts + 1, total + 2000 + parts * 3000 + 4000)
    cands = n * p.max_props_per_ep * 2  # tight and loose variants
    cold_calls = math.ceil(cands / p.coldview_batch)
    judge_calls = p.swiss_rounds * (p.top_k_pairwise // 2) * 2
    return {
        "bible": bible,
        "propose": (
            chunks,
            int(total * (1 + 1 / p.chunk_eps)) + chunks * 3000 + n * p.max_props_per_ep * 130,
        ),
        "montage": (1, 3000 + chunks * 5 * 60 + 1200),
        "coldview": (cold_calls, cands * (clip + 120) + cold_calls * 400),
        "judge": (judge_calls, judge_calls * (2 * clip + 290)),
    }
