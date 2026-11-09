"""Runner, parse, bible, propose and montage stages with a scripted FakeProvider (no network)."""

import json
import re
import shutil
from pathlib import Path

import pytest

from promo.config import Config, Profile
from promo.llm.ledger import BudgetExceeded
from promo.llm.providers import FakeProvider
from promo.runner import StageAbort, make_ctx, parse_episode_spec, run_pipeline
from promo.stages.bible import BibleOut, apply_aliases, fallback_bible, postprocess, tag_stats

from .conftest import FIX

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def cfg_fake(**prof) -> Config:
    prof = {"rpm": 100_000, "tpm": 10**9, "concurrency": 8, **prof}
    p = Profile(name="fake", provider="fake", model="fake-model", **prof)
    return Config(profiles={"fake": p}, stages={s: "fake" for s in
                  ("bible", "propose", "montage", "coldview", "judge")})  # fmt: skip


def ids(show, ep, kinds=("dialogue",)):
    return [ln.id for ln in show.ep(ep).lines if ln.kind in kinds]


class Responder:
    """Answers by prompt TASK marker. Counts calls per task."""

    def __init__(self, show, max_ep=2):
        d1, d2 = ids(show, 1), ids(show, 2)
        self.bible = {
            "premise": "A woman returns to a harbor town.",
            "characters": [
                {
                    "canonical": "Mira",
                    "aliases": ["MIRA", "GHOST ALIAS"],
                    "role": "heroine",
                    "first_ep": 1,
                },
                {"canonical": "Tobias", "aliases": ["TOBIAS"], "role": "rival", "first_ep": 1},
            ],
            "arcs": [{"name": "the ledger", "eps": [1, 2]}],
            "reveals": [{"what": "x", "line_id": d1[2]}, {"what": "bad", "line_id": "E99-L001"}],
            "open_questions": [
                {
                    "idx": 7,
                    "question": "Who sent the signal?",
                    "raised_at": d1[1],
                    "resolved_at": d2[3],
                },
                {
                    "idx": 8,
                    "question": "hallucinated",
                    "raised_at": "E99-L001",
                    "resolved_at": None,
                },
                {
                    "idx": 9,
                    "question": "bad resolve",
                    "raised_at": d1[0],
                    "resolved_at": "E77-L002",
                },
            ],
            "spoiler_line_ids": [d2[2], "E99-L009"],
        }
        self.proposals = {
            "proposals": [
                {
                    "ep": 1,
                    "start_id": d1[0],
                    "hook_id": d1[1],
                    "cliff_id": d1[3],
                    "end_id": d1[4],
                    "hook_type": "reveal",
                    "why": "ok",
                    "confidence": 0.9,
                    "question_ref": 0,
                },
                {
                    "ep": 1,
                    "start_id": d1[0],
                    "hook_id": "E01-L999",
                    "cliff_id": d1[3],
                    "end_id": d1[4],
                    "hook_type": "reveal",
                    "confidence": 0.5,
                },
                {
                    "ep": 1,
                    "start_id": d1[0],
                    "hook_id": d2[1],
                    "cliff_id": d1[3],
                    "end_id": d1[4],
                    "hook_type": "twist",
                    "confidence": 0.5,
                },
                {
                    "ep": 1,
                    "start_id": d1[4],
                    "hook_id": d1[1],
                    "cliff_id": d1[3],
                    "end_id": d1[2],
                    "hook_type": "threat",
                    "confidence": 0.5,
                },
                {
                    "ep": 3,
                    "start_id": ids(show, 3)[0],
                    "hook_id": ids(show, 3)[1],
                    "cliff_id": ids(show, 3)[2],
                    "end_id": ids(show, 3)[3],
                    "hook_type": "other",
                    "confidence": 0.5,
                },
                {
                    "ep": 2,
                    "start_id": d2[0],
                    "hook_id": d2[1],
                    "cliff_id": d2[4],
                    "end_id": d2[5],
                    "hook_type": "violence",
                    "confidence": 0.7,
                },
            ],
            "beats": [
                {"line_id": d1[1], "hook_type": "reveal", "text": "beat one"},
                {"line_id": d2[2], "hook_type": "threat", "text": "beat two"},
                {"line_id": "E01-L999", "hook_type": "other", "text": "bad"},
                {"line_id": ids(show, 3)[0], "hook_type": "other", "text": "outside"},
            ],
        }
        self.montage = {"montages": [
            {"segments": [{"start_id": d1[1], "end_id": d1[2]}, {"start_id": d2[1], "end_id": d2[3]}],
             "hook_id": d1[1], "cliff_id": d2[2], "hook_type": "reveal", "confidence": 0.6},
            {"segments": [{"start_id": d1[1], "end_id": d1[2]}], "hook_id": d1[1], "cliff_id": d1[2]},
            {"segments": [{"start_id": d1[1], "end_id": d1[2]}, {"start_id": d2[1], "end_id": d2[3]}],
             "hook_id": d2[1], "cliff_id": d2[2]},
        ]}  # fmt: skip
        self.n = {
            "BIBLE": 0,
            "BIBLE_CHUNK": 0,
            "PROPOSE": 0,
            "MONTAGE": 0,
            "COLDVIEW": 0,
            "JUDGE": 0,
            "SINGLE_PROMPT": 0,
        }
        self.cold_open = True
        self.judge_winner = "A"

    def __call__(self, messages):
        text = messages[0]["content"]
        task = text.split("\n", 1)[0].replace("TASK: ", "").strip()
        self.n[task] += 1
        if task == "SINGLE_PROMPT":
            return json.dumps(
                {"proposals": self.proposals["proposals"][:1] + self.proposals["proposals"][-1:]}
            )
        if task == "JUDGE":
            return json.dumps(
                {
                    "hook": self.judge_winner,
                    "ending": self.judge_winner,
                    "clarity": self.judge_winner,
                    "reason": "stronger opening and open ending",
                }
            )
        if task == "COLDVIEW":
            labels = re.findall(r"=== CLIP ([A-Z]) ===", text)
            return json.dumps({"clips": [
                {"clip_ref": lab, "who": "two rivals", "stakes": "a family secret",
                 "next_question": "what happens next", "makes_sense": True,
                 "open_ending": self.cold_open, "hook": 4, "clarity": 3, "curiosity": 5}
                for lab in labels
            ]})  # fmt: skip
        if task == "BIBLE_CHUNK":
            return json.dumps(
                {
                    "summary": "s",
                    "characters": [],
                    "reveals": [],
                    "open_questions": [],
                    "spoiler_candidates": [],
                }
            )
        return json.dumps(
            {"BIBLE": self.bible, "PROPOSE": self.proposals, "MONTAGE": self.montage}[task]
        )


