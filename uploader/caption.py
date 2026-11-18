"""`claude -p`(Claude Code 헤드리스)로 인스타그램 캡션을 만든다."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from typing import Any

MAX_CAPTION = 2200          # 인스타그램 캡션 최대 길이
MAX_HASHTAGS = 8           # 규칙은 4~5개. 많으면 스팸처럼 보여 오히려 손해

# 게시물에 레포·소스코드 이야기를 넣지 않는다 (게임만 소개).
BANNED = [
    re.compile(r"github\.com", re.I),
    re.compile(r"레포|리포지토리|저장소|오픈\s*소스|소스\s*코드|repo(sitory)?\b|open[- ]?source|pull request|커밋", re.I),
]


class CaptionError(RuntimeError):
    pass


def link_of(src: dict[str, Any]) -> str:
    """게시물에 꼭 들어가야 하는 링크 (게임: 플레이 링크, 레포 소개: 레포 주소)."""
    return src["repo_url"] if src.get("mode") == "repo" else src["play_url"]


def build_prompt(src: dict[str, Any], kind: str, scene: str, slides: list[str], recent: list[str]) -> str:
    facts = "\n".join(f"- {f}" for f in src["facts"])
    slide_txt = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(slides)) or "(없음)"
    recent_txt = "\n---\n".join(c[:400] for c in recent) or "(없음)"
    kind_ko = {"REELS": "릴스(짧은 데모 영상)", "CAROUSEL": "캐러셀(카드뉴스 여러 장)", "STORIES": "스토리"}.get(kind, kind)
    if src.get("mode") == "repo":
        who = "오픈소스 개발 도구를 만드는 개발자"
        subject = "프로젝트"
        rules = f"""- 첫 줄은 개발자가 공감할 짧은 훅이면서, 검색할 만한 키워드(예: Claude Code, AI 코딩 에이전트)를 자연스럽게 담는다. 과장 광고 말투는 피한다.
- 본문 4~7줄. 무엇을 해 주는지, 어떻게 쓰는지(설치 흐름), 개인정보·셀프 호스팅 같은 특징 중 2~3개를 구체적으로. 위 사실에 없는 기능·수치를 지어내지 않는다.
- 레포 링크 줄을 꼭 넣는다: "▶ GitHub: {src['repo_url']}" (프로필 링크에도 있다고 덧붙인다).
- 아직 베타라는 점을 솔직하게 한 줄 넣는다.
- 마지막 줄에 해시태그 4~5개만: #{src['hashtag']} 1개 + 주제 태그 2~3개 + 대상 태그 1개(예: #개발자). 너무 큰 태그(#AI, #개발) 대신 구체적인 중간 크기 태그."""
    else:
        who = "인디 게임 개발자"
        subject = "게임"
        rules = f"""- 첫 줄은 스크롤을 멈추게 하는 짧은 훅이면서, 사람들이 검색할 말(예: 대학원생 게임, 웹게임 추천)을 자연스럽게 담는다. 대학원생·연구실 공감 유머 톤, 과장 광고 말투는 피한다.
- 본문 3~6줄. 이번 장면과 게임 특징 1~2개만 골라 구체적으로. 위 사실에 없는 기능을 지어내지 않는다.
- 플레이 링크 줄을 꼭 넣는다: "▶ 플레이: {src['play_url']}" (폰 가로·세로 모두, 설치 없이 브라우저에서).
- GitHub 저장소, 레포, 소스코드, 오픈소스, 개발 과정 이야기는 절대 쓰지 않는다. 게임만 소개한다.
- 마지막 줄에 해시태그 4~5개만 (한국어 위주): #{src.get('hashtag', '랩워즈')} 1개 + 주제 태그 2~3개(#웹게임추천 #디펜스게임 등) + 대상 태그 1개(#대학원생공감 등). 너무 큰 태그는 쓰지 않는다."""
    return f"""너는 {who}의 인스타그램 계정 캡션을 쓰는 사람이다. 아래 {subject}를 소개하는 {kind_ko} 게시물 캡션을 한국어로 써라.

# {subject}
이름: {src['title']}
한 줄 소개: {src['tagline']}
링크: {link_of(src)}
{facts}

# 이번 게시물
장면: {scene}
슬라이드/화면 구성:
{slide_txt}

# 최근에 쓴 캡션 (표현·첫 문장이 겹치지 않게)
{recent_txt}

# 규칙
{rules}
- 이모지는 3개 이하.
- 전체 1200자 이내.

출력은 JSON 객체 하나만, 다른 말 없이: {{"caption": "..."}}"""


def validate(caption: str, link: str, allow_repo: bool = False) -> list[str]:
    problems = []
    if not caption.strip():
        problems.append("빈 캡션")
    if len(caption) > MAX_CAPTION:
        problems.append(f"{MAX_CAPTION}자 초과")
    if caption.count("#") > MAX_HASHTAGS:
        problems.append(f"해시태그 {MAX_HASHTAGS}개 초과")
    if link.rstrip("/") not in caption:
        problems.append("필수 링크 없음")
    for pat in [] if allow_repo else BANNED:
        m = pat.search(caption)
        if m:
            problems.append(f"금지 표현: {m.group(0)}")
    return problems


def parse_json(output: str) -> dict[str, Any]:
    """모델 출력에서 가장 바깥 JSON 객체를 꺼낸다 (앞뒤 설명·코드펜스 무시)."""
    m = re.search(r"\{.*\}", output, re.S)
    if not m:
        raise CaptionError(f"JSON을 찾지 못함: {output[:300]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise CaptionError(f"JSON 파싱 실패: {e}: {output[:300]}") from e
    if not isinstance(data, dict):
        raise CaptionError(f"JSON 객체가 아님: {output[:300]}")
    return data


def parse(output: str) -> str:
    data = parse_json(output)
    if not isinstance(data.get("caption"), str):
        raise CaptionError(f"caption 키 없음: {output[:300]}")
    return data["caption"].strip()


def call_claude(prompt: str, cfg: dict[str, Any]) -> str:
    cmd = list(cfg["command"])
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    # 프로젝트 폴더의 CLAUDE.md·메모리를 끌어오지 않도록 빈 임시 폴더에서 실행한다.
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd,
                              timeout=cfg.get("timeout_seconds", 300))
    if proc.returncode != 0:
        raise CaptionError(f"claude 실행 실패({proc.returncode}): {(proc.stderr or proc.stdout)[-1000:]}")
    return proc.stdout


def generate(src: dict[str, Any], cfg: dict[str, Any], kind: str, scene: str, slides: list[str],
             recent: list[str], attempts: int = 3) -> str:
    prompt = build_prompt(src, kind, scene, slides, recent)
    last: list[str] = []
    for _ in range(attempts):
        caption = parse(call_claude(prompt, cfg))
        last = validate(caption, link_of(src), allow_repo=src.get("mode") == "repo")
        if not last:
            return caption
        prompt += "\n\n# 직전 결과의 문제 (고쳐서 다시)\n- " + "\n- ".join(last)
    raise CaptionError("캡션 검증 실패: " + ", ".join(last))
