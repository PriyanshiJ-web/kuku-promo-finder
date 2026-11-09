import re

from promo.parse import compact, parse_episode_text, parse_folder
from promo.parse.reader import parse_tag, parse_time

from .conftest import FIX


def test_three_episodes_in_order(show):
    assert [e.num for e in show.episodes] == [1, 2, 3]
    assert show.report.episodes == 3


def test_ids_stable_and_ordered(show):
    again = parse_folder(FIX)
    for a, b in zip(show.episodes, again.episodes, strict=True):
        assert [ln.id for ln in a.lines] == [ln.id for ln in b.lines]
        starts = [ln.start for ln in a.lines]
        assert starts == sorted(starts)
        assert a.lines[0].id == f"E{a.num:02d}-L001"
        assert [ln.idx for ln in a.lines] == list(range(1, len(a.lines) + 1))


def test_time_formats():
    assert parse_time("00:12.50") == 12.5
    assert parse_time("0:04") == 4.0
    assert parse_time("1:02.5") == 62.5
    assert parse_time("1:01:02.50") == 3662.5


def test_case_insensitive_headers(show):
    ep2 = show.ep(2)
    assert ep2.scenes[0].heading.startswith("INT. BOAT HOUSE")
    assert ep2.scenes[0].start == 0.0
    assert ep2.scenes[0].end is None  # missing "Scene end" tolerated
    assert ep2.scenes[1].end == 45.0


def test_malformed_block_counted_not_fatal(show):
    r = show.report
    assert r.lines_missing_time == 1
    assert 0.98 < r.coverage < 1.0
    assert not any("Tobias freezes" in ln.text for ln in show.ep(2).lines)


def test_os_voice_and_emotions(show):
    os_lines = [ln for ln in show.ep(2).lines if ln.voice == "OS"]
    assert len(os_lines) == 1
    assert os_lines[0].speaker_raw == "MIRA"
    assert os_lines[0].emotions == ["Pleading", "Afraid"]


def test_multiline_text_joined(show):
    venn = [ln for ln in show.ep(2).lines if ln.speaker_raw == "VENN"][0]
    assert "and now it is done" in venn.text and "\n" not in venn.text


def test_episode_number_fallback_to_filename(show):
    ep3 = show.ep(3)
    assert ep3.num == 3 and ep3.lines[0].id.startswith("E03-")


def test_unknown_line_counted(show):
    assert show.report.unknown_lines == 1


def test_global_offsets(show):
    e1, e2, e3 = show.episodes
    assert e1.offset == 0
    assert e2.offset == e1.duration
    assert e3.offset == e1.duration + e2.duration
    ln = e2.lines[0]
    assert ln.gstart == e2.offset + ln.start and ln.gend == e2.offset + ln.end
    assert e1.duration == 40.0


def test_kinds_and_lookup(show):
    kinds = {ln.kind for e in show.episodes for ln in e.lines}
    assert kinds == {"dialogue", "action", "camera", "sfx"}
    ln = show.ep(1).lines[3]
    assert show.line(ln.id) is ln


def test_parse_tag():
    assert parse_tag("SOUND FX:")[0] == "sfx"
    kind, spk, emo, voice = parse_tag("THE STRANGER (O.S.) (Stern, Accusing):")
    assert (kind, spk, emo, voice) == ("dialogue", "THE STRANGER", ["Stern", "Accusing"], "OS")
    assert parse_tag("LORD X (O.S. / Continues):")[3] == "OS"
    assert parse_tag("Lady Z (Off-screen) (Vulnerable):")[3] == "OS"
    assert parse_tag("MARA (V.O.):")[3] == "VO"


def test_no_script_markers_is_tolerated():
    txt = "SCENE 01\n**ACTION:**\nStart: (00:01.00) | End: (00:02.00)\nA door opens.\n"
    ep, st = parse_episode_text(txt, "episode-009.txt", 1)
    assert ep.num == 9 and len(ep.lines) == 1 and st.warnings


def test_render_omits_camera_and_format(show):
    ep = show.ep(1)
    out = compact(ep)
    assert "CAMERA" not in out
    assert out.splitlines()[0].startswith("E01-L")
    assert all(re.match(r"^E01-L\d{3} \[\d{2}:\d{2}\.\d\] ", row) for row in out.splitlines())
    assert "CAMERA" in compact(ep, include_camera=True)
    dlg = [r for r in out.splitlines() if '"' in r][0]
    assert '""' not in dlg  # source quotes are not doubled
