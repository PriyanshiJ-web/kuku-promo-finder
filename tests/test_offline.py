import json
import shutil

from promo.config import Config, Profile
from promo.heuristics import line_hits, propose_offline
from promo.runner import make_ctx, run_pipeline
from promo.schema import Line

from .conftest import FIX
from .synth import mkep, mkshow


def test_line_hits_counts_words_exclamations_and_emotions():
    ln = Line(id="a", ep=1, scene=1, idx=1, kind="dialogue", start=0, end=1,
              text="She slapped him! Again!", emotions=["Furious", "calm"])  # fmt: skip
    n, kind = line_hits(ln)
    assert n == 4 and kind == "violence"  # slapped, two exclamation marks, furious
    quiet = ln.model_copy(update={"text": "Good morning.", "emotions": []})
    assert line_hits(quiet)[0] == 0


def test_propose_offline_shape_and_rules():
    spec = [("a", "dialogue", 10, 12, "He grabs the ledger!"), ("b", "dialogue", 14, 16, "Stop!"),
            ("c", "dialogue", 40, 42, "Nothing to see here."),
            ("d", "dialogue", 120, 123, "The poison is in the cup, gasp!"),
            ("cam", "camera", 0, 130, "wide")]  # fmt: skip
    ep, L = mkep(1, spec, 130.0)
    props = propose_offline(mkshow(ep), 6)
    assert props and all(p.start_id == p.hook_id and p.cliff_id == p.end_id for p in props)
    for p in props:
        hook, cliff = ep.lines[int(p.hook_id[-3:]) - 1], ep.lines[int(p.cliff_id[-3:]) - 1]
        assert hook.start <= cliff.start < hook.start + 45
        assert 0 <= p.confidence <= 1
    by_hook = {p.hook_id: p for p in props}
    late = by_hook[L["d"].id]  # within the last 30 s of the episode: bonus applied
    early = by_hook[L["a"].id]
    assert late.confidence > 0 and early.confidence > 0
    assert len(props) <= 6


def test_max_props_and_min_gap():
    spec = [(f"l{i}", "dialogue", i * 2.0, i * 2.0 + 1, "Stop! Gasp!") for i in range(40)]
    ep, _ = mkep(1, spec, 90.0)
    props = propose_offline(mkshow(ep), 3)
    assert len(props) <= 3
    starts = sorted(ep.lines[int(p.hook_id[-3:]) - 1].start for p in props)
    assert all(b - a >= 10 for a, b in zip(starts, starts[1:], strict=False))


async def test_offline_pipeline_runs_without_any_provider_or_key(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "CUSTOM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config(profiles={"gemini_flash": Profile(name="gemini_flash", model="m", api_key_env="GEMINI_API_KEY"),
                           "gemini_lite": Profile(name="gemini_lite", model="m", api_key_env="GEMINI_API_KEY")},
                 stages={"bible": "gemini_flash", "propose": "gemini_flash", "montage": "gemini_flash",
                         "coldview": "gemini_lite", "judge": "gemini_lite"})  # fmt: skip
    folder = tmp_path / "show"
    shutil.copytree(FIX, folder)
    for f in folder.glob("*.txt"):  # exclamation marks give the keyword heuristic something to find
        f.write_text(f.read_text().replace('."\n', '!"\n'))
    ctx = make_ctx(folder, "off", cfg, None, offline=True, outputs_root=tmp_path / "o",
                   cache_path=tmp_path / "c.sqlite")  # fmt: skip
    reports = await run_pipeline(ctx)
    assert [r.status for r in reports] == ["ran"] * 9
    assert ctx.ledger.calls == 0  # no model was called
    rk = json.loads((ctx.out_dir / "rank.json").read_text())
    assert rk["meta"]["method"] == "pointwise" and rk["meta"]["ranked"] > 0
    pw = [p["final"] for p in rk["items"]]
    assert pw[0] == max(pw)  # MMR may reorder the rest for diversity, the best stays first
    assert [p["rank"] for p in rk["items"]] == list(range(1, len(pw) + 1))
    assert (ctx.out_dir / "promos.csv").exists() and (ctx.out_dir / "report.md").exists()
    assert (await run_pipeline(ctx))[0].status == "skipped"  # hash-skip still works offline
