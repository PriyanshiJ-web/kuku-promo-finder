"""Tolerant screenplay parser (spec section 0)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from promo.parse.cuts import safe_cuts
from promo.parse.render import est_tokens, render_text
from promo.schema import Episode, Kind, Line, ParseReport, Scene, Show

TIME = r"(?:\d+:)?\d+:\d+(?:\.\d+)?"
RE_TIME_LINE = re.compile(
    rf"^start\s*:\s*\(?\s*({TIME})\s*\)?\s*\|\s*end\s*:\s*\(?\s*({TIME})\s*\)?\s*$", re.I
)
RE_BLOCK = re.compile(r"^\*\*(.+?):?\*\*\s*:?\s*$")
RE_SCENE = re.compile(r"^scene\s+(\d+)\s*$", re.I)
RE_FIELD = re.compile(
    r"^(scene heading|scene start|scene end|actions and dialogue)\s*:\s*(.*)$", re.I
)
RE_EPNUM = re.compile(r"^episode number\s*:\s*(?:episode\s*)?(\d+)", re.I)
RE_LOGLINE = re.compile(r"^episode logline\s*:\s*(.*)$", re.I)
RE_CONT = re.compile(r"^continuity notes\s*:\s*(.*)$", re.I)
RE_PAREN = re.compile(r"\(([^()]*)\)")
VOICE_MARKS = {
    "o.s.": "OS",
    "o.s": "OS",
    "off-screen": "OS",
    "off screen": "OS",
    "v.o.": "VO",
    "v.o": "VO",
}


def parse_time(s: str) -> float:
    parts = s.strip().split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def parse_tag(raw: str) -> tuple[Kind, str | None, list[str], str | None]:
    """Split a block tag into (kind, speaker_raw, emotions, voice)."""
    name = RE_PAREN.sub("", raw).strip(" :*")
    upper = re.sub(r"\s+", " ", name.upper())
    kinds: dict[str, Kind] = {
        "CAMERA": "camera",
        "ACTION": "action",
        "SOUND FX": "sfx",
        "SFX": "sfx",
        "SOUND": "sfx",
    }
    if upper in kinds:
        return kinds[upper], None, [], None
    emotions: list[str] = []
    voice = None
    for grp in RE_PAREN.findall(raw):
        head = grp.split("/")[0].strip().lower()
        if head in VOICE_MARKS:
            voice = VOICE_MARKS[head]
            continue
        emotions += [e.strip() for e in grp.split(",") if e.strip()]
    return "dialogue", upper, emotions, voice


@dataclass
class _Block:
    kind: Kind
    speaker: str | None
    emotions: list[str]
    voice: str | None
    scene: int
    start: float = 0.0
    end: float = 0.0
    text: list[str] = field(default_factory=list)


@dataclass
class _Stats:
    headers: int = 0
    missing_time: int = 0
    unknown: int = 0
    end_before_start: int = 0
    warnings: list[str] = field(default_factory=list)


def _fnum(filename: str) -> int | None:
    m = re.search(r"(\d+)", filename)
    return int(m.group(1)) if m else None


def parse_episode_text(text: str, filename: str, fallback_num: int) -> tuple[Episode, _Stats]:
    st = _Stats()
    meta: dict[str, str] = {}
    last_meta: str | None = None
    scenes: dict[int, Scene] = {}
    cur_scene = 0
    in_script = False
    state = "idle"  # idle | await_time | text | skip
    blk: _Block | None = None
    blocks: list[_Block] = []
    ep_num: int | None = None

    def flush() -> None:
        nonlocal blk
        if blk is not None:
            blocks.append(blk)
        blk = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if state == "text" and blk is not None and not blk.text:
                continue  # blank line between the time line and the text: keep waiting for it
            if state in ("text", "skip"):
                flush()
                state = "idle"
            continue
        low = line.lower()
        if low.startswith("--- script start"):
            in_script, state = True, "idle"
            continue
        if low.startswith("--- script end"):
            flush()
            in_script, state = False, "idle"
            continue
        m_block = RE_BLOCK.match(line)
        m_scene = RE_SCENE.match(line)
        if not in_script and not (m_block or m_scene):
            m = RE_EPNUM.match(line)
            if m:
                ep_num, last_meta = int(m.group(1)), None
            elif m := RE_LOGLINE.match(line):
                meta["logline"], last_meta = m.group(1).strip(), "logline"
            elif m := RE_CONT.match(line):
                meta["continuity"], last_meta = m.group(1).strip(), "continuity"
            elif last_meta:
                meta[last_meta] += " " + line
            continue
        if not in_script:
            in_script = True
            st.warnings.append(f"{filename}: no SCRIPT START marker")
        if m_scene:
            flush()
            state = "idle"
            cur_scene = int(m_scene.group(1))
            scenes.setdefault(cur_scene, Scene(num=cur_scene))
            continue
        m_field = RE_FIELD.match(line)
        if m_field and state != "text":
            key, val = m_field.group(1).lower(), m_field.group(2).strip()
            sc = scenes.setdefault(cur_scene, Scene(num=cur_scene))
            if key == "scene heading":
                sc.heading = val
            elif key in ("scene start", "scene end"):
                try:
                    setattr(sc, key.split()[1], parse_time(val))
                except ValueError:
                    st.warnings.append(f"{filename}: bad {key} value")
            continue
        if m_block:
            flush()
            st.headers += 1
            kind, spk, emo, voice = parse_tag(m_block.group(1))
            blk = _Block(kind, spk, emo, voice, cur_scene)
            state = "await_time"
            continue
        if state == "await_time":
            m = RE_TIME_LINE.match(line)
            if m and blk is not None:
                blk.start, blk.end = parse_time(m.group(1)), parse_time(m.group(2))
                state = "text"
            else:
                st.missing_time += 1
                blk = None
                state = "skip"
            continue
        if state == "text" and blk is not None:
            blk.text.append(line)
        elif state == "skip":
            continue
        else:
            st.unknown += 1
    flush()
    st.end_before_start = sum(1 for b in blocks if b.end < b.start)
    return _assemble(blocks, scenes, meta, ep_num, filename, fallback_num), st


def _assemble(blocks, scenes, meta, ep_num, filename, fallback_num) -> Episode:
    num = ep_num if ep_num is not None else (_fnum(filename) or fallback_num)
    blocks.sort(key=lambda b: b.start)  # stable: ties keep file order
    lines = [
        Line(
            id=f"E{num:02d}-L{i:03d}",
            ep=num,
            scene=b.scene,
            idx=i,
            kind=b.kind,
            speaker_raw=b.speaker,
            emotions=b.emotions,
            voice=b.voice,
            start=b.start,
            end=max(b.end, b.start),
            text=" ".join(b.text).strip(),
        )
        for i, b in enumerate(blocks, 1)
    ]
    scene_list = [scenes[k] for k in sorted(scenes) if k > 0 or lines]
    ends = [s.end for s in scene_list if s.end is not None] + [ln.end for ln in lines]
    duration = max(ends, default=0.0)
    ep = Episode(
        num=num,
        file=filename,
        logline=meta.get("logline"),
        continuity=meta.get("continuity"),
        duration=duration,
        scenes=scene_list,
        lines=lines,
    )
    ep.cuts = safe_cuts(lines, duration)
    return ep


def _check(ep: Episode, rep: ParseReport) -> None:
    by_scene = {s.num: s for s in ep.scenes}
    for ln in ep.lines:
        sc = by_scene.get(ln.scene)
        if sc and sc.start is not None and ln.start < sc.start - 0.5:
            rep.out_of_scene += 1
        elif sc and sc.end is not None and ln.start > sc.end + 0.5:
            rep.out_of_scene += 1


def parse_folder(folder: str | Path, name: str | None = None) -> Show:
    folder = Path(folder)
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"no .txt files in {folder}")
    rep = ParseReport()
    episodes: list[Episode] = []
    headers = 0
    seen: set[int] = set()
    for i, f in enumerate(files, 1):
        text = f.read_text(encoding="utf-8", errors="replace")
        ep, st = parse_episode_text(text, f.name, i)
        if ep.num in seen:
            rep.warnings.append(f"{f.name}: duplicate episode number {ep.num}, using position {i}")
            ep = parse_episode_text(text, f.name, i)[0]
            ep = _renumber(ep, i)
        seen.add(ep.num)
        headers += st.headers
        rep.lines_missing_time += st.missing_time
        rep.unknown_lines += st.unknown
        rep.warnings += st.warnings
        if st.missing_time:
            rep.warnings.append(f"ep {ep.num}: {st.missing_time} block(s) with unusable time")
        if st.unknown:
            rep.warnings.append(f"ep {ep.num}: {st.unknown} unknown line(s)")
        if not ep.lines:
            rep.warnings.append(f"ep {ep.num}: no blocks parsed")
        _check(ep, rep)
        rep.end_before_start += st.end_before_start
        for ln in ep.lines:
            rep.blocks_by_kind[ln.kind] = rep.blocks_by_kind.get(ln.kind, 0) + 1
        episodes.append(ep)
    episodes.sort(key=lambda e: e.num)
    offset = 0.0
    parsed = 0
    for ep in episodes:
        ep.offset = offset
        for ln in ep.lines:
            ln.gstart, ln.gend = offset + ln.start, offset + ln.end
        offset += ep.duration
        parsed += len(ep.lines)
        rep.est_tokens_full += est_tokens(render_text(ep, True))
        rep.est_tokens_compact += est_tokens(render_text(ep, False))
    rep.episodes = len(episodes)
    rep.total_seconds = offset
    rep.coverage = round(parsed / headers, 4) if headers else 1.0
    return Show(name=name or folder.name, episodes=episodes, report=rep)


def _renumber(ep: Episode, num: int) -> Episode:
    for ln in ep.lines:
        ln.ep = num
        ln.id = f"E{num:02d}-L{ln.idx:03d}"
    ep.num = num
    return ep
