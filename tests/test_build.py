from promo.schema import Candidate, MontageProposal, MontageSegment, Proposal, Segment
from promo.stages.build import (
    BuildParams,
    build_montage,
    build_single,
    dedupe,
    jaccard,
    time_iou,
)
from promo.stages.gates import code_gates

from .synth import mkep, mkshow, scene1

PRM = BuildParams()


def prop(labels, start, hook, cliff, end=None, ep=1, conf=0.8, **kw):
    return Proposal(
        ep=ep, start_id=labels[start].id, hook_id=labels[hook].id, cliff_id=labels[cliff].id,
        end_id=labels[end or cliff].id, hook_type="reveal", confidence=conf, **kw,
    )  # fmt: skip


def segs(c):
    return [(s.start, s.end) for s in c.segments]


def has_dialogue_inside(ep, t):
    return any(ln.kind == "dialogue" and ln.start + 0.1 < t < ln.end - 0.1 for ln in ep.lines)


# ---- start snapping -------------------------------------------------------------------


def test_start_snaps_to_earliest_cut_in_window_after_start_id():
    ep, L = scene1()
    cands, why = build_single(prop(L, "pre", "h", "c"), mkshow(ep), PRM)
    tight = [c for c in cands if c.variant == "tight"][0]
    assert why is None and segs(tight) == [(48.0, 73.0)]
    hook = L["h"]
    assert 0 <= hook.start - tight.segments[0].start <= 3.0


def test_start_id_earlier_than_window_is_ignored_for_the_window():
    ep, L = scene1()
    cands, _ = build_single(prop(L, "s0", "h", "c"), mkshow(ep), PRM)
    tight = [c for c in cands if c.variant == "tight"][0]
    assert tight.segments[0].start == 46.5  # earliest cut in [46, 49], not 30
    assert L["h"].start - tight.segments[0].start <= 3.0


def test_no_cut_in_window_falls_back_to_hook_start_with_note():
    spec = [
        ("a", "dialogue", 10, 40, "A very long speech about the tide."),
        ("h", "dialogue", 20, 23, "Tobias, you must stop."),
        ("c", "dialogue", 45, 48, "What did you do with the ledger?"),
    ]
    spec += [("cam", "camera", 0, 60, "Static wide shot.")]
    ep, L = mkep(1, spec, 60.0)
    cands, _ = build_single(prop(L, "a", "h", "c"), mkshow(ep), PRM)
    tight = [c for c in cands if c.variant == "tight"][0]
    assert tight.segments[0].start == 20.0
    assert "start_not_on_shot_boundary" in tight.notes


def test_hook_never_later_than_window_over_many_proposals(show):
    for ep in show.episodes:
        dl = [ln for ln in ep.lines if ln.kind == "dialogue"]
        for i, h in enumerate(dl):
            for c in dl[i : i + 6]:
                p = Proposal(ep=ep.num, start_id=dl[0].id, hook_id=h.id, cliff_id=c.id,
                             end_id=c.id, hook_type="other")  # fmt: skip
                cands, _ = build_single(p, show, PRM)
                for cand in cands:
                    off = h.start - cand.segments[0].start
                    assert -1e-6 <= off <= 3.0 + 1e-6 or "start_not_on_shot_boundary" in cand.notes


# ---- end snapping ---------------------------------------------------------------------


def test_end_never_strictly_inside_dialogue(show):
    for ep in show.episodes:
        dl = [ln for ln in ep.lines if ln.kind == "dialogue"]
        for i, h in enumerate(dl):
            for c in dl[i:]:
                p = Proposal(ep=ep.num, start_id=h.id, hook_id=h.id, cliff_id=c.id, end_id=c.id,
                             hook_type="other")  # fmt: skip
                for cand in build_single(p, show, PRM)[0]:
                    for s in cand.segments:
                        assert not has_dialogue_inside(ep, s.end)
                        assert not has_dialogue_inside(ep, s.start)


def test_end_overlapping_dialogue_moves_to_a_cut():
    spec = [
        ("h", "dialogue", 10, 12, "Stop right there."),
        ("c", "dialogue", 20, 36, "I have something to confess to everyone here."),
        ("cam1", "camera", 0, 30, "wide"), ("cam2", "camera", 30, 60, "wide"),
    ]  # fmt: skip
    ep, L = mkep(1, spec, 60.0)
    p = prop(L, "h", "h", "c")
    cands, _ = build_single(p, mkshow(ep), PRM)
    # cliff ends at 36, a cut exists at 36 (the dialogue's own end), the 30 boundary is inside it
    assert [c for c in cands if c.variant == "tight"][0].segments[0].end == 36.0
    assert 30.0 not in ep.cuts


