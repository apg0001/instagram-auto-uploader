"""게시물 상태를 담는 SQLite. 노션이 화면이라면 이건 원장(ledger)이다."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 상태 이름은 노션 Select 옵션과 같게 맞춘다.
DRAFT, APPROVED, PUBLISHED, REJECTED, FAILED = "초안", "승인", "게시됨", "반려", "실패"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,            -- REELS | CAROUSEL | IMAGE | STORIES
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  caption TEXT NOT NULL,
  media TEXT NOT NULL,           -- JSON: [{"file": 로컬 경로, "url": 공개 URL, "type": "image"|"video"}]
  meta TEXT NOT NULL DEFAULT '{}',
  notion_page_id TEXT,
  ig_media_id TEXT,
  permalink TEXT,
  error TEXT,
  scheduled_at REAL,
  created_at REAL NOT NULL,
  decided_at REAL,               -- 게시·반려된 시각 (미디어 보존 기한 기준)
  media_deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Post:
    id: str
    source: str
    kind: str
    status: str
    title: str
    caption: str
    media: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)
    notion_page_id: str | None = None
    ig_media_id: str | None = None
    permalink: str | None = None
    error: str | None = None
    scheduled_at: float | None = None
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    media_deleted: int = 0


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- posts ---
    def save(self, p: Post) -> None:
        d = dict(p.__dict__)
        d["media"] = json.dumps(p.media, ensure_ascii=False)
        d["meta"] = json.dumps(p.meta, ensure_ascii=False)
        cols = ",".join(d)
        marks = ",".join(f":{k}" for k in d)
        self.conn.execute(f"INSERT OR REPLACE INTO posts ({cols}) VALUES ({marks})", d)
        self.conn.commit()

    def _row(self, r: sqlite3.Row) -> Post:
        d = dict(r)
        d["media"] = json.loads(d["media"])
        d["meta"] = json.loads(d["meta"])
        return Post(**d)

    def get(self, pid: str) -> Post | None:
        r = self.conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def find(self, prefix: str) -> Post | None:
        rows = self.conn.execute("SELECT * FROM posts WHERE id LIKE ?", (prefix + "%",)).fetchall()
        return self._row(rows[0]) if len(rows) == 1 else None

    def by_status(self, *statuses: str) -> list[Post]:
        q = f"SELECT * FROM posts WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at"
        return [self._row(r) for r in self.conn.execute(q, statuses)]

    def all(self) -> list[Post]:
        return [self._row(r) for r in self.conn.execute("SELECT * FROM posts ORDER BY created_at")]

    def recent_captions(self, source: str, n: int = 5) -> list[str]:
        rows = self.conn.execute(
            "SELECT caption FROM posts WHERE source=? ORDER BY created_at DESC LIMIT ?", (source, n)
        ).fetchall()
        return [r[0] for r in rows]

    def titles(self, prefix: str, n: int = 40) -> list[str]:
        """제목이 prefix로 시작하는 최근 게시물 제목 (반려 제외) — 시리즈가 이미 다룬 주제."""
        rows = self.conn.execute(
            "SELECT title FROM posts WHERE substr(title, 1, ?) = ? AND status != '반려' ORDER BY created_at DESC LIMIT ?",
            (len(prefix), prefix, n)).fetchall()
        return [r[0] for r in rows]

    def count_source(self, source: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM posts WHERE source=?", (source,)).fetchone()[0]

    # --- kv ---
    def kv_get(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()
