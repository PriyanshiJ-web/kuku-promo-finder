"""SQLite (WAL) response cache."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


class Cache:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS llm (key TEXT PRIMARY KEY, text TEXT NOT NULL, "
            "in_tokens INTEGER, out_tokens INTEGER)"
        )
        self._db.commit()

    @staticmethod
    def make_key(model_id: str, template_sha: str, messages: list[dict], params: dict) -> str:
        blob = json.dumps([model_id, template_sha, messages, params], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> tuple[str, int, int] | None:
        row = self._db.execute(
            "SELECT text, in_tokens, out_tokens FROM llm WHERE key=?", (key,)
        ).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def put(self, key: str, text: str, in_tokens: int, out_tokens: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO llm VALUES (?,?,?,?)", (key, text, in_tokens, out_tokens)
        )
        self._db.commit()
