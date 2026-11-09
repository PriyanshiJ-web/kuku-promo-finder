import csv
import json

from promo.evaluation import baselines, metrics, perturb
from promo.evaluation.run import run_eval
from promo.schema import Candidate, Segment
from promo.stages.build import BuildParams
from promo.stages.gates import code_gates

from .synth import mkshow, scene1
from .test_stages import run_all

PRM = BuildParams()


def valid_g1_g2(c, show):
    return all(g.passed for g in code_gates(c, show, PRM) if g.gate in ("G1", "G2"))


def long_show():
    from .synth import mkep

    eps = []
    for n in (1, 2, 3):
        spec = [(f"c{a}", "camera", a, a + 5, "wide") for a in range(0, 150, 5)]
        spec += [(f"d{a}", "dialogue", a + 1, a + 3, "A line.") for a in range(0, 150, 15)]
        eps.append(mkep(n, spec, 150.0, offset=150.0 * (n - 1))[0])
    return mkshow(*eps)


def test_random_and_last_baselines_are_valid_and_seeded():
    show = long_show()
    a, b = baselines.random_45(show, PRM, n=6, seed=1), baselines.random_45(show, PRM, n=6, seed=1)
    assert [c.id for c in a] == [c.id for c in b] and len(a) == 6
    assert [c.id for c in baselines.random_45(show, PRM, n=6, seed=2)] != [c.id for c in a]
    last = baselines.last_45(show, PRM, n=3)
    assert len(last) == 3 and all(valid_g1_g2(c, show) for c in a + last)
    for c in last:
        assert c.segments[0].end == show.ep(c.segments[0].ep).duration  # ends with the episode
        assert 40 <= c.duration <= 60


def test_heuristic_baseline_passes_length_and_cut_gates():
    spec = [(f"l{i}", "dialogue", i * 6.0, i * 6.0 + 2, "Stop! Gasp!") for i in range(12)]
    spec += [("cam", "camera", 0, 80, "wide")]
    ep, _ = __import__("tests.synth", fromlist=["mkep"]).mkep(1, spec, 80.0)
    show = mkshow(ep)
    cands = baselines.heuristic(show, PRM, 6)
    assert cands and all(valid_g1_g2(c, show) for c in cands)


def make_cand(L, a=46.5, b=80.0):
    return Candidate(id="x", kind="single", segments=[Segment(ep=1, start=a, end=b, start_id="a",
                     end_id="b")], hook_id=L["h"].id, cliff_id=L["c"].id, duration=b - a)  # fmt: skip


def test_perturbations_move_the_right_edges():
    ep, L = scene1()
    show = mkshow(ep)
    c = make_cand(L, 48.0, 73.0)
    e = perturb.early(c, show)
    assert e.segments[0].start <= 40.0 and e.segments[0].end == 73.0 and "perturb_early" in e.notes
    lt = perturb.late(c, show)
    # the next two dialogue lines after 73 are 80..84 and nothing else, so it runs to the cut at 120
    assert lt.segments[-1].end > 73.0 and lt.segments[0].start == 48.0
    assert perturb.early(make_cand(L, 0.0, 30.0), show) is None  # nothing earlier to move to


def test_metrics_on_toy_data(tmp_path):
    ep, L = scene1()
    show = mkshow(ep)
    a, b = make_cand(L, 40.0, 70.0), make_cand(L, 90.0, 118.0)
    labels = [{"ep": 1, "start": 45.0, "end": 65.0}]
    assert metrics.precision_at_k([a, b], labels) == 0.5
    assert metrics.precision_at_k([a, b], None) == "no labels"
    assert (
        metrics.span_hit((0, 10), (9, 30)) is False and metrics.span_hit((0, 10), (5, 30)) is True
    )
    assert metrics.diversity([a, b], show) == 1.0
    assert metrics.diversity([a, a], show) == 0.0 and metrics.diversity([a], show) is None
    assert metrics.spread([a, b], show)["episodes"] == {1: 2}
    assert metrics.swap_overlap([a, b], [a], show) == 0.5
    assert metrics.mean([]) is None and metrics.mean([1, 0]) == 0.5