# ---- length handling ------------------------------------------------------------------


def make_gap_episode(dialogue_in_gap: bool):
    spec = [
        ("h", "dialogue", 10, 12, "Stop right there."),
        ("c", "dialogue", 14, 16, "Where is the ledger?"),
        ("cam20", "camera", 16, 20, "wide"), ("cam25", "camera", 20, 25, "wide"),
        ("cam30", "camera", 25, 30, "wide"), ("cam40", "camera", 30, 40, "wide"),
        ("cam0", "camera", 0, 10, "wide"), ("cam10", "camera", 10, 16, "wide"),
        ("tail", "dialogue", 30, 33, "The tide is turning now."),
    ]  # fmt: skip
    if dialogue_in_gap:
        spec.append(("gap", "dialogue", 22, 24, "Somebody is on the stairs."))
    return mkep(1, spec, 40.0)


def test_too_short_extends_only_over_non_dialogue():
    ep, L = make_gap_episode(False)
    cands, why = build_single(prop(L, "h", "h", "c"), mkshow(ep), PRM)
    tight = [c for c in cands if c.variant == "tight"][0]
    assert why is None and tight.duration >= 20.0
    assert tight.segments[0].end == 30.0  # stops before the dialogue that starts at 30


def test_too_short_is_dropped_when_extension_would_add_dialogue():
    ep, L = make_gap_episode(True)
    cands, why = build_single(prop(L, "h", "h", "c"), mkshow(ep), PRM)
    assert cands == [] and why == "too_short"


def make_long_episode(cliff_start: float):
    spec = [("h", "dialogue", 10, 12, "Stop right there."),
            ("c", "dialogue", cliff_start, 100, "I will tell them everything tonight.")]  # fmt: skip
    for a in range(0, 190, 10):
        if not (cliff_start < a < 100):
            spec.append((f"cam{a}", "camera", a, a + 10, "wide"))
    return mkep(1, spec, 200.0)


def test_too_long_becomes_two_segments_with_cliff_in_second():
    ep, L = make_long_episode(85)
    cands, why = build_single(prop(L, "h", "h", "c"), mkshow(ep), PRM)
    tight = [c for c in cands if c.variant == "tight"][0]
    assert why is None and len(tight.segments) == 2
    s1, s2 = tight.segments
    assert (s1.start, s1.end) == (10.0, 40.0) and s2.end == 100.0
    assert s2.start <= L["c"].start and s2.end >= L["c"].end  # cliff inside seg2
    assert tight.duration <= 60.0 and s2.end - s2.start >= 3.0


def test_too_long_is_dropped_when_the_cliff_cannot_fit():
    ep, L = make_long_episode(60)  # cliff starts at 60, seg2 would need to start at >= 70
    cands, why = build_single(prop(L, "h", "h", "c"), mkshow(ep), PRM)
    assert cands == [] and why == "too_long"


# ---- variants -------------------------------------------------------------------------


def test_loose_variant_extends_start_and_end_without_adding_dialogue():
    ep, L = scene1()
    cands, _ = build_single(prop(L, "pre", "h", "c"), mkshow(ep), PRM)
    by = {c.variant: c for c in cands}
    assert segs(by["tight"]) == [(48.0, 73.0)]
    assert segs(by["loose"]) == [(46.5, 80.0)]  # hook still 2.5 s in, gap 73..80 has no dialogue
    assert by["loose"].duration <= 60.0


def test_loose_does_not_move_start_if_hook_would_leave_the_window():
    spec = [
        ("pre", "action", 40, 41, "Mira waits."),
        ("h", "dialogue", 50, 52, "Stop right there."),
        ("c", "dialogue", 70, 73, "Who sent the signal?"),
        ("cam1", "camera", 44, 47, "wide"), ("cam2", "camera", 47, 50, "wide"),
        ("cam3", "camera", 52, 80, "wide"),
    ]  # fmt: skip
    ep, L = mkep(1, spec, 90.0)
    cands, _ = build_single(prop(L, "pre", "h", "c"), mkshow(ep), PRM)
    by = {c.variant: c for c in cands}
    assert by["tight"].segments[0].start == 47.0
    assert "loose" not in by or by["loose"].segments[0].start >= 47.0


