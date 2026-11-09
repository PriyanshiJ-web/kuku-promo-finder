"""The repo must hold no character names, speaker tags or dialogue from the real show.

Skipped when the data folder is absent. The names and lines are derived from the data at run time,
so this file contains none of them. Scans every tracked file, binary PDFs excluded.
"""

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from promo.parse import parse_folder

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "raw" / "duke"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="data/raw/duke not present")

MIN_LINE = 18  # a real quoted line of this many characters or more must not appear
MIN_COUNT = 3  # a capitalised word seen this often and never in lower case is a proper noun
# Ordinary English that shows up in speaker tags or capitalised at sentence starts.
GENERIC = {
    "lady", "lord", "the", "of", "in", "and", "a", "an", "off", "screen", "voice", "man", "woman",
    "stranger", "noble", "nobleman", "matriarch", "priest", "guard", "royal", "elder", "villager",
    "mysterious", "bearded", "former", "groom", "blond", "gold", "gown", "continues", "narrator",
    "crowd", "servant", "maid", "guest", "guests", "soldier", "informant", "baron", "duke",
    "duchess", "step", "mother", "father", "sister", "brother", "king", "queen", "prince",
    "princess", "captain", "doctor", "physician", "knight", "page", "herald", "steward",
    "bald", "cap", "cart", "driver", "good", "high", "old", "third", "with", "yes", "oh", "tall",
    "god", "judge", "police", "constable", "aristocratic", "noblewoman", "conspirator", "crown",
    "dowager", "fighter", "footman", "housekeeper", "loyalist", "official", "peasant", "scarred",
    "scout", "sentry", "thug", "townsman", "butler", "i'm", "i've", "i'll", "don't", "can't",
}  # fmt: skip


def tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines() if not p.endswith(".pdf")]


def norm(s: str) -> str:
    return " ".join(s.lower().replace("“", '"').replace("”", '"').split())


def real_lines(show) -> set[str]:
    out = set()
    for ep in show.episodes:
        for ln in ep.lines:
            if ln.kind != "camera":
                t = norm(ln.text).strip('"')
                if len(t) >= MIN_LINE:
                    out.add(t)
    return out


def real_names(show) -> tuple[set[str], set[str]]:
    """(single words that are names, multi-word speaker tags and names)."""
    words: set[str] = set()
    phrases: set[str] = set()

    def add_name(name: str) -> None:
        toks = [w.removesuffix("'s") for w in re.findall(r"[a-z']+", name.lower())]
        words.update(w for w in toks if w not in GENERIC and len(w) > 2)
        if len(toks) >= 3:  # long descriptive tags such as a three-word label for one person
            phrases.add(norm(name))

    for ep in show.episodes:
        for ln in ep.lines:
            if ln.speaker_raw:
                add_name(ln.speaker_raw)
    bible = ROOT / "outputs" / "duke" / "bible.json"
    if bible.exists():
        for ch in json.loads(bible.read_text())["items"][0]["characters"]:
            add_name(ch["canonical"])
            for a in ch["aliases"]:
                add_name(a)
    # capitalised words that are never written in lower case anywhere in the show: proper nouns
    texts = []
    for ep in show.episodes:
        texts += [ep.logline or "", ep.continuity or ""] + [s.heading for s in ep.scenes]
        texts += [ln.text for ln in ep.lines if ln.kind != "camera"]
    seen, lower = Counter(), set()
    for t in texts:
        for w in re.findall(r"[A-Za-z']+", t):
            if w[0].islower():
                lower.add(w.lower())
            elif w[0].isupper() and not w.isupper():
                seen[w.lower().removesuffix("'s")] += 1
    words.update(w for w, n in seen.items() if n >= MIN_COUNT and w not in lower and w not in GENERIC)
    return {w for w in words if w not in GENERIC}, phrases


@pytest.fixture(scope="module")
def show():
    return parse_folder(DATA)


def test_no_real_dialogue_in_tracked_files(show):
    lines = real_lines(show)
    for path in tracked_text_files():
        text = norm(path.read_text(errors="replace"))
        hits = [ln for ln in lines if ln in text]
        assert not hits, f"{path.relative_to(ROOT)} holds {len(hits)} real dialogue line(s)"


def test_no_real_character_names_in_tracked_files(show):
    words, phrases = real_names(show)
    assert words, "no names were derived from the data, the check would be empty"
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, words))) + r")\b", re.I)
    for path in tracked_text_files():
        text = path.read_text(errors="replace")
        found = {m.group(1).lower() for m in pattern.finditer(text)}
        found |= {p for p in phrases if p in norm(text)}
        assert not found, f"{path.relative_to(ROOT)} holds real names: {len(found)} of them"
