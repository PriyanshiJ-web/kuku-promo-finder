import itertools
import random

from promo.schema import Candidate, Segment
from promo.stages.pairwise import (
    PairResult,
    Verdict,
    bradley_terry,
    condense,
    decide,
    judge_pair,
    mmr_order,
    norm_log,
    similarity,
    swiss_pairs,
    tally,
)

from .synth import mkshow, scene1


def res(a, b, score, agree=True, valid=2):
    return PairResult(a, b, score, agree, valid, {})


def test_bt_recovers_a_transitive_order():
    ids = ["a", "b", "c", "d", "e"]  # a beats everyone below it
    results = [res(x, y, 1.0) for x, y in itertools.combinations(ids, 2)]
    p = bradley_terry(ids, results)
    assert sorted(ids, key=lambda i: -p[i]) == ids
    assert all(p[x] > p[y] for x, y in zip(ids, ids[1:], strict=False))


def test_bt_noisy_tournament_still_orders_by_strength():
    rng = random.Random(3)
    strength = {"a": 8.0, "b": 4.0, "c": 2.0, "d": 1.0}
    ids = list(strength)
    results = []
    for x, y in itertools.combinations(ids, 2):
        for _ in range(60):
            win = rng.random() < strength[x] / (strength[x] + strength[y])
            results.append(res(x, y, 1.0 if win else 0.0))
    p = bradley_terry(ids, results)
    assert sorted(ids, key=lambda i: -p[i]) == ["a", "b", "c", "d"]


def test_bt_split_decisions_are_a_tie_and_prior_keeps_values_finite():
    p = bradley_terry(["a", "b"], [res("a", "b", 0.5, agree=False)])
    assert abs(p["a"] - p["b"]) < 1e-9
    undefeated = bradley_terry(["a", "b"], [res("a", "b", 1.0)] * 5)
    assert 0 < undefeated["b"] < undefeated["a"] < float("inf")


def test_norm_log_is_zero_to_one():
    n = norm_log({"a": 4.0, "b": 1.0, "c": 0.25})
    assert n["a"] == 1.0 and n["c"] == 0.0 and 0 < n["b"] < 1
    assert norm_log({"a": 1.0, "b": 1.0}) == {"a": 0.5, "b": 0.5}


def test_swiss_round_one_pairs_neighbours_and_later_rounds_avoid_rematches():
    ids = [f"c{i}" for i in range(6)]
    pw = {i: 1.0 - n * 0.1 for n, i in enumerate(ids)}
    r1 = swiss_pairs(ids, {}, set(), pw, True)
    assert r1 == [("c0", "c1"), ("c2", "c3"), ("c4", "c5")]
    played = {frozenset(p) for p in r1}
    wins = {"c0": 1, "c2": 1, "c4": 1}
    r2 = swiss_pairs(ids, wins, played, pw, False)
    assert all(frozenset(p) not in played for p in r2) and len(r2) == 3
    assert set(r2[0]) == {"c0", "c2"}  # winners meet first
    played |= {frozenset(p) for p in r2}
    r3 = swiss_pairs(ids, wins, played, pw, False)
    assert all(frozenset(p) not in played for p in r3)


def test_swiss_never_rematches_over_many_rounds_and_handles_odd_counts():
    ids = [f"c{i}" for i in range(7)]
    pw = {i: 1.0 - n * 0.05 for n, i in enumerate(ids)}
    wins, played = {}, set()
    for rnd in range(4):
        pairs = swiss_pairs(ids, wins, played, pw, rnd == 0)
        assert len(pairs) == 3  # one bye
        for a, b in pairs:
            assert frozenset((a, b)) not in played
            played.add(frozenset((a, b)))
            wins[a] = wins.get(a, 0) + 1


def V(w, reason=""):
    return Verdict(hook=w, ending=w, clarity=w, reason=reason)


class StubLLM:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    async def call_json(self, stage, prompt, variables, schema, *, max_tokens):
        self.calls.append(variables)
        return self.verdicts.pop(0)


def two_cands():
    ep, L = scene1()
    show = mkshow(ep)
    seg = Segment(ep=1, start=46.5, end=80.0, start_id="a", end_id="b")
    mk = lambda i: Candidate(id=i, kind="single", segments=[seg], hook_id=L["h"].id,  # noqa: E731
                             cliff_id=L["c"].id, duration=33.5)  # fmt: skip
    return show, mk("x"), mk("y")


async def test_both_orders_agree_winner_gets_one():
    show, x, y = two_cands()
    # order 1: A is x. order 2: A is y. winner x both times means "A" then "B".
    llm = StubLLM([V("A", "x better"), V("B", "x again")])
    r = await judge_pair(llm, x, y, show)
    assert r.score_a == 1.0 and r.agree and r.valid == 2
    assert r.reasons == {"x": ["x better", "x again"]}
    first, second = llm.calls
    assert "E01" not in " ".join(first.values())  # judge sees clip text only
    assert (
        first["full_a"] == second["full_b"] and first["opening_a"] == second["opening_b"]
    )  # swapped


async def test_split_decision_is_half_each_and_counts_as_position_bias():
    show, x, y = two_cands()
    llm = StubLLM([V("A", "first"), V("A", "first again")])
    r = await judge_pair(llm, x, y, show)
    assert r.score_a == 0.5 and not r.agree
    t = tally([r, res("x", "y", 1.0)])
    assert t["split"] == 1 and t["position_bias_rate"] == 0.5


