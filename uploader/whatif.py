"""'만약에' 3D 릴스 — "매일 쓰는 그 돈을 그때부터 ○○에 넣었다면?"

1) 계산: 습관 비용(하루 ○원 × 30일)을 매달 자산(야후 수정주가, 달러 자산은 FRED 원/달러 환율)에 넣었다고 보고
   넣은 돈·지금 가치·배수·한 단위(잔·갑)당 가치·최대 낙폭·연말 가치 막대를 낸다.
2) 대본: Claude가 주제 후보 중 하나를 골라 9박자 대본 '틀'을 쓴다. 틀에는 숫자를 직접 못 쓰고 {value} 같은
   자리 표시만 쓴다 → 코드가 계산값을 채운다 (숫자는 전부 데이터에서).
3) 소리: 장면별 타입캐스트 내레이션(문맥 감정) → 장면 길이. 합성 BGM은 내레이션 때 줄인다.
4) 영상: video/whatif (Remotion + three.js) 를 swangle(소프트웨어 WebGL)로 렌더.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from . import bgm, compose, tts, typecast
from .caption import call_claude, parse_json

log = logging.getLogger("uploader")

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "video" / "whatif"
FPS = 30
H = {"User-Agent": "Mozilla/5.0"}
# 쓸 수 있는 자산: 티커 → (이름, 달러 자산인가)
ASSETS = {"NVDA": ("엔비디아", True), "AAPL": ("애플", True), "TSLA": ("테슬라", True), "MSFT": ("마이크로소프트", True),
          "GOOGL": ("구글", True), "AMZN": ("아마존", True), "SPY": ("미국 S&P500", True), "QQQ": ("나스닥100", True),
          "BTC-USD": ("비트코인", True), "005930.KS": ("삼성전자", False)}
VISUALS = ("spin", "pile", "growth", "rise", "crash", "end")
ITEMS = ("cup", "coin")
DISCLAIMER = "※ 과거 데이터로 계산한 상상이에요. 투자 권유가 아니며, 실제로는 수수료·세금·환전 비용이 있어요."


# ---------------------------------------------------------------- 데이터

def monthly_prices(ticker: str) -> dict[str, float]:
    """야후 월봉 수정주가 {YYYY-MM: 값} (마지막 달은 지금까지의 종가)."""
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"interval": "1mo", "range": "max"}, headers=H, timeout=30)
    r.raise_for_status()
    d = r.json()["chart"]["result"][0]
    adj = (d["indicators"].get("adjclose") or [{}])[0].get("adjclose") or d["indicators"]["quote"][0]["close"]
    out = {}
    for t, v in zip(d["timestamp"], adj, strict=False):
        if v:
            out[datetime.fromtimestamp(t, UTC).strftime("%Y-%m")] = float(v)
    return out


def krw_monthly() -> dict[str, float]:
    """FRED DEXKOUS (원/달러) 달마다 마지막 값."""
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "DEXKOUS"}, headers=H, timeout=30)
    r.raise_for_status()
    out: dict[str, float] = {}
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) == 2 and row[1] not in ("", ".") and row[0][:4].isdigit():
            out[row[0][:7]] = float(row[1])
    return out


@dataclass
class Numbers:
    daily: int
    start: str
    end: str            # 마지막으로 넣은 달
    months: int
    put: float          # 넣은 돈 (원)
    value: float        # 지금 가치 (원)
    units: int          # 산 횟수 (하루 1개 × 30일 × 달 수)
    mdd: float          # 최대 낙폭 (음수, 월말 종가 기준)
    mdd_year: str
    bars: list[dict[str, Any]] = field(default_factory=list)

    @property
    def multiple(self) -> float:
        return self.value / self.put

    @property
    def per_unit(self) -> float:
        return self.value / self.units


def habit_invest(daily: int, prices: dict[str, float], fx: dict[str, float] | None, start: str,
                 today: str) -> Numbers:
    """매달 1일치×30 을 그달 종가에 샀다고 보고 계산. fx 가 None 이면 원화 자산. today: 'YYYY-MM' (이 달은 넣지 않음)."""
    rate = (lambda m: fx.get(m)) if fx is not None else (lambda m: 1.0)
    months = [m for m in sorted(prices) if start <= m < today and rate(m)]
    if len(months) < 24:
        raise ValueError(f"데이터가 너무 짧음 ({len(months)}개월)")
    monthly = daily * 30
    shares = put = 0.0
    year_end: dict[str, float] = {}
    for m in months:
        shares += monthly / rate(m) / prices[m]
        put += monthly
        year_end[m[:4]] = shares * prices[m] * rate(m)
    last = max(prices)
    last_fx = rate(last) or rate(max(fx)) if fx is not None else 1.0
    value = shares * prices[last] * last_fx
    year_end[last[:4]] = value
    peak, mdd, mdd_m = 0.0, 0.0, months[0]
    for m in months + ([last] if last not in months else []):
        peak = max(peak, prices[m])
        dd = prices[m] / peak - 1
        if dd < mdd:
            mdd, mdd_m = dd, m
    bars, prev = [], None
    for y, v in sorted(year_end.items()):
        bars.append({"year": y, "log": math.log10(max(v, 1)), "label": won(v), "down": prev is not None and v < prev})
        prev = v
    return Numbers(daily, months[0], months[-1], len(months), put, value, len(months) * 30, mdd, mdd_m[:4], bars)


def won(x: float) -> str:
    """1.03e9 → '10억 3,000만 원', 1.917e7 → '1,917만 원', 4500 → '4,500원'."""
    if x >= 1e8:
        eok = int(x // 1e8)
        man = int(round((x % 1e8) / 1e6)) * 100  # 100만 단위로
        if man >= 10000:
            eok, man = eok + 1, 0
        return f"{eok:,}억" + (f" {man:,}만" if man else "") + " 원"
    if x >= 1e4:
        return f"{round(x / 1e4):,}만 원"
    return f"{x:,.0f}원"


def placeholders(n: Numbers, asset: str, unit: str) -> dict[str, str]:
    return {"daily": won(n.daily), "start_year": n.start[:4], "years": str(max(1, round(n.months / 12))),
            "asset": asset, "put": won(n.put), "value": won(n.value), "multiple": f"{n.multiple:,.0f}배",
            "per_unit": won(n.per_unit), "units": f"{n.units:,}{unit}", "mdd": f"{n.mdd * 100:.0f}%",
            "mdd_year": n.mdd_year}


# ---------------------------------------------------------------- 대본

WRITE_PROMPT = """너는 인스타그램 '만약에' 3D 릴스 작가다. 보는 사람의 습관을 콕 찔러서 "아 ㅅㅂ" 소리 나오게 하고 댓글로 자기 얘기를 하게 만드는 35~45초짜리.