def test_labels_are_only_read_never_created(tmp_path):
    assert metrics.load_labels(tmp_path / "labels.csv") is None
    assert not (tmp_path / "labels.csv").exists()
    p = tmp_path / "labels.csv"
    p.write_text(
        "episode,start,end,hook_note,cliffhanger_note,quality_1to5\n1,01:05.5,01:30,x,y,4\n2,10,40,x,y,5\nbad,,,,\n"
    )
    assert metrics.load_labels(p) == [{"ep": 1, "start": 65.5, "end": 90.0},
                                      {"ep": 2, "start": 10.0, "end": 40.0}]  # fmt: skip


def test_label_template_is_header_only():
    from pathlib import Path

    t = Path(__file__).resolve().parents[1] / "eval" / "labels" / "template.csv"
    rows = list(csv.reader(t.read_text().splitlines()))
    assert rows == [["episode", "start", "end", "hook_note", "cliffhanger_note", "quality_1to5"]]
    assert not (t.parent / "labels.csv").exists()


async def test_run_eval_end_to_end_with_fake_judge(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    out = await run_eval(ctx, use_llm=True)
    assert set(out["win_rate_vs_baselines"]) == {
        "random_45",
        "last_45",
        "heuristic",
        "single_prompt",
    }
    for d in out["perturbation"].values():
        assert d["judged"] <= d["pairs"]
    assert out["precision_at_10"] == "no labels"
    data = json.loads((ctx.out_dir / "eval.json").read_text())
    assert data["show"] == "t" and (ctx.out_dir / "eval.md").exists()
    assert "Win rate" in (ctx.out_dir / "eval.md").read_text()


async def test_run_eval_without_llm_only_computes_code_metrics(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    before = sum(resp.n.values())
    out = await run_eval(ctx, use_llm=False)
    assert sum(resp.n.values()) == before and out["win_rate_vs_baselines"] == {}
    assert out["diversity"] is not None or out["top_n"] < 2


async def test_single_prompt_baseline_is_frozen_between_eval_runs(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    await run_eval(ctx, use_llm=True)
    first = json.loads((ctx.out_dir / "eval_baselines.json").read_text())
    calls = resp.n["SINGLE_PROMPT"]
    await run_eval(ctx, use_llm=True)  # reuses the saved clips, no new model call for it
    assert resp.n["SINGLE_PROMPT"] == calls
    assert json.loads((ctx.out_dir / "eval_baselines.json").read_text()) == first
    # a refresh goes back to the model path (a validated reply may still come from the cache)
    await run_eval(ctx, use_llm=True, refresh_baselines=True)
    assert resp.n["SINGLE_PROMPT"] >= calls
    assert (ctx.out_dir / "eval_baselines.json").exists()


async def test_perturbation_only_eval_writes_a_separate_file_and_compares(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    main = await run_eval(ctx, use_llm=True)  # writes eval.json with the judge profile recorded
    before = (ctx.out_dir / "eval.json").read_text()
    assert main["judge_profile"] == "fake" and main["judge_model"] == "fake-model"
    out = await run_eval(ctx, use_llm=True, only="perturbation", out_name="eval_diag")
    assert (ctx.out_dir / "eval.json").read_text() == before  # not overwritten
    assert (ctx.out_dir / "eval_diag.json").exists() and (ctx.out_dir / "eval_diag.md").exists()
    assert set(out["perturbation"]) == {"early", "late"} and "win_rate_vs_baselines" not in out
    assert out["compared_with"]["perturbation"] == main["perturbation"]
    md = (ctx.out_dir / "eval_diag.md").read_text()
    assert "main eval" in md and "this run" in md and "original preferred" in md
