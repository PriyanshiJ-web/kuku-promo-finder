from promo.evaluation.audit import agreement, audit_set, clip_defects
from promo.schema import Bible, Candidate, Segment

from .synth import mkep, mkshow


def cand(ep, seg_start, seg_end, first, last):
    return Candidate(
        id="x", kind="single", variant="tight",
        segments=[Segment(ep=1, start=seg_start, end=seg_end, start_id=first.id, end_id=last.id)],
        hook_id=first.id, cliff_id=last.id, duration=seg_end - seg_start, hook_type="reveal",
    )  # fmt: skip


def show_and_lines():
    ep, L = mkep(1, [
        ("a", "dialogue", 1, 3, "...and that is why."),
        ("b", "dialogue", 10, 13, "Who took the key?"),
        ("c", "dialogue", 30, 33, "I took it, and I am"),
        ("ans", "dialogue", 40, 44, "It was me."),
    ], 60.0)  # fmt: skip
    return mkshow(ep), L


def test_defects_are_detected():
    show, L = show_and_lines()
    bible = Bible.model_validate({"open_questions": [
        {"idx": 0, "question": "q", "raised_at": L["b"].id, "resolved_at": L["ans"].id}]})  # fmt: skip
    bad = cand(show.episodes[0], 0, 44, L["a"], L["ans"])
    d = clip_defects(bad, show, bible)
    assert d["opens_mid_sentence"] and d["contains_answer"] and not d["ends_mid_sentence"]
    cut = cand(show.episodes[0], 9, 34, L["b"], L["c"])
    d = clip_defects(cut, show, bible)
    assert d["silent_open"] is False and d["ends_mid_sentence"] and not d["opens_mid_sentence"]


def test_rates_and_agreement():
    show, L = show_and_lines()
    bible = Bible()
    good = cand(show.episodes[0], 9, 14, L["b"], L["b"])
    bad = cand(show.episodes[0], 0, 4, L["a"], L["a"])
    r = audit_set([good, bad], show, bible)
    assert r["clips"] == 2 and r["opens_mid_sentence"] == 0.5 and r["any_defect"] == 0.5
    assert agreement([good], [good, bad], show) == 1.0 and agreement([good], [bad], show) == 0.0
