"""Promo Finder: review app. Browse ranked promos, read the clip, approve / trim / reject.

Run from the promo-finder folder:  streamlit run app/review.py
It reads outputs/<show>/ (your runs) and demo/outputs/ (an invented demo show that ships with the
repo). Nothing is uploaded anywhere. Never deploy it with a confidential show's data.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # works without `pip install -e .`

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from promo.parse import parse_folder  # noqa: E402
from promo.parse.render import clip_lines  # noqa: E402
from promo.schema import Candidate  # noqa: E402

RUN_DIRS = [ROOT / "outputs", ROOT / "demo" / "outputs"]
GATES = {
    "G1": "Length is 20 to 60 s", "G2": "Every cut is clean", "G3": "Hook within 3 s",
    "G_seg": "Segments long enough, in order", "G4": "Ends unresolved", "G5": "Makes sense alone",
    "G6": "Question is answered later in the show", "G7": "No spoiler line inside",
    "G8": "Answer line is not inside the clip",
}  # fmt: skip

st.set_page_config(page_title="Promo Finder", layout="wide")


def mmss(s: float) -> str:
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def find_runs() -> dict[str, Path]:
    runs: dict[str, Path] = {}
    for base in RUN_DIRS:
        for f in sorted(base.glob("*/promos.json")):
            demo = base.parent.name == "demo"
            runs[f.parent.name + (" (invented demo show)" if demo else "")] = f.parent
    return runs


def screenplay_folder(run: Path) -> str:
    """Best guess of where the screenplay .txt files are, for the transcript."""
    man = read_json(run / "manifest.json") or {}
    if run.parent.parent.name == "demo":
        guesses = [str(ROOT / "demo" / "show")]  # the demo run always uses the demo show
    else:
        base = run.name.split("_")[0]  # "duke__alt" is a variant of "duke"
        guesses = [str(man.get("folder", "")), str(ROOT / "data" / "raw" / base)]
    for g in guesses:
        if not g:
            continue
        p = Path(g.replace("\\", "/"))
        p = p if p.is_absolute() else ROOT / p
        if p.is_dir():
            return str(p)
    return ""


@st.cache_resource(show_spinner="Reading the screenplay...")
def parsed(folder: str):
    return parse_folder(folder)


def reviews(run: Path) -> dict[str, dict]:
    f = run / "review.csv"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8") as fh:
        return {r["promo_id"]: r for r in csv.DictReader(fh)}  # last decision wins


def save_review(run: Path, row: dict) -> None:
    f = run / "review.csv"
    new = not f.exists()
    with f.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


# ---- sidebar -----------------------------------------------------------------------------

runs = find_runs()
st.sidebar.header("Promo Finder")
st.sidebar.caption("Finds the best 20 to 60 second promo clips in a microdrama screenplay.")
labels = list(runs)
if st.session_state.get("pick") in labels:
    st.session_state["run_label"] = st.session_state.pop("pick")
label = st.sidebar.selectbox("Run to view", labels, key="run_label") if labels else None
run = runs[label] if label else None
promos = read_json(run / "promos.json") if run else None
src = st.sidebar.text_input("Screenplay folder", screenplay_folder(run) if run else "")
if label and "demo" in label:
    st.sidebar.info("An invented show made for this demo. It holds no real show content.")

tab_p, tab_h, tab_q, tab_r = st.tabs(["Promos", "How it works", "Quality and failures", "Run"])

# ---- promos ------------------------------------------------------------------------------

with tab_p:
    if not promos:
        st.info("No run found yet. Open the Run tab to analyse a screenplay.")
    else:
        done = reviews(run)
        types = sorted({p["candidate"]["hook_type"] for p in promos})
        c1, c2, c3 = st.columns([3, 2, 2])
        pick = c1.multiselect("Hook type", types, default=types)
        n_all = len(promos)
        top = c2.slider("Show top", 1, n_all, min(20, n_all)) if n_all > 1 else n_all
        hide = c3.checkbox("Hide reviewed")
        rows = [p for p in promos if p["candidate"]["hook_type"] in pick][:top]
        rows = [p for p in rows if not (hide and p["candidate"]["id"] in done)]
        st.caption(f"{len(promos)} promos found. Rank 1 is the one to post first.")
        table = pd.DataFrame([{
            "Rank": p["rank"],
            "Where": "; ".join(
                f"Ep {s['ep']} {mmss(s['start'])} to {mmss(s['end'])}"
                for s in p["candidate"]["segments"]
            ),
            "Length (s)": round(p["candidate"]["duration"], 1),
            "Hook type": p["candidate"]["hook_type"],
            "Score": round(p["final"], 2),
            "Failed checks": ", ".join(g["gate"] for g in p["gates"] if not g["passed"]),
            "Review": done.get(p["candidate"]["id"], {}).get("decision", ""),
            "Hook": p["candidate"]["hook_text"],
        } for p in rows])  # fmt: skip
        sel = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="single-row")
        st.caption("Click a row to open it.")
        csv_file = run / "promos.csv"
        if csv_file.exists():
            st.download_button("Download promos.csv", csv_file.read_bytes(), "promos.csv")
        if rows:
            p = rows[min(sel.selection.rows[0] if sel.selection.rows else 0, len(rows) - 1)]
            c = p["candidate"]
            st.divider()
            st.subheader(f"Rank {p['rank']}: {c['duration']:.1f} s, {c['hook_type']}")
            left, right = st.columns([3, 2])
            with left:
                st.markdown(f"**Hook.** {c['hook_text']}")
                st.markdown(f"**Cliffhanger.** {c['cliffhanger_text']}")
                st.markdown(f"**Open question.** {c['open_question']}")
                st.markdown(f"**Why it works.** {c['why']}")
                st.markdown(f"**Why post it first.** {p['post_first_reason'] or 'n/a'}")
                st.dataframe(pd.DataFrame([{
                    "Segment": i, "Episode": s["ep"], "Start": mmss(s["start"]),
                    "End": mmss(s["end"]), "Length (s)": round(s["end"] - s["start"], 1),
                } for i, s in enumerate(c["segments"], 1)]), hide_index=True)  # fmt: skip
                if any(n in c["notes"] for n in ("start_after_fragment", "end_before_answer")):
                    st.caption("Cut polish moved a start or end to a cleaner point.")
            with right:
                cv = p.get("coldview")
                if cv:
                    st.markdown("**Cold viewer** (saw only this clip)")
                    st.write(f"Hook {cv['hook']}/5, clarity {cv['clarity']}/5, "
                             f"curiosity {cv['curiosity']}/5")  # fmt: skip
                    st.caption(f"Who: {cv['who']}  \nStakes: {cv['stakes']}")
                else:
                    st.caption("No cold-viewer scores (offline run).")
                st.markdown("**Checks**")
                for g in p["gates"]:
                    st.write(f"{'Pass' if g['passed'] else 'Fail'}: {GATES.get(g['gate'], g['gate'])}")
            if src and Path(src).is_dir():
                cand = Candidate.model_validate(c)
                lines = []
                for ln, t in clip_lines(cand, parsed(src)):
                    mark = {c["hook_id"]: "HOOK ", c["cliff_id"]: "CLIFF"}.get(ln.id, "     ")
                    who = ln.speaker or ln.speaker_raw or ln.kind.upper()
                    lines.append(f"{mark} [{t:05.1f}s] {who}: {ln.text}")
                with st.expander("Transcript of the clip", expanded=True):
                    st.code("\n".join(lines), language=None)
            else:
                st.caption("Set the screenplay folder in the sidebar to see the transcript.")
            st.markdown("**Your decision**")
            a, b, n = st.columns([1, 1, 3])
            ds = a.number_input("Move start (s)", -10.0, 10.0, 0.0, 0.5, key=f"ds{c['id']}")
            de = b.number_input("Move end (s)", -10.0, 10.0, 0.0, 0.5, key=f"de{c['id']}")
            note = n.text_input("Note", key=f"n{c['id']}")
            for word, col in zip(("approve", "trim", "reject"), st.columns(6)[:3], strict=True):
                if col.button(word.capitalize(), key=f"{word}{c['id']}"):
                    save_review(run, {"ts": datetime.now().isoformat(timespec="seconds"),
                                      "promo_id": c["id"], "rank": p["rank"], "decision": word,
                                      "trim_start_s": ds, "trim_end_s": de, "note": note})  # fmt: skip
                    st.rerun()
            if done:
                data = (run / "review.csv").read_bytes()
                st.download_button("Download review.csv", data, "review.csv")

# ---- how it works ------------------------------------------------------------------------

with tab_h:
    st.markdown((ROOT / "app" / "how_it_works.md").read_text(encoding="utf-8"))

# ---- quality -----------------------------------------------------------------------------

with tab_q:
    shown = False
    if run:
        for name, title in (("eval.md", "Comparison with baselines"), ("report.md", "Run report")):
            f = run / name
            if f.exists():
                shown = True
                with st.expander(title, expanded=name == "eval.md"):
                    st.markdown(f.read_text(encoding="utf-8"))
    if run and (done_q := reviews(run)):
        top10 = {p["candidate"]["id"] for p in (promos or [])[:10]}
        seen = [r for k, r in done_q.items() if k in top10]
        good = sum(1 for r in seen if r["decision"] in ("approve", "trim"))
        st.markdown(f"**Your review so far:** {len(done_q)} promos reviewed. "
                    f"Of the top 10, you looked at {len(seen)} and kept {good} (approved or trimmed). "
                    "This is a quick check by you, not a blind label set.")  # fmt: skip
    fm = ROOT / "docs" / "failures.md"
    if fm.exists():
        with st.expander("Where the tool picks badly", expanded=True):
            st.markdown(fm.read_text(encoding="utf-8"))
    if not shown:
        st.caption("This run has no eval file. `promo eval --show NAME` creates one.")

# ---- run ---------------------------------------------------------------------------------

with tab_r:
    st.write("Analyse a screenplay: upload the episode .txt files, or give a folder on this machine.")
    up = st.file_uploader("Episode .txt files", type="txt", accept_multiple_files=True)
    folder = st.text_input("or a folder path", "")
    name = st.text_input("Name for this run", "myshow")
    eps = st.text_input("Episodes (blank = all, e.g. 1-5)", "")
    offline = st.checkbox("Offline: keyword rules only, no model, no API key", value=True)
    st.caption("Without offline mode the run uses the models in config/models.yaml and needs "
               "API keys in .env. It takes about 12 minutes for 50 episodes on free tiers.")  # fmt: skip
    if st.button("Run") and (up or folder):
        if up:
            tmp = Path(tempfile.mkdtemp(prefix="promo_"))
            for f in up:
                (tmp / Path(f.name).name).write_bytes(f.getvalue())
            folder = str(tmp)
        cmd = [sys.executable, "-c", "from promo.cli import app; app()", "run", folder]
        cmd += ["--show", name] + (["--episodes", eps] if eps else [])
        cmd += ["--offline"] if offline else []
        box, log = st.empty(), []
        with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as proc:  # fmt: skip
            for line in proc.stdout:
                log.append(line.rstrip())
                box.code("\n".join(log[-25:]), language=None)
        if proc.returncode == 0:
            st.session_state["pick"] = name
            st.success("Done. Open the Promos tab.")
            st.rerun()
        else:
            st.error("The run stopped. The last lines above say why. Try offline mode.")