def build(tmp_path, folder=FIX, spec=None, cfg=None, responder=None, **kw):
    cfg = cfg or cfg_fake()
    ctx = make_ctx(folder, "t", cfg, spec, providers={"fake": FakeProvider(default=responder)},
                   outputs_root=tmp_path / "out", cache_path=tmp_path / "c.sqlite", **kw)  # fmt: skip
    return ctx


async def run_all(tmp_path, spec="1-2", cfg=None, force=None):
    from promo.parse import parse_folder

    resp = Responder(parse_folder(FIX))
    cfg = cfg or cfg_fake()
    cfg.params.chunk_eps = 5
    ctx = build(tmp_path, spec=spec, cfg=cfg, responder=resp)
    reports = await run_pipeline(ctx, force=force)
    return ctx, reports, resp


def read(ctx, name):
    return json.loads((ctx.out_dir / f"{name}.json").read_text())


def test_episode_spec():
    assert parse_episode_spec("1-3,7") == {1, 2, 3, 7}
    assert parse_episode_spec(None) is None


async def test_full_run_writes_artifacts(tmp_path):
    ctx, reports, _ = await run_all(tmp_path)
    assert [r.status for r in reports] == ["ran"] * 9
    for n in (
        "parse",
        "bible",
        "propose",
        "montage",
        "build",
        "gates",
        "coldview",
        "rank",
        "report",
        "manifest",
        "parse_report",
    ):
        assert (ctx.out_dir / f"{n}.json").exists()
    assert set(read(ctx, "propose")) >= {"meta", "items", "failures", "beats"}
    assert not list(ctx.out_dir.glob("*.tmp"))  # atomic writes leave no temp files


