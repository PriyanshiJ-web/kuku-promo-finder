"""Property checks on the real show. Skipped when the data is absent. Prints no script text."""

from pathlib import Path

import pytest

from promo.parse import parse_folder

DATA = Path(__file__).resolve().parents[1] / "data" / "raw" / "duke"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="data/raw/duke not present")


@pytest.fixture(scope="module")
def real():
    return parse_folder(DATA)


def test_coverage(real):
    assert real.report.coverage >= 0.99


def test_no_cut_inside_dialogue(real):
    for ep in real.episodes:
        spans = [(ln.start, ln.end) for ln in ep.lines if ln.kind == "dialogue"]
        for c in ep.cuts:
            assert not any(s + 0.1 < c < e - 0.1 for s, e in spans), (ep.num, c)


def test_ids_identical_across_parses(real):
    again = parse_folder(DATA)
    for a, b in zip(real.episodes, again.episodes, strict=True):
        assert [ln.id for ln in a.lines] == [ln.id for ln in b.lines]


def test_duration_matches_max_line_end(real):
    for ep in real.episodes:
        assert abs(ep.duration - max(ln.end for ln in ep.lines)) <= 1.0, ep.num


def test_lines_sorted_by_start(real):
    for ep in real.episodes:
        starts = [ln.start for ln in ep.lines]
        assert starts == sorted(starts), ep.num


def test_episode_numbers_contiguous(real):
    assert [e.num for e in real.episodes] == list(range(1, len(real.episodes) + 1))
