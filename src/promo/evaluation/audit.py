"""Rule-based defect audit: the same objective checks on the tool's clips and on every baseline.

No model and no hand labels are involved, so it cannot be fooled by a judge's taste and it is free
to rerun. It counts concrete defects an editor would fix, it does not measure how good a clip is.
Defects: opens on the tail of a sentence, no spoken line in the first 3 s, last spoken line is cut
mid-sentence, or the clip contains a line the bible marks as the answer to an open question, or
a spoiler.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from promo.evaluation import baselines
from promo.evaluation.metrics import IOU_MATCH
from promo.parse.render import clip_lines
from promo.schema import Bible, Candidate, Promo, Show
from promo.stages.build import BuildParams, time_iou
from promo.stages.polish import is_fragment

END_OK = re.compile(r'[.!?…"”]\s*$')
FLAGS = ("opens_mid_sentence", "silent_open", "ends_mid_sentence", "contains_answer",
         "contains_spoiler")  # fmt: skip


def clip_defects(c: Candidate, show: Show, bible: Bible) -> dict[str, bool]:
    rows = clip_lines(c, show)
    talk = [(ln, t) for ln, t in rows if ln.kind == "dialogue"]
    ids = {ln.id for ln, _ in rows}
    answers = {q.resolved_at for q in bible.open_questions if q.resolved_at}
    return {
        "opens_mid_sentence": bool(talk) and is_fragment(talk[0][0].text),
        "silent_open": not any(t <= 3.0 for _, t in talk),
        "ends_mid_sentence": bool(talk) and not END_OK.search(talk[-1][0].text.strip()),
        "contains_answer": bool(ids & answers),
        "contains_spoiler": bool(ids & set(bible.spoiler_line_ids)),
    }


def audit_set(cands: list[Candidate], show: Show, bible: Bible) -> dict:
    per = [clip_defects(c, show, bible) for c in cands]
    n = len(per)
    rate = {f: round(sum(p[f] for p in per) / n, 3) if n else None for f in FLAGS}
    rate["any_defect"] = round(sum(any(p.values()) for p in per) / n, 3) if n else None
    rate["clips"] = n
    return rate


def agreement(a: list[Candidate], b: list[Candidate], show: Show) -> float:
    """Share of clips in a that a second, independent run also found (time overlap >= 0.5)."""
    hits = sum(any(time_iou(x, y, show) >= IOU_MATCH for y in b) for x in a)
    return round(hits / len(a), 3) if a else 0.0


def _top(path: Path, n: int) -> list[Candidate]:
    promos = [Promo.model_validate(x) for x in json.loads(path.read_text())]
    return [p.candidate for p in sorted(promos, key=lambda p: p.rank)[:n]]


def run_audit(out_dir: Path, show: Show, prm: BuildParams, max_props: int) -> dict:
    bible = Bible.model_validate(json.loads((out_dir / "bible.json").read_text())["items"][0])
    sets: dict[str, list[Candidate]] = {
        "tool top 10": _top(out_dir / "promos.json", 10),
        "tool top 20": _top(out_dir / "promos.json", 20),
        "random 45 s (50 clips)": baselines.random_45(show, prm, n=50),
        "last 45 s (all episodes)": baselines.last_45(show, prm, n=len(show.episodes)),
        "keyword heuristic top 10": baselines.heuristic(show, prm, max_props),
    }
    saved = out_dir / "eval_baselines.json"
    if saved.exists():
        sp = json.loads(saved.read_text())["single_prompt"]
        sets["one prompt over the show"] = [Candidate.model_validate(c) for c in sp]
    result = {"sets": {k: audit_set(v, show, bible) for k, v in sets.items()}}
    alts = sorted(out_dir.parent.glob(f"{out_dir.name}__*/promos.json"))
    result["agreement_with_second_run"] = {
        p.parent.name: agreement(sets["tool top 20"], _top(p, 20), show) for p in alts
    }
    return result


def markdown(name: str, res: dict) -> str:
    cols = [*FLAGS, "any_defect"]
    head = ["clips", "opens mid-sentence", "silent first 3 s", "ends mid-sentence",
            "has an answer", "has spoiler", "any defect"]  # fmt: skip
    out = [f"# Defect audit: {name}", "",
           "Rule-based, no model, no labels. Lower is better. Same checks for every set.", "",
           "| set | " + " | ".join(head) + " |", "|---" * (len(head) + 1) + "|"]  # fmt: skip
    for k, v in res["sets"].items():
        out.append(f"| {k} | {v['clips']} | " + " | ".join(str(v[c]) for c in cols) + " |")
    for k, v in res["agreement_with_second_run"].items():
        out += ["", f"Agreement: {v} of the tool's top 20 were also found by the independent run "
                    f"`{k}` (a different proposing and judging model)."]  # fmt: skip
    return "\n".join(out) + "\n"
