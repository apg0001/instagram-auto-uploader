"""GitHub Trending(주간)에서 이번 주 별을 많이 받은 레포를 모은다.

GitHub에는 '주간 별 증가량' 공식 API가 없어서 github.com/trending?since=weekly 페이지(약 25개)를 읽고,
'stars this week' 순으로 다시 정렬한다. 매주 스냅샷을 남겨 지난주와 비교(순위·증가량)한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import requests

URL = "https://github.com/trending?since=weekly"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) instagram-auto-uploader"}


@dataclass
class Repo:
    name: str            # owner/repo
    description: str
    language: str | None
    stars_week: int      # 이번 주 증가
    stars_total: int
    rank: int = 0        # 이번 주 증가순 순위 (1부터)


def _int(text: str) -> int:
    m = re.search(r"[\d,]+", text or "")
    return int(m.group(0).replace(",", "")) if m else 0


def parse(html: str) -> list[Repo]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    repos = []
    for a in soup.select("article.Box-row"):
        link = a.select_one("h2 a")
        if not link:
            continue
        p = a.select_one("p")
        lang = a.select_one('[itemprop="programmingLanguage"]')
        week = a.select_one("span.d-inline-block.float-sm-right")
        total = a.select_one('a[href$="/stargazers"]')
        repos.append(Repo(name=link["href"].strip("/"), description=p.get_text(" ", strip=True) if p else "",
                          language=lang.get_text(strip=True) if lang else None,
                          stars_week=_int(week.get_text() if week else ""), stars_total=_int(total.get_text() if total else "")))
    repos.sort(key=lambda r: r.stars_week, reverse=True)
    for i, r in enumerate(repos, 1):
        r.rank = i
    return repos


def fetch() -> list[Repo]:
    r = requests.get(URL, headers=UA, timeout=30)
    r.raise_for_status()
    repos = parse(r.text)
    if len(repos) < 5:
        raise RuntimeError(f"트렌딩 레포가 너무 적음 ({len(repos)}) — 페이지 구조가 바뀌었을 수 있음")
    return repos


def readme(name: str, limit: int = 1500) -> str:
    try:
        r = requests.get(f"https://api.github.com/repos/{name}/readme", timeout=20,
                         headers={**UA, "Accept": "application/vnd.github.raw"})
        return r.text[:limit] if r.ok else ""
    except requests.RequestException:
        return ""


# ---------------------------------------------------------------- 주간 스냅샷

def save(repos: list[Repo], folder: Path, day: date) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{day.isoformat()}.json"
    out.write_text(json.dumps([asdict(r) for r in repos], ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def previous(folder: Path, day: date, min_gap_days: int = 5) -> dict[str, Repo]:
    """day 보다 min_gap_days 이상 앞선 가장 최근 스냅샷 (지난주)."""
    cands = sorted(p for p in folder.glob("*.json") if (day - date.fromisoformat(p.stem)).days >= min_gap_days)
    if not cands:
        return {}
    return {d["name"]: Repo(**d) for d in json.loads(cands[-1].read_text(encoding="utf-8"))}


def change(r: Repo, prev: dict[str, Repo]) -> dict:
    """지난주 대비: {'new': bool, 'prev_rank', 'prev_week', 'diff_week'} (지난주 기록이 아예 없으면 None 값)."""
    if not prev:
        return {"new": None, "prev_rank": None, "prev_week": None, "diff_week": None}
    p = prev.get(r.name)
    if not p:
        return {"new": True, "prev_rank": None, "prev_week": None, "diff_week": None}
    return {"new": False, "prev_rank": p.rank, "prev_week": p.stars_week, "diff_week": r.stars_week - p.stars_week}


def details(name: str) -> dict:
    """공개 API로 레포 정보: 조직 계정 여부, 만든 날, 라이선스, 토픽. 실패하면 빈 dict."""
    try:
        r = requests.get(f"https://api.github.com/repos/{name}", timeout=20,
                         headers={**UA, "Accept": "application/vnd.github+json"})
        if not r.ok:
            return {}
        d = r.json()
        return {"org": (d.get("owner") or {}).get("type") == "Organization",
                "created": (d.get("created_at") or "")[:10],
                "license": ((d.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", ""),
                "topics": d.get("topics") or []}
    except requests.RequestException:
        return {}


def insight(r: Repo, meta: dict, day: date) -> str:
    """데이터로 계산한 흥미로운 숫자 한 줄."""
    if meta.get("created"):
        age = (day - date.fromisoformat(meta["created"])).days
        if 0 <= age <= 60:
            return f"만든 지 {age}일 만에 별 {r.stars_total:,}개"
    if r.stars_total:
        share = r.stars_week / r.stars_total * 100
        if share >= 15:
            return f"누적 별의 {share:.0f}%가 이번 주에"
    return f"하루 평균 별 {r.stars_week // 7:,}개씩"