async def test_second_run_is_skipped_and_identical(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    before = {n: (ctx.out_dir / f"{n}.json").read_bytes() for n in ("bible", "propose", "montage")}
    calls = sum(resp.n.values())
    ctx2 = build(tmp_path, spec="1-2", cfg=cfg_fake(), responder=resp)
    reports = await run_pipeline(ctx2)
    assert [r.status for r in reports] == ["skipped"] * 9
    assert sum(resp.n.values()) == calls
    assert before == {n: (ctx.out_dir / f"{n}.json").read_bytes() for n in before}
    assert ctx2.bible is not None and ctx2.alias_map  # state restored from artifacts


async def test_param_change_reruns_only_affected_stages(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    cfg = cfg_fake()
    cfg.params.max_props_per_ep = 1
    ctx2 = build(tmp_path, spec="1-2", cfg=cfg, responder=resp)
    reports = await run_pipeline(ctx2)
    assert [(r.name, r.status) for r in reports][:2] == [("parse", "skipped"), ("bible", "skipped")]
    assert reports[2].status == "ran"


async def test_force_reruns_one_stage(tmp_path):
    ctx, _, resp = await run_all(tmp_path)
    ctx2 = build(tmp_path, spec="1-2", cfg=cfg_fake(), responder=resp)
    reports = await run_pipeline(ctx2, force={"bible"})
    assert reports[1].status == "ran"


async def test_changed_input_file_reruns_everything(tmp_path):
    folder = tmp_path / "show"
    shutil.copytree(FIX, folder)
    from promo.parse import parse_folder

    resp = Responder(parse_folder(FIX))
    ctx = build(tmp_path, folder=folder, spec="1-2", responder=resp)
    await run_pipeline(ctx)
    f = next(folder.glob("episode-002*"))
    f.write_text(f.read_text() + "\n")
    ctx2 = build(tmp_path, folder=folder, spec="1-2", responder=resp)
    assert (await run_pipeline(ctx2))[0].status == "ran"


async def test_propose_validation_counts_drops(tmp_path):
    ctx, _, _ = await run_all(tmp_path)
    m = read(ctx, "propose")["meta"]
    assert m["raw_proposals"] == 6 and m["kept"] == 2
    assert m["drops"] == {
        "unknown_id": 1, "cross_episode": 1, "bad_order": 1, "outside_chunk": 1,
        "beat_unknown_id": 1, "beat_outside_chunk": 1,
    }  # fmt: skip
    assert m["n_beats"] == 2


async def test_kept_beats_are_all_inside_the_chunk(tmp_path):
    ctx, _, _ = await run_all(tmp_path)
    beats = read(ctx, "propose")["beats"]
    assert {b["line_id"][:3] for b in beats} <= {"E01", "E02"}


async def test_over_limit_proposals_are_capped(tmp_path):
    cfg = cfg_fake()
    cfg.params.max_props_per_ep = 1
    from promo.parse import parse_folder

    show = parse_folder(FIX)
    d1 = ids(show, 1)
    resp = Responder(show)
    resp.proposals = {"proposals": [
        {"ep": 1, "start_id": d1[0], "hook_id": d1[1], "cliff_id": d1[3], "end_id": d1[4],
         "hook_type": "reveal", "confidence": c} for c in (0.3, 0.9)
    ], "beats": []}  # fmt: skip
    ctx = build(tmp_path, spec="1-2", cfg=cfg, responder=resp)
    await run_pipeline(ctx)
    p = read(ctx, "propose")
    assert p["meta"]["drops"] == {"over_limit": 1}
    assert [i["confidence"] for i in p["items"]] == [0.9]


async def test_bible_postprocess_in_run(tmp_path):
    ctx, _, _ = await run_all(tmp_path)
    b = read(ctx, "bible")
    meta, bible = b["meta"], b["items"][0]
    assert meta["alias_map"]["MIRA"] == "Mira" and "GHOST ALIAS" not in meta["alias_map"]
    assert meta["dropped"]["aliases"] == 1
    assert len(bible["reveals"]) == 1
    assert [q["idx"] for q in bible["open_questions"]] == [0, 1]  # renumbered, bad ones dropped
    assert bible["open_questions"][1]["resolved_at"] is None
    assert len(bible["spoiler_line_ids"]) == 1
    assert "VENN" in meta["unresolved_tags"]  # code never guesses aliases by string rules
    speakers = {ln.speaker for ln in ctx.full_show.ep(1).lines if ln.kind == "dialogue"}
    assert "Mira" in speakers and "Tobias" in speakers


def test_apply_aliases_keeps_unmapped_raw(show):
    apply_aliases(show, {"MIRA": "Mira"})
    sp = {ln.speaker_raw: ln.speaker for ln in show.ep(2).lines if ln.kind == "dialogue"}
    assert sp["MIRA"] == "Mira" and sp["VENN"] == "VENN"


def test_fallback_bible_has_one_character_per_tag(show):
    bible, info = fallback_bible(show)
    assert {c.canonical for c in bible.characters} == {t for t, _, _ in tag_stats(show)}
    assert info["unresolved_tags"] == []


def test_postprocess_alias_conflict_first_wins(show):
    out = BibleOut(characters=[
        {"canonical": "A", "aliases": ["MIRA"]}, {"canonical": "B", "aliases": ["MIRA", "TOBIAS"]}
    ])  # fmt: skip
    _, info = postprocess(out, show)
    assert info["alias_map"]["MIRA"] == "A" and info["alias_map"]["TOBIAS"] == "B"
    assert info["dropped"]["alias_conflicts"] == 1


async def test_bible_failure_falls_back_and_records(tmp_path):
    from promo.parse import parse_folder

    resp = Responder(parse_folder(FIX))
    orig = resp.__call__

    def bad_bible(messages):
        if messages[0]["content"].startswith("TASK: BIBLE"):
            return "not json"
        return orig(messages)

    ctx = build(tmp_path, spec="1-2", responder=bad_bible)
    await run_pipeline(ctx, stages=["parse", "bible"])
    b = read(ctx, "bible")
    assert b["meta"]["degraded"] is True and b["failures"]
    assert b["items"][0]["characters"]


async def test_bible_map_reduce_when_too_big(tmp_path):
    cfg = cfg_fake(
        max_input_tokens=1300
    )  # single call needs more than 80% of this, each chunk fits
    cfg.params.chunk_eps = 1
    from promo.parse import parse_folder

    resp = Responder(parse_folder(FIX))
    ctx = build(tmp_path, spec="1-2", cfg=cfg, responder=resp)
    await run_pipeline(ctx, stages=["parse", "bible"])
    assert resp.n["BIBLE_CHUNK"] == 2 and resp.n["BIBLE"] == 1
    assert read(ctx, "bible")["meta"]["mode"] == "map_reduce"


async def test_montage_validation(tmp_path):
    ctx, _, _ = await run_all(tmp_path)
    m = read(ctx, "montage")["meta"]
    assert m["raw"] == 3 and m["kept"] == 1
    assert m["drops"] == {"bad_segment_count": 1, "hook_not_in_first_segment": 1}


async def test_episode_filter_limits_what_is_proposed(tmp_path):
    ctx, _, _ = await run_all(tmp_path, spec="1-2")
    assert [e.num for e in ctx.show.episodes] == [1, 2]
    assert len(ctx.full_show.episodes) == 3
    assert read(ctx, "propose")["meta"]["chunks"] == [[1, 2]]


async def test_lookahead_is_next_episode_and_marked_read_only(tmp_path):
    from promo.parse import parse_folder

    resp = Responder(parse_folder(FIX))
    seen = []
    inner = resp.__call__

    def spy(messages):
        seen.append(messages[0]["content"])
        return inner(messages)

    ctx = build(tmp_path, spec="1-2", responder=spy)
    await run_pipeline(ctx, stages=["parse", "bible", "propose"])
    prompt = next(t for t in seen if t.startswith("TASK: PROPOSE"))
    assert "## EPISODE 3 (READ-ONLY LOOKAHEAD)" in prompt
    before, after = prompt.split("## EPISODE 3 (READ-ONLY LOOKAHEAD)")
    assert "E03-L" in after and "## EPISODE 3\n" not in before


async def test_parse_aborts_on_low_coverage(tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    blocks = "".join("**ACTION:**\nStart: (oops) | End: (nope)\ntext\n\n" for _ in range(30))
    (folder / "episode-001.txt").write_text(
        "EPISODE NUMBER: Episode 1\n\n--- SCRIPT START ---\n\nSCENE 01\n\n"
        "**ACTION:**\nStart: (00:01.00) | End: (00:02.00)\nok\n\n" + blocks + "--- SCRIPT END ---\n"
    )
    ctx = build(tmp_path, folder=folder, responder=lambda m: "{}")
    with pytest.raises(StageAbort, match="coverage"):
        await run_pipeline(ctx)


async def test_budget_guard_aborts_before_calls(tmp_path):
    from promo.parse import parse_folder

    cfg = cfg_fake()
    cfg.params.max_calls = 0
    resp = Responder(parse_folder(FIX))
    ctx = build(tmp_path, spec="1-2", cfg=cfg, responder=resp)
    with pytest.raises(BudgetExceeded):
        await run_pipeline(ctx)
    assert sum(resp.n.values()) == 0


def test_every_prompt_contains_json_and_a_task_marker():
    for f in PROMPTS.glob("*.md"):
        text = f.read_text()
        assert "JSON" in text, f.name
        assert text.startswith("TASK: ") or f.name == "ping.md", f.name


def test_bare_list_replies_are_accepted():
    from promo.stages.montage import MontageOut
    from promo.stages.propose import ProposeOut

    p = {"ep": 1, "start_id": "E01-L001", "hook_id": "E01-L002", "cliff_id": "E01-L003",
         "end_id": "E01-L004", "hook_type": "reveal"}  # fmt: skip
    assert len(ProposeOut.model_validate([p]).proposals) == 1
    m = {"segments": [{"start_id": "a", "end_id": "b"}], "hook_id": "a", "cliff_id": "b"}
    assert len(MontageOut.model_validate([m]).montages) == 1


async def test_build_and_gates_stages_run_and_skip(tmp_path):
    ctx, _, _ = await run_all(tmp_path)
    b, g = read(ctx, "build"), read(ctx, "gates")
    assert set(b["meta"]) >= {"built", "kept", "drops", "by_kind_variant", "duration_hist_5s"}
    assert g["meta"]["candidates"] == len(b["items"]) == len(g["items"])
    assert all(
        {x["gate"] for x in it["gates"]} == {"G1", "G2", "G3", "G_seg", "G6", "G7"}
        for it in g["items"]
    )
    # editing a build parameter reruns build and gates only
    cfg = cfg_fake()
    cfg.params.min_len = 15
    ctx2 = build(tmp_path, spec="1-2", cfg=cfg, responder=Responder(ctx.full_show))
    status = {r.name: r.status for r in await run_pipeline(ctx2)}
    assert status["propose"] == "skipped" and status["build"] == "ran" and status["gates"] == "ran"


async def test_out_suffix_writes_to_a_separate_directory(tmp_path):
    from promo.parse import parse_folder

    resp = Responder(parse_folder(FIX))
    ctx = build(tmp_path, spec="1-2", responder=resp, out_suffix="alt")
    await run_pipeline(ctx)
    assert ctx.out_dir.name == "t__alt" and (ctx.out_dir / "promos.json").exists()
    assert not (tmp_path / "out" / "t").exists()


def test_offline_falls_back_to_the_episode_ending_when_no_keyword_matches():
    from promo.heuristics import propose_offline

    from .synth import mkep, mkshow

    ep, _ = mkep(1, [("a", "dialogue", 10, 13, "The tide comes in at six."),
                     ("b", "dialogue", 70, 74, "Bring the boat round."),
                     ("c", "dialogue", 90, 95, "We leave at dawn.")], 100.0)  # fmt: skip
    props = propose_offline(mkshow(ep), 6)
    assert len(props) == 1 and props[0].confidence == 0.05
    assert props[0].hook_id.endswith("L002") or props[0].hook_id.endswith("L003")
