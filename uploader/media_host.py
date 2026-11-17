"""미디어를 공개 GitHub 레포(GitHub Pages)에 올려 인스타그램이 가져갈 URL을 만든다.

인스타그램은 컨테이너를 만들 때 URL에서 파일을 내려받아 보관하므로, 게시가 끝난 뒤에는
원본이 필요 없다. 보존 기한이 지난 파일은 히스토리째(orphan 커밋 + force push) 지운다.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import requests

CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {proc.stderr.strip()}")
    return proc.stdout.strip()


class MediaHost:
    def __init__(self, repo: Path, pages_base: str, wait_seconds: int = 600):
        self.repo = repo
        self.base = pages_base.rstrip("/")
        self.wait_seconds = wait_seconds

    def url_for(self, post_id: str, name: str) -> str:
        return f"{self.base}/{post_id}/{name}"

    def upload(self, post_id: str, files: list[Path]) -> list[str]:
        """파일을 <repo>/<post_id>/ 로 복사·푸시하고 Pages에서 열릴 때까지 기다린다."""
        git(self.repo, "pull", "--quiet", "--rebase", "origin", "main")
        dest = self.repo / post_id
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
        git(self.repo, "add", post_id)
        git(self.repo, "commit", "--quiet", "-m", f"add {post_id}")
        git(self.repo, "push", "--quiet", "origin", "main")
        urls = [self.url_for(post_id, f.name) for f in files]
        self.wait_live(urls)
        return urls

    def wait_live(self, urls: list[str]) -> None:
        deadline = time.time() + self.wait_seconds
        pending = list(urls)
        while pending:
            url = pending[0]
            try:
                r = requests.head(url, timeout=20, allow_redirects=True)
                want = CONTENT_TYPES.get(Path(url).suffix.lower())
                if r.status_code == 200 and (not want or r.headers.get("content-type", "").startswith(want)):
                    pending.pop(0)
                    continue
            except requests.RequestException:
                pass
            if time.time() > deadline:
                raise TimeoutError(f"Pages에 아직 안 올라옴: {url}")
            time.sleep(10)

    def prune(self, remove_ids: list[str]) -> int:
        """지울 게시물 폴더를 빼고 남은 파일만 담은 새 루트 커밋으로 main을 갈아끼운다."""
        git(self.repo, "pull", "--quiet", "--rebase", "origin", "main")
        removed = 0
        for pid in remove_ids:
            d = self.repo / pid
            if d.exists():
                shutil.rmtree(d)
                removed += 1
        if not removed:
            return 0
        git(self.repo, "checkout", "--quiet", "--orphan", "snapshot")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", f"snapshot: remove {removed} expired posts")
        git(self.repo, "branch", "-D", "main")
        git(self.repo, "branch", "-m", "main")
        git(self.repo, "push", "--quiet", "--force", "origin", "main")
        git(self.repo, "reflog", "expire", "--expire=now", "--all")
        git(self.repo, "gc", "--quiet", "--prune=now")
        return removed
