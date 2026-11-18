"""주간 깃허브 인기 레포 Top 5 — 릴스(릴스 탭) + 캐러셀(피드).

릴스 장면: 표지 → 5위 → … → 1위 → 한눈에 보기 → 마무리. 장면마다 TTS 내레이션, 밑에 합성 BGM(목소리 나올 때 자동으로 작아짐).
레포 장면은 별 숫자가 올라가고, 한눈에 보기는 막대가 자라난다. 자막은 넣지 않는다(사용자 결정).
캐러셀: 표지 → 1위~5위 한 장씩(설명·이런 분께·써 보는 법·통계) → 한눈에 보기 → 출처.
숫자(별·순위·만든 날)는 모두 데이터에서 코드로 넣고, Claude는 한국어 설명·태그·읽는 법·캡션만 쓴다.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import bgm, themes, tts
from .caption import CaptionError, call_claude, parse_json
from .compose import band, chip, font, text_block
from .sources.github_trending import Repo, change, insight

log = logging.getLogger("uploader")
W, H = 1080, 1920
TOP = 5
MAX_HASHTAGS = 8
FPS = 30
ANIM = 18  # 등장 애니메이션 프레임 수 (0.6초)
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

SCRIPT_PROMPT = """너는 개발자 인스타그램 작가다. 이번 주 GitHub Trending(주간)에서 별이 많이 늘어난 레포 후보다.
각 레포를 비전공자도 알아듣게 한국어로 소개하는 원고를 써라. 숫자(별 개수·순위·날짜)는 내가 따로 넣으니 쓰지 마라.

# 후보 (이번 주 별 증가순)
{cands}

# 규칙
- exclude: 크랙·불법 우회·악성코드 의심, 성인물, 코인 홍보, 저작권 침해 자료 모음, 별이 비정상적으로 늘어난 것 같은 레포는 true (이유를 reason에).
  보안 연구·교육용 도구는 정상. 확실하지 않으면 false.
- desc: 화면에 쓸 한 줄 설명, 32자 이내, "~하는 도구" 처럼 쉬운 말. README·설명에 있는 내용만.
- who: 이런 분께 좋아요 — 대상 한 줄, 28자 이내 (예: 디자인 시스템을 처음 만드는 프론트엔드 개발자).
- install: README에 실제로 적힌 설치·실행 명령 한 줄 그대로 (없거나 길면 ""). 지어내지 않는다.
- features: README에 적힌 핵심 기능 3개, 각 22자 이내 쉬운 말 (예: 컴포넌트를 AI가 바로 조립). README에 없는 건 쓰지 않는다.
- tags: 주제 키워드 2개, 각 8자 이내, # 없이 (예: AI에이전트, 디자인시스템).
- say: 내레이션 한 문장, 40자 이내, 친근한 존댓말. 숫자·레포 이름은 넣지 않는다 (앞에 내가 "N위, 이름." 을 붙인다).
- name_ko: 레포 이름을 한국어로 읽는 법 (예: astryx → 아스트릭스). 내레이션에만 쓴다.
- trend: 이번 주 흐름 한 줄 (예: AI 에이전트 도구 강세), 20자 이내.
- caption_reel: 릴스 캡션. 첫 줄 검색 키워드 훅(예: 이번 주 깃허브 인기 레포 Top 5) → 레포 이름 다섯 줄(순위 숫자 없이)
  → "자세한 정리는 피드 캐러셀에서" → 해시태그 4~5개. 짧게.
- caption_carousel: 캐러셀 캡션. 첫 줄 검색 키워드 훅 → 레포 다섯 개를 한 줄씩(이름 — 한 줄 설명) → "집계: GitHub Trending 주간, 이번 주 별 증가순"
  → "저장해 두고 하나씩 써 보세요" 같은 한 줄 → 해시태그 4~5개.
- 두 캡션 모두 투자 권유·과장 금지, 이모지 3개 이하. 레포 목록은 exclude 가 아닌 것 중 위에서 다섯 개 순서 그대로.