# 시리즈 지침과 후보
{brief}

# 이번에 쓸 수 있는 계산 (이것으로 계산되는 주제만 고른다)
매일 쓰는 돈(daily_won, 원) × 30일을 start(YYYY-MM)부터 매달 자산(ticker)에 넣었다고 보고, 코드가 아래 값을 계산한다.
자산 ticker: {assets}
자리 표시(이것만 숫자로 쓸 수 있다): {{daily}} 하루 금액 · {{start_year}} 시작 연도 · {{years}} 몇 년 · {{asset}} 자산 이름 ·
{{put}} 넣은 돈 합계 · {{value}} 지금 가치 · {{multiple}} 몇 배 · {{per_unit}} 한 단위(잔·갑·번)당 실제 가치 ·
{{units}} 산 횟수(단위 포함) · {{mdd}} 최대 낙폭(예: -63%) · {{mdd_year}} 그 해

# 규칙
- 대사·자막에 숫자를 직접 쓰지 않는다 (0~9 금지). 숫자는 전부 자리 표시로. "한 잔", "매일"처럼 말로는 된다.
- 반말로 친구가 놀리듯 도발. 모욕·비하·특정인 저격 금지. 매수 권유처럼 들리면 안 된다 — 8번째 장면에서 {{mdd}} 폭락을 버티기 어렵다는 현실 반전을 꼭.
- 장면 9개: ① 도발 훅 ② 작은 기준 ③ 쌓이면 ④ 다른 데 넣었다면 ⑤ 불어남(growth) ⑥ 최종 펀치 ⑦ 아픈 비교 ⑧ 현실 반전 ⑨ 댓글 미끼.
- visual: spin(물체 하나가 돎) · pile(물체가 쌓임) · growth(연도별 막대, ⑤에만) · rise(솟구침) · crash(폭락) · end(마무리, ⑨에만)
- item: cup(테이크아웃 커피잔) · coin(금화). 습관이 커피가 아니면 coin.
- head는 화면 큰 글자(12자 안팎, 줄바꿈 \\n 가능), big은 강조 숫자(자리 표시), sub는 작은 설명, tag는 big 위 꼬리표(예: "가정").
- say는 내레이션 한두 문장(장면당 25자 안팎). 대사는 자연스러운 구어체.
- daily_won 은 흔히 드는 값으로 정하고 daily_note 에 근거를 짧게 (화면에 '가정'으로 표시됨).
- 캡션: 첫 줄 훅, 핵심 2~3줄, 댓글 유도 한 줄. 숫자는 자리 표시로. 해시태그 4~5개.

