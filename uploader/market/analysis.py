"""시장 스냅샷을 claude -p(웹 검색 허용)에 넘겨 등락 근거와 캡션을 받는다.

원칙
- 숫자는 스냅샷 값만 쓴다. 카드의 숫자는 코드가 스냅샷에서 직접 그린다.
- 근거는 웹 검색으로 찾은 기사·공시가 있을 때만 쓰고 출처를 남긴다. 없으면 null.
- 매수·매도 권유를 하지 않는다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..caption import CaptionError, call_claude, parse_json

DISCLAIMER = "투자 권유가 아닌 정보 제공 목적의 글입니다. 투자 판단과 책임은 본인에게 있습니다."
BANNED = re.compile(r"매수\s*추천|매도\s*추천|사세요|파세요|목표\s*주가|무조건|확정\s*수익|급등\s*예상|리딩방", re.I)

REPORT_KO = {"premarket": "장 시작 전 브리핑", "close": "장 마감 시황", "weekly": "주간 리포트", "monthly": "월간 리포트"}

SCHEMA = {
    "premarket": """{
  "headline": "오늘 장을 한 줄로 (25자 이내)",
  "summary": "밤사이 미국장과 오늘 국내장 관전 포인트 2문장, 130자 이내",
  "us_sectors": [{"name": "us_sectors에 있는 이름", "reason": "근거 1~2문장(70자 이내) 또는 null", "source": "언론사명 또는 null"}],
  "us_bigtech": [{"name": "us_bigtech에 있는 이름", "reason": "...", "source": "..."}],
  "checkpoints": ["오늘 국내 시장에서 볼 일정·이슈 (실적 발표, 경제 지표, 정책 등) 3~5개, 각 40자 이내"],
  "caption": "인스타 캡션"
}""",
    "close": """{
  "headline": "오늘 장을 한 줄로 (25자 이내)",
  "summary": "시장 전체 흐름 2문장, 130자 이내 (지수·수급·환율)",
  "sectors": [{"name": "sectors_top/bottom에 있는 업종명", "reason": "근거 1~2문장(70자 이내) 또는 null", "source": "언론사명 또는 null"}],
  "movers": [{"name": "gainers/losers/market_cap에 있는 종목명", "reason": "...", "source": "..."}],
  "watch": [{"name": "데이터에 있는 종목명", "why": "내일 주의해서 볼 이유 40자 이내"}],
  "caption": "인스타 캡션"
}""",
    "weekly": """{
  "headline": "이번 주를 한 줄로 (25자 이내)",
  "summary": "한 주 흐름 2문장, 130자 이내",
  "sectors": [{"name": "sectors에 있는 이름", "reason": "...", "source": "..."}],
  "movers": [{"name": "caps_top/caps_bottom에 있는 종목명", "reason": "...", "source": "..."}],
  "issues": ["이번 주 시장을 움직인 이슈 3~5개, 각 45자 이내"],
  "next": ["다음 주 볼 일정 2~4개, 각 40자 이내"],
  "caption": "인스타 캡션"
}""",
}
SCHEMA["monthly"] = SCHEMA["weekly"].replace("이번 주", "이번 달").replace("한 주", "한 달").replace("다음 주", "다음 달")


def build_prompt(snap: dict[str, Any]) -> str:
    report = snap["report"]
    return f"""너는 한국 개인 투자자용 인스타그램 증시 계정의 애널리스트 겸 에디터다. 아래 데이터로 "{REPORT_KO[report]}" 게시물 원고를 써라.

# 데이터 (이 숫자만 사실로 쓴다)
```json
{json.dumps(snap, ensure_ascii=False, indent=1)}
```

# 할 일
1. 웹 검색으로 각 항목이 왜 오르고 내렸는지 근거(기사·공시·발표)를 찾는다. 기준일은 {snap['date']} 전후.
2. 근거를 찾은 항목만 reason에 1~2문장으로 쓰고 source에 언론사명을 쓴다. 못 찾으면 reason과 source를 null로 둔다. 추측으로 이유를 만들지 않는다.
3. 이름은 데이터에 있는 이름을 그대로 쓴다. 데이터에 없는 수치(목표가, 전망 수치 등)는 쓰지 않는다.
4. 매수·매도 권유, 수익 보장, 과장 표현 금지. 중립적인 시황 톤.
5. caption 규칙: 첫 줄 훅 → 핵심 3~5줄(숫자는 데이터 값, 소수 둘째 자리까지) → 빈 줄 → 마지막에 이 문장을 그대로: "{DISCLAIMER}" → 해시태그 4~5개(#증시 #코스피 #주식공부 등 관련성 높은 것만). 1500자 이내, 이모지 3개 이하.

# 출력
설명 없이 JSON 객체 하나만:
{SCHEMA[report]}"""


def validate(result: dict[str, Any], snap: dict[str, Any]) -> list[str]:
    problems = []
    cap = result.get("caption") or ""
    if DISCLAIMER not in cap:
        problems.append("캡션에 면책 문구 없음")
    if len(cap) > 2200:
        problems.append("캡션 2200자 초과")
    blob = json.dumps(result, ensure_ascii=False)
    m = BANNED.search(blob)
    if m:
        problems.append(f"금지 표현: {m.group(0)}")
    stray = stray_numbers(" ".join(str(result.get(k) or "") for k in ("headline", "summary", "caption")), snap)
    if stray:
        problems.append("데이터에 없는 숫자(스냅샷 값만 쓸 것): " + ", ".join(stray[:6]))
    names = set(re.findall(r'"name":\s*"([^"]+)"', json.dumps(snap, ensure_ascii=False)))
    for key in ("sectors", "movers", "us_sectors", "us_bigtech", "watch"):
        for item in result.get(key) or []:
            if item.get("name") not in names:
                problems.append(f"데이터에 없는 이름: {item.get('name')}")
    return problems


NUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")


def _snap_numbers(obj: Any, out: set[float]) -> set[float]:
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _snap_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _snap_numbers(v, out)
    return out


def stray_numbers(text: str, snap: dict[str, Any]) -> list[str]:
    """글에 나온 숫자 중 스냅샷에 없는 것. 100 미만 정수(날짜·개수·순위)는 검사하지 않는다."""
    known = _snap_numbers(snap, set())
    stray = []
    for tok in NUM.findall(text):
        v = float(tok.replace(",", ""))
        if v.is_integer() and v < 100:
            continue
        if any(abs(v - k) < 0.006 or round(k, 1) == v or round(k) == v for k in known):
            continue
        stray.append(tok)
    return stray


def analyze(snap: dict[str, Any], cfg: dict[str, Any], attempts: int = 2) -> dict[str, Any]:
    run_cfg = dict(cfg)
    run_cfg["command"] = list(cfg["command"]) + ["--allowedTools", "WebSearch", "WebFetch"]
    run_cfg["timeout_seconds"] = max(int(cfg.get("timeout_seconds", 300)), 900)
    prompt = build_prompt(snap)
    problems: list[str] = []
    for _ in range(attempts):
        result = parse_json(call_claude(prompt, run_cfg))
        problems = validate(result, snap)
        if not problems:
            return result
        prompt += "\n\n# 직전 결과의 문제 (고쳐서 JSON 전체를 다시)\n- " + "\n- ".join(problems)
    raise CaptionError("시황 원고 검증 실패: " + ", ".join(problems))


def reasons_by_name(items: list[dict] | None) -> dict[str, dict]:
    return {i["name"]: i for i in (items or []) if i.get("name")}
