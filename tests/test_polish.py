from promo.schema import Bible, Candidate, Promo, Segment
from promo.stages.polish import fix_end, fix_start, is_fragment, polish

from .synth import mkep, mkshow


def promo(segments, hook_id, ref=None):
    c = Candidate(
        id="x", kind="single", variant="tight", segments=segments, hook_id=hook_id,
        cliff_id=hook_id, duration=sum(s.end - s.start for s in segments), hook_type="reveal",
        question_ref=ref,
    )  # fmt: skip
    return Promo(rank=1, candidate=c, gates=[], pointwise=0.5, final=0.5)


def test_fragment_detection():
    assert is_fragment('"...and it is done."')
    assert is_fragment("and then he left.")
    assert not is_fragment("Get out of my house.")


def test_start_moves_past_a_sentence_fragment():
    ep, L = mkep(1, [
        ("frag", "dialogue", 10, 12, "...and that is the end of it."),
        ("hook", "dialogue", 12.5, 15, "You are not my daughter."),
        ("end", "dialogue", 40, 44, "Open the door."),
    ], 60.0)  # fmt: skip
    show = mkshow(ep)
    p = promo([Segment(ep=1, start=10, end=44, start_id=L["frag"].id, end_id=L["end"].id)],
              L["hook"].id)  # fmt: skip
    assert fix_start(p, show) == "start_after_fragment"
    assert 12.0 <= p.candidate.segments[0].start <= 12.5


def test_fragment_start_kept_when_clip_would_get_too_short():
    ep, L = mkep(1, [
        ("frag", "dialogue", 10, 12, "and so it goes."),
        ("hook", "dialogue", 12.5, 15, "You are not my daughter."),
        ("end", "dialogue", 25, 28, "Open the door."),
    ], 60.0)  # fmt: skip
    p = promo([Segment(ep=1, start=10, end=28, start_id=L["frag"].id, end_id=L["end"].id)],
              L["hook"].id)  # fmt: skip
    assert fix_start(p, mkshow(ep)) == "fragment_start_kept"
    assert p.candidate.segments[0].start == 10


def test_end_cuts_before_the_answer_line():
    ep, L = mkep(1, [
        ("hook", "dialogue", 0.5, 3, "Who took the key?"),
        ("mid", "dialogue", 20, 24, "Nobody will say."),
        ("ans", "dialogue", 50, 53, "I took it."),
    ], 70.0)  # fmt: skip
    bible = Bible.model_validate({"open_questions": [
        {"idx": 0, "question": "Who took the key?", "raised_at": L["hook"].id,
         "resolved_at": L["ans"].id}]})  # fmt: skip
    p = promo([Segment(ep=1, start=0, end=55, start_id=L["hook"].id, end_id=L["ans"].id)],
              L["hook"].id, ref=0)  # fmt: skip
    assert fix_end(p, mkshow(ep), bible) == "end_before_answer"
    assert p.candidate.segments[0].end <= 50.0


def test_near_duplicates_dropped_and_ranks_renumbered():
    ep, L = mkep(1, [("a", "dialogue", 5, 9, "Same words here.")], 100.0)
    seg = lambda a, b: [Segment(ep=1, start=a, end=b, start_id=L["a"].id, end_id=L["a"].id)]  # noqa: E731
    one, two, three = promo(seg(0, 30), L["a"].id), promo(seg(2, 32), L["a"].id), promo(seg(60, 90), L["a"].id)  # fmt: skip
    kept, stats = polish([one, two, three], mkshow(ep), Bible())
    assert len(kept) == 2 and stats["near_duplicates_dropped"] == 1
    assert [p.rank for p in kept] == [1, 2]