def test_identical_loose_variant_is_skipped():
    spec = [
        ("h", "dialogue", 0, 3, "Stop right there."), ("c", "dialogue", 22, 25, "Who sent it?"),
        ("cam", "camera", 0, 25, "wide"),
    ]  # fmt: skip
    ep, L = mkep(1, spec, 25.0)  # nothing earlier than 0 and nothing later than 25
    cands, _ = build_single(prop(L, "h", "h", "c"), mkshow(ep), PRM)
    assert [c.variant for c in cands] == ["tight"]


# ---- montage --------------------------------------------------------------------------


def two_eps():
    e1, L1 = scene1()
    spec = [
        ("m1", "dialogue", 5, 9, "The council has gathered at dawn."),
        ("m2", "dialogue", 20, 26, "She is not who you think she is."),
        ("cam", "camera", 0, 5, "wide"), ("cam2", "camera", 9, 20, "wide"),
        ("cam3", "camera", 26, 40, "wide"),
    ]  # fmt: skip
    e2, L2 = mkep(2, spec, 40.0, offset=120.0)
    return mkshow(e1, e2), L1, L2


def mont(segs_ids, hook, cliff, conf=0.7):
    return MontageProposal(
        segments=[MontageSegment(start_id=a, end_id=b) for a, b in segs_ids],
        hook_id=hook, cliff_id=cliff, hook_type="reveal", confidence=conf,
    )  # fmt: skip


def test_montage_cross_episode_valid():
    show, L1, L2 = two_eps()
    m = mont([(L1["h"].id, L1["d1"].id), (L2["m1"].id, L2["m2"].id)], L1["h"].id, L2["m2"].id)
    cand, why = build_montage(m, show, PRM, 0)
    assert why is None and cand.kind == "montage" and len(cand.segments) == 2
    assert [s.ep for s in cand.segments] == [1, 2] and 20 <= cand.duration <= 60
    assert cand.segments[0].start <= L1["h"].start <= cand.segments[0].start + 3.0


def test_montage_must_be_chronological():
    show, L1, L2 = two_eps()
    m = mont([(L2["m1"].id, L2["m2"].id), (L1["h"].id, L1["d1"].id)], L2["m1"].id, L1["d1"].id)
    assert build_montage(m, show, PRM, 0) == (None, "not_chronological")


def test_montage_first_segment_hook_is_pulled_into_window():
    show, L1, L2 = two_eps()
    # starts at s0 (30 s) but the hook is at 49: start must move into [46, 49]
    m = mont([(L1["s0"].id, L1["c"].id), (L2["m1"].id, L2["m2"].id)], L1["h"].id, L2["m2"].id)
    cand, why = build_montage(m, show, PRM, 0)
    assert why is None and L1["h"].start - cand.segments[0].start <= 3.0


def test_montage_short_segment_and_total_length_rules():
    show, L1, L2 = two_eps()
    short = mont([(L1["h"].id, L1["h"].id), (L2["m1"].id, L2["m1"].id)], L1["h"].id, L2["m1"].id)
    assert build_montage(short, show, PRM, 0)[1] in ("short_segment", "too_short")
    long_ = mont(
        [(L1["s0"].id, L1["after"].id), (L2["m1"].id, L2["m2"].id)], L1["h"].id, L2["m2"].id
    )
    assert build_montage(long_, show, BuildParams(max_len=30), 0)[1] in (
        "too_long",
        "short_segment",
    )


def test_montage_segments_never_overlap():
    show, L1, _ = two_eps()
    m = mont([(L1["h"].id, L1["d1"].id), (L1["d1"].id, L1["c"].id)], L1["h"].id, L1["c"].id)
    assert build_montage(m, show, PRM, 0)[1] == "not_chronological"


# ---- dedupe ---------------------------------------------------------------------------


def cand_from(show, ep_num, a, b, conf, source, variant="tight"):
    return Candidate(
        id=f"{source}-{variant}", kind="single", variant=variant,
        segments=[Segment(ep=ep_num, start=a, end=b, start_id="x", end_id="y")],
        hook_id="h", cliff_id="c", duration=b - a, confidence=conf, source=source,
    )  # fmt: skip


def test_dedupe_by_time_iou_keeps_higher_confidence():
    ep, _ = scene1()
    show = mkshow(ep)
    a = cand_from(show, 1, 46.0, 80.0, 0.6, "p1")
    b = cand_from(show, 1, 48.0, 84.0, 0.9, "p2")
    assert time_iou(a, b, show) > 0.5
    kept, dropped = dedupe([a, b], show)
    assert [c.source for c in kept] == ["p2"] and dropped == 1


