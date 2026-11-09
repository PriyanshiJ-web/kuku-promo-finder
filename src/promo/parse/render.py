"""Prompt rendering (spec section 3)."""

from __future__ import annotations

import math

from promo.schema import Candidate, Episode, Line, Show


def est_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def fmt_time(t: float) -> str:
    """mm:ss.s with the tenth truncated, never rounded up across a second."""
    tenths = int(t * 10 + 1e-6)
    m, rem = divmod(tenths, 600)
    return f"{m:02d}:{rem // 10:02d}.{rem % 10}"


def _unquote(text: str) -> str:
    t = text.strip()
    if len(t) >= 2 and t[0] in '"“' and t[-1] in '"”':
        t = t[1:-1]
    return t


def _speaker(ln: Line) -> str:
    return ln.speaker or ln.speaker_raw or "UNKNOWN"


def render_line(ln: Line, stamp: str) -> str:
    head = f"{ln.id} [{stamp}] " if ln.id else f"[{stamp}] "
    if ln.kind == "dialogue":
        emo = f" ({', '.join(e.lower() for e in ln.emotions)})" if ln.emotions else ""
        voice = f" ({ln.voice})" if ln.voice else ""
        return f'{head}{_speaker(ln)}{voice}{emo}: "{_unquote(ln.text)}"'
    label = {"action": "ACTION", "sfx": "SFX", "camera": "CAMERA"}[ln.kind]
    return f"{head}{label}: {ln.text}"


def render_text(ep: Episode, include_camera: bool) -> str:
    return compact(ep, include_camera)


def compact(ep: Episode, include_camera: bool = False) -> str:
    rows = [
        render_line(ln, fmt_time(ln.start))
        for ln in ep.lines
        if include_camera or ln.kind != "camera"
    ]
    return "\n".join(rows)


def in_segment(ln: Line, start: float, end: float) -> bool:
    if start - 0.05 <= ln.start < end:
        return True
    if ln.kind == "dialogue":
        return False
    return min(ln.end, end) - max(ln.start, start) >= 0.5


def clip_lines(cand: Candidate, show: Show) -> list[tuple[Line, float]]:
    """Non-camera lines a viewer sees, each with its time relative to the clip start."""
    out: list[tuple[Line, float]] = []
    elapsed = 0.0
    for seg in cand.segments:
        for ln in show.ep(seg.ep).lines:
            if ln.kind != "camera" and in_segment(ln, seg.start, seg.end):
                out.append((ln, elapsed + max(0.0, ln.start - seg.start)))
        elapsed += seg.end - seg.start
    return out


OPENING_SECONDS = 3.0
LAST_SECONDS = 5.0


def clip_blocks(cand: Candidate, show: Show) -> tuple[str, str, str]:
    """(OPENING, FULL CLIP, LAST LINES) for the structured judge, all as a cold viewer sees them.

    OPENING holds the lines that start in the first 3.0 s, LAST LINES the lines that are on screen
    during the final 5 s of the clip.
    """
    rows = clip_lines(cand, show)
    total = sum(s.end - s.start for s in cand.segments)

    def fmt(sel: list[tuple[Line, float]]) -> str:
        text = "\n".join(
            render_line(ln.model_copy(update={"id": ""}), f"{t:04.1f}s") for ln, t in sel
        )
        return text or "(no lines)"

    opening = [(ln, t) for ln, t in rows if t <= OPENING_SECONDS]
    last = [(ln, t) for ln, t in rows if t + (ln.end - ln.start) > total - LAST_SECONDS]
    return fmt(opening), clip_text(cand, show), fmt(last)


def clip_text(cand: Candidate, show: Show) -> str:
    """What a cold viewer sees: no episode numbers, no line IDs, times relative to clip start."""
    out: list[str] = []
    last_seg = None
    for ln, t in clip_lines(cand, show):
        seg_no = next(
            i for i, sg in enumerate(cand.segments)
            if sg.ep == ln.ep and in_segment(ln, sg.start, sg.end)
        )  # fmt: skip
        if last_seg is not None and seg_no != last_seg:
            out.append("--- CUT ---")
        last_seg = seg_no
        out.append(render_line(ln.model_copy(update={"id": ""}), f"{t:04.1f}s"))
    return "\n".join(out)