async def test_failed_verdicts_are_handled_without_raising():
    show, x, y = two_cands()
    one = await judge_pair(StubLLM([None, V("A", "r")]), x, y, show)
    assert one.valid == 1 and one.score_a == 0.0  # only order 2 answered: A is y, y wins
    none = await judge_pair(StubLLM([None, None]), x, y, show)
    assert none.valid == 0 and none.score_a == 0.5


def cand_at(show, i, a, b, hook_type="reveal", conf=0.5):
    L = {ln.text: ln for ln in show.ep(1).lines}
    h = L["You lied to the whole town."]
    return Candidate(id=i, kind="single", segments=[Segment(ep=1, start=a, end=b, start_id="a",
                     end_id="b")], hook_id=h.id, cliff_id=h.id, duration=b - a,
                     hook_type=hook_type, confidence=conf)  # fmt: skip


def test_mmr_keeps_near_identical_candidates_out_of_the_top_three():
    ep, _ = scene1()
    show = mkshow(ep)
    t0 = cand_at(show, "t0", 46.5, 80.0)
    t1 = cand_at(show, "t1", 46.6, 80.0)  # near-identical to t0
    o = cand_at(show, "o", 84.0, 118.0, hook_type="threat")
    p = cand_at(show, "p", 0.0, 30.0, hook_type="twist")
    final = {"t0": 0.9, "t1": 0.89, "o": 0.8, "p": 0.75}
    order = mmr_order([t0, t1, o, p], final, 0.7, show)
    assert order[:3] == ["t0", "o", "p"] and order[3] == "t1"
    assert similarity(t0, t1, show) == 1.0


def test_candidates_outside_top_k_stay_below_via_group_wise_mmr():
    # rank.run builds the order as mmr(top) + mmr(rest), so the rest can never overtake
    ep, _ = scene1()
    show = mkshow(ep)
    top = [cand_at(show, "t", 46.5, 80.0)]
    rest = [cand_at(show, "r", 46.6, 80.0)]  # identical clip, higher raw pointwise
    final = {"t": 0.1, "r": 0.9 - 1}
    order = mmr_order(top, final, 0.7, show) + mmr_order(rest, final, 0.7, show)
    assert order == ["t", "r"]


def test_condense_picks_the_most_typical_reason():
    rs = ["opens on a slap and ends before the answer", "slap opens it and ends before the answer",
          "nice colours"]  # fmt: skip
    assert condense(rs) in rs[:2]
    assert condense([]) == "" and condense(["only one"]) == "only one"


def test_decide_is_a_majority_with_the_hook_breaking_ties():
    assert decide({"hook": "A", "ending": "B", "clarity": "B"}) == "B"
    assert decide({"hook": "B", "ending": "A", "clarity": "A"}) == "A"
    assert decide({"hook": "A", "ending": "A", "clarity": "A"}) == "A"
    assert decide({"hook": "A", "ending": "B"}) == "A"  # tie: the hook decides
    assert decide({"hook": "B", "ending": "A"}) == "B"
    assert Verdict(hook="B", ending="A", clarity="B").winner == "B"


async def test_pair_detail_records_which_question_each_clip_won():
    show, x, y = two_cands()
    # order 1 (A is x): hook A=x, ending B=y, clarity A=x. order 2 (A is y): hook B=x, ending A=y, clarity B=x
    llm = StubLLM([Verdict(hook="A", ending="B", clarity="A", reason="r1"),
                   Verdict(hook="B", ending="A", clarity="B", reason="r2")])  # fmt: skip
    r = await judge_pair(llm, x, y, show)
    assert r.detail == [{"hook": "x", "ending": "y", "clarity": "x"},
                        {"hook": "x", "ending": "y", "clarity": "x"}]  # fmt: skip
    assert r.agree and r.score_a == 1.0  # x wins the majority in both orders


def test_clip_blocks_opening_full_and_last_lines():
    from promo.parse.render import clip_blocks

    from .synth import mkep

    spec = [
        ("a", "dialogue", 10, 12, "Opening line."), ("b", "action", 11, 11.5, "A door slams."),
        ("c", "dialogue", 14, 17, "Middle line."), ("d", "dialogue", 21, 26, "Almost there."),
        ("e", "dialogue", 26, 28, "Final line."), ("cam", "camera", 10, 30, "wide"),
    ]  # fmt: skip
    ep, L = mkep(1, spec, 40.0)
    seg = Segment(ep=1, start=10.0, end=30.0, start_id="a", end_id="e")
    c = Candidate(id="z", kind="single", segments=[seg], hook_id=L["a"].id, cliff_id=L["e"].id,
                  duration=20.0)  # fmt: skip
    opening, full, last = clip_blocks(c, mkshow(ep))
    assert "Opening line." in opening and "A door slams." in opening and "Middle" not in opening
    assert all(w in full for w in ("Opening line.", "Middle line.", "Almost there.", "Final line."))
    assert (
        "Final line." in last and "Almost there." in last
    )  # 22-24 s ends after the final 5 s starts at 15
    assert "Opening line." not in last and "CAMERA" not in full and "E01" not in opening + last


def test_judge_prompt_has_the_three_blocks_and_three_questions():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "prompts" / "judge_pairwise.md").read_text()
    for ph in ("opening_a", "full_a", "last_a", "opening_b", "full_b", "last_b"):
        assert "{{" + ph + "}}" in text
    assert "JSON" in text and text.startswith("TASK: JUDGE")
    for q in ('"hook"', '"ending"', '"clarity"'):
        assert q in text
