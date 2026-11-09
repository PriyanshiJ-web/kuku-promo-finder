import csv
import json
import string

from promo.parse.render import clip_text
from promo.schema import Candidate, ColdView, GateResult, Promo, Segment
from promo.stages.coldview import ColdOut, gates_for, make_batches, render_batch
from promo.stages.rank import pointwise
from promo.stages.report import CSV_COLS, csv_text, mmss

from .synth import mkshow, scene1
from .test_stages import Responder, build, read, run_all


def cv(hook=3, clarity=3, curiosity=3, **kw):
    base = dict(clip_ref="A", who="two rivals", stakes="a secret", next_question="what now",
                makes_sense=True, open_ending=True)  # fmt: skip
    return ColdView(hook=hook, clarity=clarity, curiosity=curiosity, **{**base, **kw})


def cand(conf=0.5, **kw):
    return Candidate(id="c1", kind="single", segments=[Segment(ep=1, start=0, end=25, start_id="a",
                     end_id="b")], hook_id="h", cliff_id="c", duration=25.0, confidence=conf, **kw)  # fmt: skip


def test_pointwise_formula_and_scaling():
    # hook 5, curiosity 3, clarity 1 -> 1.0, 0.5, 0.0 scaled
    got = pointwise(cv(5, 1, 3), cand(0.6), [], hook_ep=50, free_eps=10)
    assert abs(got - (0.4 * 1.0 + 0.25 * 0.5 + 0.2 * 0.0 + 0.15 * 0.6)) < 1e-9


def test_free_episode_boost_and_soft_penalties():
    base = pointwise(cv(), cand(), [], 50, 10)
    assert abs(pointwise(cv(), cand(), [], 10, 10) - base - 0.05) < 1e-9
    assert abs(pointwise(cv(), cand(), [], 11, 10) - base) < 1e-9
    soft = [
        GateResult(gate="G6", passed=False, hard=False),
        GateResult(gate="G7", passed=False, hard=False),
    ]
    assert abs(pointwise(cv(), cand(), soft, 50, 10) - (base - 0.25)) < 1e-9
    hard_only = [GateResult(gate="G1", passed=False, hard=True)]
    assert (
        pointwise(cv(), cand(), hard_only, 50, 10) == base
    )  # hard gates exclude, they do not score


def test_g4_and_g5_rules():
    assert [g.passed for g in gates_for(cv())] == [True, True]
    assert [g.passed for g in gates_for(cv(open_ending=False))] == [False, True]
    assert [g.passed for g in gates_for(cv(makes_sense=False))] == [True, False]
    assert [g.passed for g in gates_for(cv(who="  "))] == [True, False]
    assert [g.passed for g in gates_for(None)] == [False, False]
    assert all(g.hard for g in gates_for(None))


def test_batches_are_deterministic_labelled_and_sized():
    cands = [cand().model_copy(update={"id": f"id{i:02d}"}) for i in range(14)]
    a, b = make_batches(cands, 6), make_batches(list(reversed(cands)), 6)
    assert [[c.id for c in x] for x in a] == [[c.id for c in x] for x in b]
    assert [len(x) for x in a] == [6, 6, 2]
    assert [c.id for x in a for c in x] != sorted(c.id for c in cands)  # shuffled


def test_batch_text_has_letters_and_hides_ids_and_episodes():
    ep, L = scene1()
    show = mkshow(ep)
    c = Candidate(id="zz", kind="single", segments=[Segment(ep=1, start=46.5, end=80.0,
                  start_id="a", end_id="b")], hook_id=L["h"].id, cliff_id=L["c"].id, duration=33.5)  # fmt: skip
    text = render_batch([c, c], show)
    assert "=== CLIP A ===" in text and "=== CLIP B ===" in text
    assert "E01" not in text and "CAMERA" not in text and "episode" not in text.lower()
    assert text.split("=== CLIP B ===")[0].count("[") == clip_text(c, show).count("[")


def test_coldout_accepts_bare_list():
    row = cv().model_dump()
    assert len(ColdOut.model_validate([row]).clips) == 1


async def test_pipeline_ranks_and_writes_reports(tmp_path):
    ctx, reports, resp = await run_all(tmp_path)
    assert [r.name for r in reports][-3:] == ["coldview", "rank", "report"]
    rk = read(ctx, "rank")
    assert rk["meta"]["ranked"] == len(rk["items"]) > 0
    finals = [p["final"] for p in rk["items"]]
    assert finals == sorted(finals, reverse=True)
    assert [p["rank"] for p in rk["items"]] == list(range(1, len(finals) + 1))
    for name in ("promos.json", "promos.csv", "report.md"):
        assert (ctx.out_dir / name).exists()
    rows = list(csv.reader((ctx.out_dir / "promos.csv").read_text().splitlines()))
    assert rows[0] == CSV_COLS and len(rows) > 1
    report = (ctx.out_dir / "report.md").read_text()
    for heading in ("## Summary", "## Top", "## Gates", "## Drop reasons", "## Failures",
                    "## Tokens, cost and time per stage", "## Profiles and models"):  # fmt: skip
        assert heading in report


