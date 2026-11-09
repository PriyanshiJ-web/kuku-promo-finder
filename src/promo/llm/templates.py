"""Prompt templates: plain .md files with {{var}} substitution. The sha is part of the cache key."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from promo.config import PROMPTS_DIR

RE_VAR = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Template:
    name: str
    text: str
    sha: str

    def render(self, variables: dict) -> str:
        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in variables:
                raise KeyError(f"prompt '{self.name}': missing variable '{key}'")
            val = variables[key]
            return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)

        return RE_VAR.sub(sub, self.text)


def load_template(name: str, prompts_dir: Path | None = None) -> Template:
    path = Path(prompts_dir or PROMPTS_DIR) / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    return Template(name=name, text=text, sha=hashlib.sha256(text.encode()).hexdigest())