# 출력 (JSON 하나만)
{{"title": "관리용 제목", "topic": "고른 후보", "ticker": "NVDA", "start": "2015-01", "daily_won": 4500,
  "daily_note": "근거", "unit": "잔", "item": "cup",
  "scenes": [{{"visual": "spin", "item": "cup", "head": "...", "big": null, "sub": null, "tag": null, "say": "..."}}],
  "caption": "...", "hashtags": ["#..."]}}"""


TEXT_KEYS = ("head", "big", "sub", "tag", "say")


def check_spec(spec: dict[str, Any]) -> list[str]:
    """틀 검사: 숫자 직접 쓰기 금지, visual·item·ticker·장면 수."""
    errs = []
    if spec.get("ticker") not in ASSETS:
        errs.append(f"ticker {spec.get('ticker')} 못 씀")
    if not re.fullmatch(r"\d{4}-\d{2}", str(spec.get("start", ""))):
        errs.append("start 형식")
    if not isinstance(spec.get("daily_won"), int) or not 500 <= spec["daily_won"] <= 200000:
        errs.append("daily_won 범위")
    scenes = spec.get("scenes") or []
    if not 6 <= len(scenes) <= 10:
        errs.append(f"장면 {len(scenes)}개")
    for i, s in enumerate(scenes):
        if s.get("visual") not in VISUALS:
            errs.append(f"{i + 1}번 visual")
        if s.get("item") and s["item"] not in ITEMS:
            errs.append(f"{i + 1}번 item")
        for k in TEXT_KEYS:
            if re.search(r"\d", re.sub(r"\{\w+\}", "", str(s.get(k) or ""))):
                errs.append(f"{i + 1}번 {k}에 숫자를 직접 씀: {s.get(k)}")
        if not s.get("say"):
            errs.append(f"{i + 1}번 say 없음")
    if re.search(r"\d", re.sub(r"\{\w+\}", "", str(spec.get("caption") or ""))):
        errs.append("캡션에 숫자를 직접 씀")
    return errs


def write(brief: str, cfg: dict[str, Any]) -> dict[str, Any]:
    run = {**cfg, "command": list(cfg["command"]) + ["--allowedTools", "WebSearch", "WebFetch"],
           "timeout_seconds": max(int(cfg.get("timeout_seconds", 300)), 600)}
    assets = ", ".join(f"{k}({v[0]})" for k, v in ASSETS.items())
    prompt = WRITE_PROMPT.format(brief=brief, assets=assets)
    for attempt in range(2):
        spec = parse_json(call_claude(prompt, run))
        errs = check_spec(spec)
        if not errs:
            return spec
        log.warning("만약에 대본 틀 오류 (%d): %s", attempt + 1, errs)
        prompt += "\n\n# 지난 출력의 오류 (고쳐서 다시)\n" + "\n".join(f"- {e}" for e in errs)
    raise ValueError("만약에 대본 틀 오류: " + "; ".join(errs))


def fill(spec: dict[str, Any], p: dict[str, str]) -> list[dict[str, Any]]:
    class Safe(dict):
        def __missing__(self, k: str) -> str:
            raise KeyError(f"모르는 자리 표시 {{{k}}}")

    out = []
    for i, s in enumerate(spec["scenes"]):
        sc = {"key": f"s{i}", "visual": s["visual"], "item": s.get("item") or spec.get("item") or "coin"}
        for k in TEXT_KEYS:
            if s.get(k):
                sc[k] = str(s[k]).format_map(Safe(p))
        out.append(sc)
    return out


# ---------------------------------------------------------------- 만들기

@dataclass
class Result:
    video: Path
    cover: Path
    caption: str
    title: str
    seconds: float


def build(spec: dict[str, Any], work: Path, today: str | None = None, voice: str = typecast.DEFAULT_VOICE,
          tts_cache: Path | None = None, node_bin: str | None = None, seed: int = 1) -> Result:
    work.mkdir(parents=True, exist_ok=True)
    name, usd = ASSETS[spec["ticker"]]
    prices = monthly_prices(spec["ticker"])
    fx = krw_monthly() if usd else None
    n = habit_invest(int(spec["daily_won"]), prices, fx, spec["start"], today or datetime.now().strftime("%Y-%m"))
    p = placeholders(n, name, spec.get("unit") or "번")
    scenes = fill(spec, p)
    (work / "numbers.json").write_text(json.dumps({**p, "end": n.end, "months": n.months}, ensure_ascii=False, indent=1))
    log.info("만약에 계산: %s", p)

    # 장면별 내레이션 → 길이
    cache = tts_cache or work / "tts"
    voices = []
    for i, sc in enumerate(scenes):
        prev = scenes[i - 1]["say"] if i else ""
        nxt = scenes[i + 1]["say"] if i + 1 < len(scenes) else ""
        f = typecast.synth(sc["say"], work / f"say{i}.mp3", cache, voice, prev, nxt)
        dur = tts.duration(f)
        min_s = 5.0 if sc["visual"] == "growth" else 2.2
        sc["frames"] = int(math.ceil(max(min_s, dur + 0.45) * FPS))
        voices.append(f)
    total = sum(sc["frames"] for sc in scenes) / FPS
    src = "주가: Yahoo Finance(수정주가)" + (" · 환율: FRED" if usd else "")
    props = {"scenes": [{k: v for k, v in sc.items() if k != "say"} for sc in scenes], "bars": n.bars,
             "footer": f"{src}\n과거 데이터로 계산한 상상 · 투자 권유 아님", "accent": "#8C6CFF"}
    (work / "props.json").write_text(json.dumps(props, ensure_ascii=False))

    # 렌더 (무음)
    silent = work / "silent.mp4"
    env = {**os.environ, "PATH": f"{node_bin or Path.home() / '.local/node/bin'}:{os.environ.get('PATH', '')}"}
    subprocess.run(["nice", "-n", "19", "npx", "remotion", "render", "WhatIf", str(silent), f"--props={work / 'props.json'}",
                    "--gl=swangle", "--concurrency=2", "--log=error"], cwd=PROJECT, env=env, check=True, timeout=3600)

    # 내레이션 이어 붙이기 + BGM (내레이션 때 줄임)
    starts, t = [], 0.0
    for sc in scenes:
        starts.append(t + 0.2)
        t += sc["frames"] / FPS
    inputs, filt = [], []
    for i, (f, st) in enumerate(zip(voices, starts, strict=True)):
        inputs += ["-i", str(f)]
        filt.append(f"[{i}:a]adelay={int(st * 1000)}|{int(st * 1000)}[v{i}]")
    filt.append("".join(f"[v{i}]" for i in range(len(voices))) +
                f"amix=inputs={len(voices)}:normalize=0,apad,atrim=0:{total:.2f}[voice]")
    voice_wav = work / "voice.wav"
    compose.run_ffmpeg([*inputs, "-filter_complex", ";".join(filt), "-map", "[voice]", "-ar", "44100", str(voice_wav)])
    music = bgm.write(total, seed, work / "bgm.wav")
    video = work / "whatif.mp4"
    compose.run_ffmpeg(["-i", str(silent), "-i", str(voice_wav), "-i", str(music), "-filter_complex",
                        "[2:a]volume=0.55[m];[m][1:a]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
                        "[duck][1:a]amix=inputs=2:normalize=0[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(video)])
    cover = compose.video_cover(video, 1.2, work / "cover.jpg")

    class Safe(dict):
        def __missing__(self, k: str) -> str:
            return ""

    tags = " ".join(str(h) for h in (spec.get("hashtags") or [])[:5])
    caption = (f"{str(spec.get('caption') or '').format_map(Safe(p)).strip()}\n\n"
               f"계산: 하루 {p['daily']}({spec.get('daily_note') or '가정'}) × 30일을 {n.start[:4]}년 {int(n.start[5:])}월부터 "
               f"매달 {name}에 넣었다고 가정 · {n.end[:4]}년 {int(n.end[5:])}월까지 {n.months}개월\n"
               f"{DISCLAIMER}\n출처: {src}\n\n{tags}").strip()
    title = f"{spec.get('title') or spec.get('topic') or '만약에'}"
    return Result(video, cover, caption, title, total)