async def test_clips_with_closed_ending_are_excluded_from_rank(tmp_path):
    from promo.parse import parse_folder
    from tests.conftest import FIX

    resp = Responder(parse_folder(FIX))
    resp.cold_open = False
    ctx = build(tmp_path, spec="1-2", responder=resp)
    from promo.runner import run_pipeline

    await run_pipeline(ctx)
    rk = read(ctx, "rank")
    assert rk["items"] == [] and rk["meta"]["excluded_by_gate"].get("G4", 0) > 0


async def test_missing_coldview_answers_are_retried_once(tmp_path):
    from promo.parse import parse_folder
    from promo.runner import run_pipeline
    from tests.conftest import FIX

    resp = Responder(parse_folder(FIX))
    inner = resp.__call__
    state = {"first": True}

    def skip_first(messages):
        out = inner(messages)
        if messages[0]["content"].startswith("TASK: COLDVIEW") and state["first"]:
            state["first"] = False
            data = json.loads(out)
            data["clips"] = data["clips"][:-1]  # the model forgets the last clip
            return json.dumps(data)
        return out

    ctx = build(tmp_path, spec="1-2", responder=skip_first)
    await run_pipeline(ctx)
    cm = read(ctx, "coldview")["meta"]
    assert cm["drops"].get("retried_missing") == 1 and cm["missing"] == 0


def test_mmss_and_csv_rows_per_segment():
    assert mmss(75.5) == "01:15.50" and mmss(0) == "00:00.00" and mmss(59.999) == "01:00.00"
    c = cand(hook_text="hook", cliffhanger_text="cliff", why="because")
    c2 = c.model_copy(
        update={"segments": [c.segments[0], c.segments[0].model_copy(update={"ep": 2})]}
    )
    p = Promo(
        rank=1,
        candidate=c2,
        gates=[GateResult(gate="G6", passed=False, hard=False)],
        pointwise=0.5,
        final=0.5,
    )
    rows = list(csv.DictReader(csv_text([p]).splitlines()))
    assert [r["seg_no"] for r in rows] == ["1", "2"] and rows[1]["episode"] == "2"
    assert rows[0]["gates_failed"] == "G6" and rows[0]["start"] == "00:00.00"
    assert string.ascii_uppercase[0] == "A"


async def test_pipeline_pairwise_fields_and_position_bias(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    rk = read(ctx, "rank")
    assert rk["meta"]["method"] == "swiss+bt+mmr"
    assert rk["meta"]["judge"]["position_bias_rate"] == 1.0  # the stub always answers "A"
    assert all(p["bt"] is not None for p in rk["items"][:24])
    assert all(p["post_first_reason"] == "" for p in rk["items"])  # split decisions give no wins
    # two orders per pair, a swapped order with identical clip text is a cache hit
    assert len(rk["pairs"]) <= resp.n["JUDGE"] <= 2 * len(rk["pairs"])
    for pair in rk["pairs"]:
        assert pair["a"] != pair["b"]
    keys = [frozenset((p["a"], p["b"])) for p in rk["pairs"]]
    assert len(keys) == len(set(keys))  # no rematches


def _promo(cid, source, variant, final, rank):
    c = cand().model_copy(update={"id": cid, "source": source, "variant": variant})
    return Promo(rank=rank, candidate=c, pointwise=final, final=final)


def test_collapse_keeps_only_the_best_variant_per_source_and_renumbers():
    from promo.stages.report import collapse_variants

    ranked = [
        _promo("a-tight", "propose:A", "tight", 0.9, 1),
        _promo("b", "propose:B", "tight", 0.8, 2),
        _promo("a-loose", "propose:A", "loose", 0.7, 3),
        _promo("c-tight", "propose:C", "tight", 0.5, 4),
        _promo("c-loose", "propose:C", "loose", 0.6, 5),  # the loose one scores higher here
    ]
    out = collapse_variants(ranked)
    assert [p.candidate.id for p in out] == ["a-tight", "b", "c-loose"]
    assert [p.rank for p in out] == [1, 2, 3]
    assert len({p.candidate.source for p in out}) == len(out)
    assert [p.rank for p in ranked] == [1, 2, 3, 4, 5]  # the input is not modified


async def test_pipeline_outputs_never_hold_two_variants_of_one_source(tmp_path):
    ctx, _, _ = await run_all(tmp_path)
    ranked = read(ctx, "rank")["items"]
    assert len({p["candidate"]["source"] for p in ranked}) < len(ranked)  # variants did compete
    promos = json.loads((ctx.out_dir / "promos.json").read_text())
    assert len({p["candidate"]["source"] for p in promos}) == len(promos)
    assert [p["rank"] for p in promos] == list(range(1, len(promos) + 1))
    rows = list(csv.DictReader((ctx.out_dir / "promos.csv").read_text().splitlines()))
    ids = {r["promo_id"] for r in rows}
    assert ids == {p["candidate"]["id"] for p in promos}
    assert read(ctx, "report")["meta"]["variants_collapsed"] == len(ranked) - len(promos)
