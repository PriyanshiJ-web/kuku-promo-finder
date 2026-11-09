"""Safe cut points (spec section 2)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from promo.schema import Episode, Line

TOL = 0.1


def safe_cuts(lines: list[Line], duration: float) -> list[float]:
    """Block boundaries (plus 0 and duration) that fall strictly inside no dialogue line."""
    spans = sorted((ln.start, ln.end) for ln in lines if ln.kind == "dialogue")
    bounds = {0.0, round(duration, 2)}
    for ln in lines:
        bounds.add(round(ln.start, 2))
        bounds.add(round(ln.end, 2))
    out = []
    for t in sorted(bounds):
        if t < 0 or t > duration + 1e-9:
            continue
        if any(s + TOL < t < e - TOL for s, e in spans):
            continue
        out.append(t)
    return out


def cut_at_or_before(ep: Episode, t: float) -> float:
    cuts = ep.cuts
    i = bisect_right(cuts, t + 1e-9)
    return cuts[i - 1] if i else cuts[0]


def cut_at_or_after(ep: Episode, t: float) -> float:
    cuts = ep.cuts
    i = bisect_left(cuts, t - 1e-9)
    return cuts[i] if i < len(cuts) else cuts[-1]


def cuts_between(ep: Episode, a: float, b: float) -> list[float]:
    return [c for c in ep.cuts if a - 1e-9 <= c <= b + 1e-9]