# 출력 (JSON 하나만)
{{"repos": [{{"name": "owner/repo", "exclude": false, "reason": "", "desc": "...", "who": "...", "install": "...",
             "tags": ["...", "..."], "features": ["...", "...", "..."], "say": "...", "name_ko": "..."}}],
  "trend": "...", "intro_say": "표지 내레이션 한 문장 (숫자 없이)", "outro_say": "마무리 한 문장 (팔로우 유도, 과하지 않게)",
  "caption_reel": "...", "caption_carousel": "..."}}"""


@dataclass
class Scene:
    frames: list[Path]           # 앞쪽은 애니메이션(1/30초씩), 마지막 프레임을 남은 시간 동안 보여 준다
    say: str
    audio: Path | None = None
    seconds: float = 3.0


@dataclass
class Pick:
    repo: Repo
    info: dict
    meta: dict = field(default_factory=dict)
    chg: dict = field(default_factory=dict)
    fact: str = ""


def write_script(cands: list[Repo], readmes: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for r in cands:
        lines.append(f"- {r.name} | 언어 {r.language or '-'} | 설명: {r.description or '(없음)'}\n  README 앞부분: "
                     + " ".join(readmes.get(r.name, "").split())[:900])
    data = parse_json(call_claude(SCRIPT_PROMPT.format(cands="\n".join(lines)), cfg))
    if not isinstance(data.get("repos"), list) or not data.get("caption_reel") or not data.get("caption_carousel"):
        raise CaptionError("원고에 repos 또는 캡션이 없음")
    return data


def validate_caption(text: str) -> list[str]:
    p = []
    if not text.strip():
        p.append("빈 캡션")
    if len(text) > 2200:
        p.append("2200자 초과")
    if text.count("#") > MAX_HASHTAGS:
        p.append(f"해시태그 {MAX_HASHTAGS}개 초과")
    return p


def pick(repos: list[Repo], script: dict[str, Any]) -> list[tuple[Repo, dict]]:
    info = {d.get("name"): d for d in script["repos"]}
    out = []
    for r in repos:
        d = info.get(r.name)
        if d is None or d.get("exclude"):
            if d and d.get("exclude"):
                log.info("제외 %s: %s", r.name, d.get("reason"))
            continue
        out.append((r, d))
        if len(out) == TOP:
            break
    if len(out) < TOP:
        raise RuntimeError(f"소개할 레포가 {len(out)}개뿐")
    return out


def _change_text(c: dict, raw_rank: int) -> str | None:
    if not c or c.get("new") is None:
        return None
    if c["new"]:
        return "NEW  이번 주 처음 순위권"
    moved = c["prev_rank"] - raw_rank
    arrow = f"▲{moved}" if moved > 0 else f"▼{-moved}" if moved < 0 else "－"
    diff = c["diff_week"]
    return f"지난주 {c['prev_rank']}위 → {arrow}   증가량 {'+' if diff >= 0 else '−'}{abs(diff):,}"


def _install(text: str) -> str:
    t = (text or "").strip().strip("`").strip()
    return t if 0 < len(t) <= 60 and "\n" not in t else ""


# ---------------------------------------------------------------- 릴스 장면

def _ease(p: float) -> float:
    return 1 - (1 - p) ** 3


def _canvas(t: dict) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    band(d, W, t["accent"])
    return img, d


def _center(d: ImageDraw.ImageDraw, text: str, y: int, f, fill) -> int:
    w = d.textlength(text, font=f)
    d.text(((W - w) / 2, y), text, font=f, fill=fill)
    return y + int(f.size * 1.25)


def _progress(d: ImageDraw.ImageDraw, t: dict, rank: int) -> None:
    """위쪽 5→1 진행 점: 지나온 순위는 채우고 지금 순위는 크게."""
    xs = [W // 2 + (i - 2) * 56 for i in range(5)]
    for i, x in enumerate(xs):
        r_here = 5 - i
        if r_here == rank:
            d.ellipse((x - 14, 140 - 14, x + 14, 140 + 14), fill=t["accent"])
        elif r_here > rank:
            d.ellipse((x - 8, 140 - 8, x + 8, 140 + 8), fill=t["accent"])
        else:
            d.ellipse((x - 8, 140 - 8, x + 8, 140 + 8), outline=t["sub"], width=3)


def _chips(d: ImageDraw.ImageDraw, items: list[tuple[str, bool]], y: int, t: dict, size: int = 32) -> int:
    """가운데 정렬 칩 한 줄. (글, 채움 여부)."""
    f = font(size)
    pad, gap = 22, 14
    ws = [d.textlength(s, font=f) + pad * 2 for s, _ in items]
    x = (W - (sum(ws) + gap * (len(ws) - 1))) / 2
    for (s, solid), w in zip(items, ws, strict=True):
        box = (x, y, x + w, y + size + 22)
        if solid:
            d.rounded_rectangle(box, radius=(size + 22) // 2, fill=t["accent"])
            d.text((x + pad, y + 8), s, font=f, fill="white")
        else:
            d.rounded_rectangle(box, radius=(size + 22) // 2, outline=t["accent"], width=3, fill=t["card"])
            d.text((x + pad, y + 8), s, font=f, fill=t["accent"])
        x += w + gap
    return y + size + 22


def scene_intro(t: dict, period: str, out: Path) -> Path:
    img, d = _canvas(t)
    chip(d, t["label"], W // 2, 230, 36, color=t["accent"])
    y = text_block(d, "이번 주 깃허브\n인기 레포", font(110), 340, t["ink"], 980, W, 1.25)
    y = _center(d, "TOP 5", y + 10, font(260), t["accent"])
    y = text_block(d, "별이 가장 많이 늘어난 오픈소스", font(48, bold=False), y + 70, t["sub"], 960, W)
    text_block(d, period, font(40, bold=False), y + 10, t["sub"], 960, W)
    img.save(out, "JPEG", quality=92)
    return out


def scene_repo(t: dict, rank: int, pk: Pick, p: float, out: Path) -> Path:
    """p: 등장 진행도 0..1 (별 숫자 카운트업)."""
    r, info = pk.repo, pk.info
    img, d = _canvas(t)
    _progress(d, t, rank)
    y = _center(d, f"{rank}위", 200, font(150), t["accent"])
    owner, _, name = r.name.partition("/")
    y = _center(d, owner + " /", y + 4, font(42, bold=False), t["sub"])
    y = text_block(d, name, font(88 if len(name) <= 16 else 66), y, t["ink"], 980, W, 1.18)
    chips = ([(f"{owner} 공식", True)] if pk.meta.get("org") else []) + [(f"#{x}", False) for x in (info.get("tags") or [])[:2]]
    if chips:
        y = _chips(d, chips, y + 18, t, 30)
    y = text_block(d, info.get("desc") or r.description[:60], font(50), y + 34, t["ink"], 940, W, 1.35)
    if info.get("who"):
        y = text_block(d, "이런 분께 · " + info["who"], font(38, bold=False), y + 8, t["sub"], 940, W, 1.35)
    # 숫자 카드 (이번 주 별은 카운트업)
    feats = [f for f in (info.get("features") or []) if isinstance(f, str)][:2]
    if feats:
        y += 14
        for f_ in feats:
            y = _center(d, "✓ " + f_[:24], y, font(36, bold=False), t["ink"])
    top = max(y + 40, 900)
    d.rounded_rectangle((60, top, W - 60, top + 250), radius=36, fill=t["card"])
    week = int(r.stars_week * _ease(p))
    cols = [("이번 주 ★", f"+{week:,}", t["accent"]), ("누적 ★", f"{r.stars_total:,}", t["ink"]), ("언어", r.language or "-", t["ink"])]
    cw = (W - 120) / 3
    for i, (lab, val, col) in enumerate(cols):
        cx = 60 + cw * i + cw / 2
        f1, f2 = font(34, bold=False), font(60 if len(val) <= 8 else 44)
        d.text((cx - d.textlength(lab, font=f1) / 2, top + 50), lab, font=f1, fill=t["sub"])
        d.text((cx - d.textlength(val, font=f2) / 2, top + 120), val, font=f2, fill=col)
    y = top + 290
    lines = [pk.fact] + ([_change_text(pk.chg, r.rank)] if _change_text(pk.chg, r.rank) else [])
    for ln in lines:
        y = _center(d, "● " + ln, y, font(38), t["ink"])
    inst = _install(info.get("install"))
    if inst and y < 1480:
        f = ImageFont.truetype(MONO, 30)
        w = min(d.textlength("$ " + inst, font=f), 940)
        d.rounded_rectangle(((W - w) / 2 - 24, y + 10, (W + w) / 2 + 24, y + 70), radius=16, fill=t["ink"])
        d.text(((W - w) / 2, y + 22), ("$ " + inst)[:56], font=f, fill=t["bg"])
    img.save(out, "JPEG", quality=90)
    return out


def scene_summary(t: dict, picks: list[Pick], trend: str, p: float, out: Path) -> Path:
    img, d = _canvas(t)
    y = text_block(d, "한눈에 보기", font(96), 230, t["ink"], 980, W)
    y = text_block(d, "이번 주에 늘어난 별", font(44, bold=False), y, t["sub"], 960, W)
    top = max(pk.repo.stars_week for pk in picks)
    y += 50
    for i, pk in enumerate(picks, 1):
        r = pk.repo
        name = r.name.split("/")[1]
        f = font(42)
        label = name if d.textlength(name, font=f) < 860 else name[:22] + "…"
        d.text((90, y), f"{i}  {label}", font=f, fill=t["ink"])
        y += 62
        bw = int(720 * r.stars_week / top * _ease(p))
        d.rounded_rectangle((90, y, 90 + max(bw, 24), y + 46), radius=23, fill=t["accent"])
        if p >= 1:
            d.text((90 + max(bw, 24) + 18, y), f"+{r.stars_week:,}", font=font(40), fill=t["ink"])
        y += 100
    if trend and p >= 1:
        chip(d, trend, W // 2, y + 30, 40, color=t["accent"])
    img.save(out, "JPEG", quality=90)
    return out


def scene_outro(t: dict, day: date, out: Path) -> Path:
    img, d = _canvas(t)
    y = text_block(d, "매주 일요일\n깃허브 인기 레포", font(100), 480, t["ink"], 980, W, 1.25)
    y = text_block(d, "자세한 정리는 피드 캐러셀에서\n팔로우하고 다음 주도 받아 보세요", font(48, bold=False), y + 40, t["sub"], 960, W, 1.4)
    y = chip(d, "출처: GitHub Trending (주간)", W // 2, y + 80, 38, color=t["accent"])
    text_block(d, f"{day:%Y.%m.%d} 기준 · 이번 주 별 증가순", font(38, bold=False), y + 26, t["sub"], 960, W)
    img.save(out, "JPEG", quality=92)
    return out


def _animated(render, work: Path, tag: str) -> list[Path]:
    """render(p, out) 를 0..1 로 ANIM 프레임 + 마지막(완성) 프레임."""
    return [render(min(1.0, i / (ANIM - 1)), work / f"{tag}_{i:02d}.jpg") for i in range(ANIM)]


# ---------------------------------------------------------------- 조립

def _ff(args: list[str]) -> None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {p.stderr[-800:]}")


def voice(scenes: list[Scene], work: Path, voice_name: str, rate: str) -> bool:
    """장면마다 TTS. 하나라도 실패하면 음성 없이(BGM만) 간다 → False."""
    try:
        for i, s in enumerate(scenes):
            s.audio = tts.synth(s.say, work / f"say{i}.mp3", voice_name, rate)
            s.seconds = max(tts.duration(s.audio) + 0.5, 2.5)
        return True
    except tts.TTSError as e:
        log.warning("%s → 음성 없이 진행", e)
        for s in scenes:
            s.audio, s.seconds = None, max(2.5, len(s.say) / 7)
        return False


def assemble(scenes: list[Scene], out: Path, work: Path, seed: int) -> Path:
    total = sum(s.seconds for s in scenes)
    lst = work / "scenes.txt"
    rows = []
    for s in scenes:
        anim = s.frames[:-1]
        for f in anim:
            rows.append(f"file '{f}'\nduration {1 / FPS:.5f}\n")
        rows.append(f"file '{s.frames[-1]}'\nduration {max(0.1, s.seconds - len(anim) / FPS):.3f}\n")
    lst.write_text("".join(rows) + f"file '{scenes[-1].frames[-1]}'\n", encoding="utf-8")
    narr = work / "narration.wav"
    voiced = [(s, sum(x.seconds for x in scenes[:i])) for i, s in enumerate(scenes) if s.audio]
    if voiced:
        ins = sum((["-i", str(s.audio)] for s, _ in voiced), [])
        delays = ";".join(f"[{i}:a]aresample=44100,adelay={int((t + 0.25) * 1000)}:all=1[a{i}]" for i, (_, t) in enumerate(voiced))
        mix = "".join(f"[a{i}]" for i in range(len(voiced)))
        _ff([*ins, "-filter_complex", f"{delays};{mix}amix=inputs={len(voiced)}:normalize=0,apad=whole_dur={total:.3f}[o]",
             "-map", "[o]", "-ac", "1", "-ar", "44100", "-t", f"{total:.3f}", str(narr)])
    else:
        _ff(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{total:.3f}", str(narr)])
    music = bgm.write(total, seed, work / "bgm.wav")
    log.info("BGM %s", bgm.describe(seed))
    _ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(narr), "-i", str(music),
         "-filter_complex",
         f"[0:v]fps={FPS},format=yuv420p[v];[1:a]asplit=2[n1][n2];[2:a]volume=0.32[m];"
         "[m][n1]sidechaincompress=threshold=0.03:ratio=8:attack=30:release=350[md];"
         "[md][n2]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    return out


# ---------------------------------------------------------------- 캐러셀

def carousel_slides(picks: list[Pick], trend: str, period: str, day: date) -> list[tuple[str, dict[str, Any]]]:
    c = {"brand": "깃허브 인기 레포", "report_name": "주간 Top 5", "date_label": f"{day:%Y.%m.%d}",
         "footnote": "GitHub Trending 주간 · 이번 주 별 증가순"}
    out = [("github", {**c, "kind": "cover", "period": period,
                       "teaser": [pk.repo.name.split("/")[1] for pk in picks]})]
    for rank, pk in enumerate(picks, 1):
        owner, _, name = pk.repo.name.partition("/")
        out.append(("github", {**c, "kind": "repo", "rank": rank, "owner": owner, "name": name, "org": pk.meta.get("org"),
                               "tags": (pk.info.get("tags") or [])[:2], "desc": pk.info.get("desc") or pk.repo.description,
                               "who": pk.info.get("who"),
                               "features": [f for f in (pk.info.get("features") or []) if isinstance(f, str)][:3], "week": pk.repo.stars_week, "total": pk.repo.stars_total,
                               "lang": pk.repo.language, "insight": pk.fact, "change": _change_text(pk.chg, pk.repo.rank),
                               "install": _install(pk.info.get("install"))}))
    out.append(("github", {**c, "kind": "summary", "trend": trend,
                           "bars": [{"name": pk.repo.name.split("/")[1], "owner": pk.repo.name.split("/")[0],
                                     "week": pk.repo.stars_week} for pk in picks]}))
    out.append(("github", {**c, "kind": "sources", "sources": ["github.com/trending?since=weekly"]
                           + [f"github.com/{pk.repo.name}" for pk in picks]}))
    return out


# ---------------------------------------------------------------- 전체

@dataclass
class Result:
    reel: Path
    cover: Path
    reel_caption: str
    carousel: list[Path]
    carousel_caption: str
    desc: list[str]


def build(repos: list[Repo], prev: dict[str, Repo], readmes: dict[str, str], metas: dict[str, dict], day: date,
          out_dir: Path, claude_cfg: dict[str, Any], voice_name: str = tts.DEFAULT_VOICE, rate: str = "+15%",
          seed: int | None = None) -> Result:
    from .cards.render import render

    t = themes.get("tech")
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    script = write_script(repos[:9], readmes, claude_cfg)
    for key in ("caption_reel", "caption_carousel"):
        problems = validate_caption(script[key])
        if problems:
            raise CaptionError(f"{key} 검증 실패: " + ", ".join(problems))
    picks = [Pick(r, info, metas.get(r.name, {}), change(r, prev), insight(r, metas.get(r.name, {}), day))
             for r, info in pick(repos, script)]
    period = f"{day - timedelta(days=6):%m.%d} ~ {day:%m.%d} · GitHub Trending 주간"

    cover = scene_intro(t, period, out_dir / "cover.jpg")
    scenes = [Scene([cover], script.get("intro_say") or "이번 주 깃허브에서 별이 가장 많이 늘어난 레포 다섯 개, 5위부터 볼게요.")]
    desc = ["표지: 이번 주 깃허브 인기 레포 TOP 5"]
    for rank in range(TOP, 0, -1):
        pk = picks[rank - 1]
        frames = _animated(lambda p, o, rank=rank, pk=pk: scene_repo(t, rank, pk, p, o), work, f"rank{rank}")
        say = f"{rank}위, {pk.info.get('name_ko') or pk.repo.name.split('/')[1]}. {pk.info.get('say', '')} 이번 주에만 별이 {pk.repo.stars_week:,}개 늘었어요."
        scenes.append(Scene(frames, say))
        desc.append(f"{rank}위 {pk.repo.name} — {pk.info.get('desc', '')} (+{pk.repo.stars_week:,}, {pk.fact})")
    frames = _animated(lambda p, o: scene_summary(t, picks, script.get("trend", ""), p, o), work, "summary")
    scenes.append(Scene(frames, f"정리하면, 이번 주는 {script.get('trend') or '이런 레포들이 인기였어요'}."))
    scenes.append(Scene([scene_outro(t, day, work / "outro.jpg")], script.get("outro_say") or "매주 일요일에 정리해 드릴게요."))
    desc += ["한눈에 보기 막대그래프", "마무리: 출처·팔로우"]

    voiced = voice(scenes, work, voice_name, rate)
    seed = seed if seed is not None else int(day.strftime("%Y%m%d"))
    reel = assemble(scenes, out_dir / "reel.mp4", work, seed)
    if not voiced:
        desc.append("(TTS 실패로 음성 없이 BGM만)")

    slides = render(carousel_slides(picks, script.get("trend", ""), period, day), out_dir / "carousel", "",
                    theme=themes.css("tech"))
    return Result(reel, cover, script["caption_reel"].strip(), slides, script["caption_carousel"].strip(), desc)
