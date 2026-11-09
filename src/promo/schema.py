"""Data models (spec section 1). Later-phase models are defined here as plain schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

Kind = Literal["dialogue", "action", "camera", "sfx"]
HookType = Literal[
    "violence", "reveal", "humiliation", "shocking_line", "romance", "threat", "twist", "other"
]


class Line(BaseModel):
    id: str
    ep: int
    scene: int
    idx: int
    kind: Kind
    speaker_raw: str | None = None
    speaker: str | None = None
    emotions: list[str] = Field(default_factory=list)
    voice: Literal["OS", "VO"] | None = None
    start: float
    end: float
    gstart: float = 0.0
    gend: float = 0.0
    text: str = ""


class Scene(BaseModel):
    num: int
    heading: str = ""
    start: float | None = None
    end: float | None = None


class Episode(BaseModel):
    num: int
    file: str
    logline: str | None = None
    continuity: str | None = None
    duration: float = 0.0
    offset: float = 0.0
    scenes: list[Scene] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    cuts: list[float] = Field(default_factory=list)


class ParseReport(BaseModel):
    episodes: int = 0
    blocks_by_kind: dict[str, int] = Field(default_factory=dict)
    lines_missing_time: int = 0
    end_before_start: int = 0
    out_of_scene: int = 0
    unknown_lines: int = 0
    coverage: float = 1.0
    total_seconds: float = 0.0
    est_tokens_full: int = 0
    est_tokens_compact: int = 0
    warnings: list[str] = Field(default_factory=list)


class Show(BaseModel):
    name: str
    episodes: list[Episode]
    report: ParseReport
    _by_id: dict[str, Line] | None = PrivateAttr(default=None)

    def ep(self, n: int) -> Episode:
        for e in self.episodes:
            if e.num == n:
                return e
        raise KeyError(f"no episode {n}")

    def line(self, line_id: str) -> Line:
        if self._by_id is None:
            self._by_id = {ln.id: ln for e in self.episodes for ln in e.lines}
        return self._by_id[line_id]


class Character(BaseModel):
    canonical: str
    aliases: list[str] = Field(default_factory=list)
    role: str = ""
    first_ep: int | None = None


class OpenQuestion(BaseModel):
    idx: int
    question: str
    raised_at: str
    resolved_at: str | None = None


class Bible(BaseModel):
    premise: str = ""
    characters: list[Character] = Field(default_factory=list)
    arcs: list[dict] = Field(default_factory=list)
    reveals: list[dict] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    spoiler_line_ids: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    ep: int
    start_id: str
    hook_id: str
    cliff_id: str
    end_id: str
    hook_type: HookType = "other"
    hook_text: str = ""
    cliffhanger_text: str = ""
    open_question: str = ""
    question_ref: int | None = None
    why: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class MontageSegment(BaseModel):
    start_id: str
    end_id: str


class MontageProposal(BaseModel):
    segments: list[MontageSegment]
    hook_id: str
    cliff_id: str
    hook_type: HookType = "other"
    hook_text: str = ""
    cliffhanger_text: str = ""
    open_question: str = ""
    question_ref: int | None = None
    why: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class Segment(BaseModel):
    ep: int
    start: float
    end: float
    start_id: str
    end_id: str


class Candidate(BaseModel):
    id: str
    kind: Literal["single", "montage"]
    variant: Literal["tight", "loose"] = "tight"
    segments: list[Segment]
    hook_id: str
    cliff_id: str
    duration: float
    hook_type: HookType = "other"
    hook_text: str = ""
    cliffhanger_text: str = ""
    open_question: str = ""
    question_ref: int | None = None
    why: str = ""
    confidence: float = 0.5
    source: str = ""
    notes: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    gate: str
    passed: bool
    hard: bool
    reason: str = ""


class ColdView(BaseModel):
    clip_ref: str
    who: str
    stakes: str
    next_question: str
    makes_sense: bool
    open_ending: bool
    hook: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    curiosity: int = Field(ge=1, le=5)


class Promo(BaseModel):
    rank: int
    candidate: Candidate
    gates: list[GateResult] = Field(default_factory=list)
    coldview: ColdView | None = None
    pointwise: float = 0.0
    bt: float | None = None
    final: float = 0.0
    post_first_reason: str = ""