def test_dedupe_recap_opening_of_next_episode_by_text_jaccard():
    shared = "the harbor lamp is out and nobody rows after midnight"
    e1, _ = mkep(1, [("a", "dialogue", 50, 58, shared), ("cam", "camera", 40, 60, "wide")], 60.0)
    e2, _ = mkep(2, [("a", "dialogue", 1, 9, shared + " again"), ("cam", "camera", 0, 20, "wide")],
                 40.0, offset=60.0)  # fmt: skip
    show = mkshow(e1, e2)
    a = cand_from(show, 1, 40.0, 60.0, 0.9, "end_of_ep1")
    b = cand_from(show, 2, 0.0, 20.0, 0.7, "recap_in_ep2")
    assert time_iou(a, b, show) == 0 and jaccard(a, b, show) > 0.6
    kept, dropped = dedupe([b, a], show)
    assert [c.source for c in kept] == ["end_of_ep1"] and dropped == 1


def test_dedupe_keeps_distinct_clips_and_variants_of_one_proposal():
    ep, _ = scene1()
    show = mkshow(ep)
    a = cand_from(show, 1, 48.0, 73.0, 0.8, "p1", "tight")
    b = cand_from(show, 1, 46.5, 80.0, 0.8, "p1", "loose")
    c = cand_from(show, 1, 90.0, 118.0, 0.7, "p2")
    kept, dropped = dedupe([a, b, c], show)
    assert len(kept) == 3 and dropped == 0


# ---- code gates -----------------------------------------------------------------------


def gate(cands, name):
    return {g.gate: g for g in cands}[name]


def good_candidate(show):
    ep = show.ep(1)
    return Candidate(
        id="g", kind="single", segments=[Segment(ep=1, start=48.0, end=73.0, start_id="a",
                                                 end_id="b")],
        hook_id=ep.lines[[ln.text for ln in ep.lines].index("You lied to the whole town.")].id,
        cliff_id="c", duration=25.0,
    )  # fmt: skip


def test_gates_pass_on_a_good_candidate():
    ep, _ = scene1()
    show = mkshow(ep)
    res = code_gates(good_candidate(show), show, PRM)
    assert {g.gate for g in res} == {"G1", "G2", "G3", "G_seg"}
    assert all(g.passed and g.hard for g in res)


def test_g1_length_reasons():
    ep, _ = scene1()
    show = mkshow(ep)
    c = good_candidate(show)
    short = c.model_copy(update={"duration": 12.0})
    g = gate(code_gates(short, show, PRM), "G1")
    assert not g.passed and "12.0" in g.reason
    long_ = c.model_copy(update={"duration": 61.0})
    assert not gate(code_gates(long_, show, PRM), "G1").passed


def test_g2_boundary_off_cut_fails():
    ep, _ = scene1()
    show = mkshow(ep)
    c = good_candidate(show)
    bad = c.model_copy(update={"segments": [c.segments[0].model_copy(update={"end": 51.0})]})
    g = gate(code_gates(bad, show, PRM), "G2")
    assert not g.passed and "51.0" in g.reason  # 51 is inside the hook line


def test_g3_hook_too_late_fails():
    ep, _ = scene1()
    show = mkshow(ep)
    c = good_candidate(show)
    late = c.model_copy(update={"segments": [c.segments[0].model_copy(update={"start": 45.0})]})
    g = gate(code_gates(late, show, PRM), "G3")
    assert not g.passed and "4.0" in g.reason


def test_g_seg_short_and_unordered_segments_fail():
    e1, L1 = scene1()
    e2, _ = mkep(2, [("a", "dialogue", 5, 9, "x y z"), ("cam", "camera", 0, 40, "wide")], 40.0)
    show = mkshow(e1, e2)
    c = good_candidate(show)
    tiny = Segment(ep=1, start=80.0, end=82.0, start_id="a", end_id="b")
    r = code_gates(c.model_copy(update={"segments": [c.segments[0], tiny]}), show, PRM)
    assert not gate(r, "G_seg").passed and "short" in gate(r, "G_seg").reason
    back = Segment(ep=1, start=10.0, end=20.0, start_id="a", end_id="b")
    r = code_gates(c.model_copy(update={"segments": [c.segments[0], back]}), show, PRM)
    assert not gate(r, "G_seg").passed and "chronological" in gate(r, "G_seg").reason
