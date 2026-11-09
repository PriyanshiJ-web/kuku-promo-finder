from promo.parse import clip_text, cut_at_or_after, cut_at_or_before, cuts_between, safe_cuts
from promo.schema import Candidate, Segment


def test_no_cut_inside_dialogue(show):
    for ep in show.episodes:
        spans = [(ln.start, ln.end) for ln in ep.lines if ln.kind == "dialogue"]
        for c in ep.cuts:
            assert not any(s + 0.1 < c < e - 0.1 for s, e in spans), (ep.num, c)


def test_zero_and_duration_are_cuts(show):
    for ep in show.episodes:
        assert ep.cuts[0] == 0.0
        assert ep.duration in ep.cuts


def test_cuts_sorted_unique(show):
    for ep in show.episodes:
        assert ep.cuts == sorted(set(ep.cuts))


def test_camera_boundary_inside_dialogue_is_rejected(show):
    ep = show.ep(1)
    # fixture beat 1 splits the camera at t=6, inside the dialogue span 5..7
    assert 6.0 not in ep.cuts
    assert 5.0 in ep.cuts or 7.0 in ep.cuts


def test_helpers(show):
    ep = show.ep(1)
    assert cut_at_or_before(ep, 6.0) <= 6.0
    assert cut_at_or_after(ep, 6.0) >= 6.0
    assert cut_at_or_after(ep, 999) == ep.duration
    assert cut_at_or_before(ep, -1) == 0.0
    mid = cuts_between(ep, 4.0, 12.0)
    assert mid and all(4.0 <= c <= 12.0 for c in mid)


def test_safe_cuts_synthetic():
    from promo.schema import Line

    d = Line(id="x", ep=1, scene=1, idx=1, kind="dialogue", start=2.0, end=6.0)
    c = Line(id="y", ep=1, scene=1, idx=2, kind="camera", start=4.0, end=8.0)
    assert safe_cuts([d, c], 10.0) == [0.0, 2.0, 6.0, 8.0, 10.0]


def test_clip_text_hides_ids_and_episodes(show):
    ep = show.ep(1)
    segs = [
        Segment(ep=1, start=0.0, end=8.0, start_id="a", end_id="b"),
        Segment(ep=2, start=0.0, end=8.0, start_id="c", end_id="d"),
    ]
    cand = Candidate(
        id="c1", kind="montage", segments=segs, hook_id="a", cliff_id="d", duration=16.0
    )
    txt = clip_text(cand, show)
    assert "--- CUT ---" in txt and "E01" not in txt and "CAMERA" not in txt
    assert txt.splitlines()[0].startswith("[00.")
    assert ep.num == 1


def _mini_show():
    from promo.schema import Episode, Line, ParseReport, Show

    def mk(i, kind, a, b, text):
        return Line(id=f"E01-L{i:03d}", ep=1, scene=1, idx=i, kind=kind, start=a, end=b, text=text)

    lines = [
        mk(1, "action", 13.2, 16.0, "Mira lunges."),
        mk(2, "action", 13.0, 15.2, "Short overlap only."),
        mk(3, "dialogue", 14.0, 15.0, "Early line."),
        mk(4, "dialogue", 15.0, 17.0, "Later line."),
    ]
    ep = Episode(num=1, file="x", duration=20.0, lines=lines, cuts=[0.0, 15.0, 20.0])
    return Show(name="m", episodes=[ep], report=ParseReport())


def test_clip_text_includes_overlapping_action():
    show = _mini_show()
    seg = Segment(ep=1, start=14.9, end=19.0, start_id="a", end_id="b")
    cand = Candidate(id="c", kind="single", segments=[seg], hook_id="a", cliff_id="b", duration=4.1)
    rows = clip_text(cand, show).splitlines()
    assert rows[0] == "[00.0s] ACTION: Mira lunges."
    assert not any("Short overlap" in r for r in rows)  # only 0.3 s overlap
    assert any("Later line" in r for r in rows)
    assert not any("Early line" in r for r in rows)  # dialogue rule unchanged
