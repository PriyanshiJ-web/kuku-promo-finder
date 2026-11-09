"""A new show may be formatted a little differently: the parser must give the same result."""

import re
from pathlib import Path

import pytest

from promo.parse import parse_folder

DEMO = Path(__file__).resolve().parents[1] / "demo" / "show"


def signature(show):
    """What the rest of the pipeline relies on: per line the kind, the text and rounded times."""
    return [
        (ln.kind, ln.text, round(ln.start, 1), round(ln.end, 1))
        for ep in show.episodes
        for ln in ep.lines
    ]


def write_variant(tmp_path, transform, rename=None):
    for f in sorted(DEMO.glob("*.txt")):
        text = transform(f.read_text(encoding="utf-8"))
        name = rename(f.name) if rename else f.name
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    return tmp_path


def secs_to_short(m):
    """00:07.50 -> 0:07.50 (single digit minutes)."""
    return f"{int(m.group(1))}:{m.group(2)}.{m.group(3)}"


VARIANTS = {
    "crlf": lambda t: t.replace("\n", "\r\n"),
    "bom": lambda t: "\ufeff" + t,
    "lowercase_headers": lambda t: re.sub(
        r"^(Scene heading|Scene start|Scene end|Actions and dialogue|EPISODE NUMBER)",
        lambda m: m.group(1).lower(),
        t,
        flags=re.M,
    ),
    "no_scene_end": lambda t: re.sub(r"^Scene end:.*\n", "", t, flags=re.M),
    "short_times": lambda t: re.sub(r"\((\d+):(\d\d)\.(\d\d)\)", lambda m: f"({secs_to_short(m)})", t),
    "trailing_spaces_and_blank_lines": lambda t: t.replace("\n", "  \n\n"),
    "lowercase_tags": lambda t: re.sub(r"^\*\*(.+?)\*\*:?$", lambda m: f"**{m.group(1).lower()}**", t,
                                       flags=re.M),
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_parses_like_the_original(tmp_path, name):
    base = signature(parse_folder(DEMO))
    show = parse_folder(write_variant(tmp_path, VARIANTS[name]))
    assert show.report.coverage >= 0.95
    assert len(show.episodes) == 5
    got = signature(show)
    if name == "lowercase_tags":  # tags are lower case, texts and times must still match
        got = [(k if k != "dialogue" else "dialogue", t, a, b) for k, t, a, b in got]
    assert got == base


def test_files_without_numbers_keep_sorted_order(tmp_path):
    show = parse_folder(
        write_variant(tmp_path, lambda t: re.sub(r"EPISODE NUMBER:.*\n", "", t),
                      rename=lambda n: "ep_" + re.search(r"-(\d{3})-", n).group(1) + ".txt")
    )  # fmt: skip
    assert [e.num for e in show.episodes] == [1, 2, 3, 4, 5]
