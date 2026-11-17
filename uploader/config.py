"""설정(config.yaml)과 비밀 값(.env)을 읽는다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict[str, Any]

    @property
    def data_dir(self) -> Path:
        p = ROOT / self.raw.get("data_dir", "data")
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media(self) -> dict[str, Any]:
        return self.raw["media"]

    @property
    def media_repo(self) -> Path:
        return ROOT / self.media["repo_dir"]

    @property
    def instagram(self) -> dict[str, Any]:
        return self.raw["instagram"]

    @property
    def ig_enabled(self) -> bool:
        return bool(self.instagram.get("enabled")) and bool(os.getenv("IG_USER_ID")) and bool(os.getenv("IG_ACCESS_TOKEN"))

    @property
    def notion_enabled(self) -> bool:
        return bool(os.getenv("NOTION_TOKEN"))

    @property
    def caption(self) -> dict[str, Any]:
        return self.raw["caption"]

    def source(self, name: str) -> dict[str, Any]:
        return self.raw["sources"][name]


def load() -> Config:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return Config(yaml.safe_load(f))
