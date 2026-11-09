"""Offline mode: keyword and emotion heuristics instead of a model (spec section 5)."""

from __future__ import annotations

import re

from promo.schema import HookType, Line, Proposal, Show

WINDOW = 45.0
MIN_GAP = 10.0  # hooks closer than this in one episode are the same moment
END_BONUS_WINDOW = 30.0
END_BONUS = 0.15
WORDS = {
    "slap": "violence", "slapped": "violence", "strike": "violence", "struck": "violence",
    "grab": "violence", "grabs": "violence", "grabbed": "violence", "blood": "threat",
    "poison": "threat", "poisoned": "threat", "scream": "shocking_line", "screams": "shocking_line",
    "gasp": "shocking_line", "gasps": "shocking_line", "reveal": "reveal", "reveals": "reveal",
    "revealed": "reveal", "kneel": "humiliation", "kneels": "humiliation", "kiss": "romance",
    "kisses": "romance", "kissed": "romance",
}  # fmt: skip
EMOTIONS = {"shocked", "furious", "horrified", "terrified", "enraged", "panicked", "gasping"}


def line_hits(ln: Line) -> tuple[int, HookType]:
    """Lexicon hits in one line and a guess at the hook type."""
    toks = re.findall(r"[a-z']+", ln.text.lower())
    kinds = [WORDS[t] for t in toks if t in WORDS]
    n = len(kinds) + ln.text.count("!") + sum(1 for e in ln.emotions if e.lower() in EMOTIONS)
    if not kinds and n:
        kinds = ["shocking_line"]
    return n, (kinds[0] if kinds else "other")  # type: ignore[return-value]


def propose_offline(show: Show, max_props: int) -> list[Proposal]:
    out: list[Proposal] = []
    for ep in show.episodes:
        rows = [ln for ln in ep.lines if ln.kind != "camera"]
        hits = [line_hits(ln) for ln in rows]
        scored: list[tuple[float, int]] = []
        for i, (ln, (n, _)) in enumerate(zip(rows, hits, strict=True)):
            if not n:
                continue
            win = [k for k, x in enumerate(rows) if ln.start <= x.start < ln.start + WINDOW]
            dens = sum(hits[k][0] for k in win) / max(len(win), 1)
            conf = min(1.0, dens / 2)
            if ln.start >= ep.duration - END_BONUS_WINDOW:
                conf = min(1.0, conf + END_BONUS)
            scored.append((conf, i))
        if not scored and rows:
            # no keyword at all (a plain-spoken show): fall back to the last WINDOW seconds of the
            # episode, where the show's own cliffhanger usually sits, with a low confidence
            tail = [k for k, x in enumerate(rows) if x.start >= ep.duration - WINDOW]
            scored.append((0.05, tail[0] if tail else 0))
        taken: list[float] = []
        for conf, i in sorted(scored, key=lambda t: (-t[0], t[1])):
            ln = rows[i]
            if any(abs(ln.start - t) < MIN_GAP for t in taken) or len(taken) >= max_props:
                continue
            last = [x for x in rows if ln.start <= x.start < ln.start + WINDOW][-1]
            taken.append(ln.start)
            out.append(Proposal(ep=ep.num, start_id=ln.id, hook_id=ln.id, cliff_id=last.id,
                                end_id=last.id, hook_type=hits[i][1], confidence=round(conf, 4),
                                hook_text=ln.text.strip('"')[:160],
                                cliffhanger_text=last.text.strip('"')[:160],
                                why="keyword and emotion density" if conf > 0.05
                                else "no keywords: last 45 s of the episode"))  # fmt: skip
    return out
