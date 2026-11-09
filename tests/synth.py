"""Tiny builder for hand-made shows with exact timings (invented text only)."""

from __future__ import annotations

from promo.parse.cuts import safe_cuts
from promo.schema import Episode, Line, ParseReport, Show


def mkep(num: int, spec: list[tuple], duration: float, offset: float = 0.0):
    """spec items: (label, kind, start, end, text). Returns (Episode, {label: Line})."""
    rows = sorted(spec, key=lambda r: r[2])
    lines, labels = [], {}
    for i, (label, kind, a, b, text) in enumerate(rows, 1):
        ln = Line(
            id=f"E{num:02d}-L{i:03d}", ep=num, scene=1, idx=i, kind=kind,
            speaker_raw="MIRA" if kind == "dialogue" else None, start=a, end=b,
            gstart=offset + a, gend=offset + b, text=text,
        )  # fmt: skip
        lines.append(ln)
        labels[label] = ln
    ep = Episode(num=num, file="x", duration=duration, offset=offset, lines=lines)
    ep.cuts = safe_cuts(lines, duration)
    return ep, labels


def mkshow(*eps: Episode) -> Show:
    return Show(name="synth", episodes=list(eps), report=ParseReport())


def scene1():
    """Episode 1, 120 s. Cuts at 0,30,33,45,46.5,48,49,52,56,60,70,73,80,84,90,120."""
    spec = [
        ("s0", "dialogue", 30, 33, "The harbor lamp is out again."),
        ("pre", "action", 48, 50, "Mira steps back from the rail."),
        ("h", "dialogue", 49, 52, "You lied to the whole town."),
        ("d1", "dialogue", 56, 60, "Nobody rows out after midnight."),
        ("c", "dialogue", 70, 73, "Who sent the lantern signal?"),
        ("after", "dialogue", 80, 84, "Close the gate behind you."),
    ]
    for a, b in [(45, 46.5), (46.5, 48), (48, 49.5), (60, 70), (70, 80), (80, 90), (90, 120)]:
        spec.append((f"cam{a}", "camera", a, b, "Wide shot of the dock."))
    return mkep(1, spec, 120.0)
