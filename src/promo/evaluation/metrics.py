"""Metrics for the eval report (spec section 6)."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from promo.parse.reader import parse_time
from promo.schema import Candidate, Show
from promo.stages.build import time_iou

IOU_MATCH = 0.5


def mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def load_labels(path: str | Path) -> list[dict] | None:
    """Rows of eval/labels/labels.csv, or None when the file does not exist (never created here)."""
    p = Path(path)
    if not p.exists():
        return None

    def secs(v: str) -> float:
        return parse_time(v) if ":" in v else float(v)

    rows = []
    for r in csv.DictReader(p.read_text().splitlines()):
        try:
            rows.append({"ep": int(r["episode"]), "start": secs(r["start"]), "end": secs(r["end"])})
        except (KeyError, ValueError):
            continue
    return rows


def span_hit(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Overlap of at least half of the shorter span."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    shorter = min(a[1] - a[0], b[1] - b[0])
    return shorter > 0 and inter >= 0.5 * shorter


def precision_at_k(top: list[Candidate], labels: list[dict] | None, k: int = 10) -> float | str:
    if labels is None:
        return "no labels"
    rows = top[:k]
    hits = sum(
        1 for c in rows
        if any(s.ep == lab["ep"] and span_hit((s.start, s.end), (lab["start"], lab["end"]))
               for s in c.segments for lab in labels)
    )  # fmt: skip
    return round(hits / len(rows), 4) if rows else 0.0


def diversity(top: list[Candidate], show: Show) -> float | None:
    """1 minus the mean pairwise time IoU of the top clips."""
    pairs = [(a, b) for i, a in enumerate(top) for b in top[i + 1 :]]
    if not pairs:
        return None
    return round(1 - sum(time_iou(a, b, show) for a, b in pairs) / len(pairs), 4)


def spread(top: list[Candidate], show: Show) -> dict:
    return {
        "hook_types": dict(Counter(c.hook_type for c in top)),
        "episodes": dict(Counter(show.line(c.hook_id).ep for c in top)),
    }


def swap_overlap(top_a: list[Candidate], top_b: list[Candidate], show: Show) -> float | None:
    """Share of clips in A that have a match (time IoU of at least 0.5) in B."""
    if not top_a or not top_b:
        return None
    hits = sum(1 for a in top_a if any(time_iou(a, b, show) >= IOU_MATCH for b in top_b))
    return round(hits / len(top_a), 4)
