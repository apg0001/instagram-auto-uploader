"""주간 깃허브 인기 레포 Top 5 릴스.

장면: 표지 → 5위 → … → 1위 → 한눈에 보기 → 마무리. 장면마다 TTS 내레이션, 밑에 합성 BGM(목소리 나올 때 자동으로 작아짐).
숫자(별 개수·순위)는 모두 수집한 데이터에서 코드로 넣고, Claude는 한국어 설명·읽는 법·캡션만 쓴다.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import bgm, themes, tts
from .caption import CaptionError, call_claude, parse_json
from .compose import band, chip, font, text_block
from .sources.github_trending import Repo, change

log = logging.getLogger("uploader")
W, H = 1080, 1920
TOP = 5
MAX_HASHTAGS = 8

SCRIPT_PROMPT = """너는 개발자 인스타그램 릴스 작가다. 이번 주 GitHub Trending(주간)에서 별이 많이 늘어난 레포 후보다.
각 레포를 비전공자도 알아듣게 한국어로 소개하는 원고를 써라. 숫자(별 개수·순위)는 내가 따로 넣으니 쓰지 마라.

# 후보 (이번 주 별 증가순)
{cands}

# 규칙
- exclude: 크랙·불법 우회·악성코드 의심, 성인물, 코인 홍보, 저작권 침해 자료 모음, 별이 비정상적으로 늘어난 것 같은 레포는 true (이유를 reason에).
  보안 연구·교육용 도구는 정상. 확실하지 않으면 false.
- desc: 화면에 쓸 한 줄 설명, 35자 이내, "~하는 도구" 처럼 쉬운 말. README·설명에 있는 내용만.
- say: 내레이션 한 문장, 40자 이내, 친근한 존댓말. 숫자·레포 이름은 넣지 않는다 (앞에 내가 "N위, 이름." 을 붙인다).
- name_ko: 레포 이름을 한국어로 읽는 법 (예: astryx → 아스트릭스, skills → 스킬즈). 내레이션에만 쓴다.
- trend: 이번 주 흐름 한 줄 (예: AI 에이전트 도구 강세), 25자 이내.
- caption: 첫 줄은 "이번 주 깃허브 인기 레포 Top 5" 같은 검색 키워드 훅 → 레포 다섯 개를 한 줄씩(순위 숫자 없이 이름 — 한 줄 설명)
  → "집계: GitHub Trending 주간, 이번 주 별 증가순" → 해시태그 4~5개. 투자 권유·과장 금지. 이모지 3개 이하.
  레포 목록은 exclude 가 아닌 것 중 위에서 다섯 개 순서 그대로.

# 출력 (JSON 하나만)
{{"repos": [{{"name": "owner/repo", "exclude": false, "reason": "", "desc": "...", "say": "...", "name_ko": "..."}}],
  "trend": "...", "intro_say": "표지 내레이션 한 문장 (숫자 없이)", "outro_say": "마무리 한 문장 (팔로우 유도, 과하지 않게)",
  "caption": "..."}}"""


@dataclass
class Scene:
    image: Path
    say: str
    audio: Path | None = None
    seconds: float = 3.0


def write_script(cands: list[Repo], readmes: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for r in cands:
        lines.append(f"- {r.name} | 언어 {r.language or '-'} | 설명: {r.description or '(없음)'}\n  README 앞부분: "
                     + " ".join(readmes.get(r.name, "").split())[:700])
    data = parse_json(call_claude(SCRIPT_PROMPT.format(cands="\n".join(lines)), cfg))
    if not isinstance(data.get("repos"), list) or not data.get("caption"):
        raise CaptionError("원고에 repos 또는 caption 이 없음")
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


# ---------------------------------------------------------------- 장면 그리기

def _canvas(t: dict) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    band(d, W, t["accent"])
    return img, d


def _center(d: ImageDraw.ImageDraw, text: str, y: int, f, fill) -> int:
    w = d.textlength(text, font=f)
    d.text(((W - w) / 2, y), text, font=f, fill=fill)
    return y + int(f.size * 1.25)


def scene_intro(t: dict, period: str, out: Path) -> Path:
    img, d = _canvas(t)
    chip(d, t["label"], W // 2, 230, 36, color=t["accent"])
    y = text_block(d, "이번 주 깃허브\n인기 레포", font(110), 340, t["ink"], 980, W, 1.25)
    y = _center(d, "TOP 5", y + 10, font(260), t["accent"])
    y = text_block(d, "별이 가장 많이 늘어난 오픈소스", font(48, bold=False), y + 70, t["sub"], 960, W)
    text_block(d, period, font(40, bold=False), y + 10, t["sub"], 960, W)
    img.save(out, "JPEG", quality=92)
    return out


def _change_text(c: dict, raw_rank: int) -> str | None:
    if c["new"] is None:
        return None
    if c["new"]:
        return "NEW  이번 주 처음 순위권"
    moved = c["prev_rank"] - raw_rank
    arrow = f"▲{moved}" if moved > 0 else f"▼{-moved}" if moved < 0 else "－"
    diff = c["diff_week"]
    return f"지난주 {c['prev_rank']}위 → {arrow}   증가량 {'+' if diff >= 0 else '−'}{abs(diff):,}"


def scene_repo(t: dict, rank: int, r: Repo, d_info: dict, c: dict, out: Path) -> Path:
    img, d = _canvas(t)
    y = _center(d, f"{rank}위", 210, font(190), t["accent"])
    owner, _, name = r.name.partition("/")
    y = _center(d, owner + " /", y + 10, font(46, bold=False), t["sub"])
    y = text_block(d, name, font(92 if len(name) <= 16 else 72), y + 4, t["ink"], 980, W, 1.2)
    y = text_block(d, d_info.get("desc") or r.description[:60], font(50, bold=False), y + 30, t["ink"], 940, W, 1.4)
    # 숫자 카드
    top = max(y + 60, 1000)
    d.rounded_rectangle((60, top, W - 60, top + 330), radius=36, fill=t["card"])
    cols = [("이번 주 ★", f"+{r.stars_week:,}", t["accent"]), ("누적 ★", f"{r.stars_total:,}", t["ink"]),
            ("언어", r.language or "-", t["ink"])]
    cw = (W - 120) / 3
    for i, (lab, val, col) in enumerate(cols):
        cx = 60 + cw * i + cw / 2
        f1, f2 = font(36, bold=False), font(62 if len(val) <= 8 else 46)
        d.text((cx - d.textlength(lab, font=f1) / 2, top + 70), lab, font=f1, fill=t["sub"])
        d.text((cx - d.textlength(val, font=f2) / 2, top + 150), val, font=f2, fill=col)
    msg = _change_text(c, r.rank)
    if msg:
        chip(d, msg, W // 2, top + 380, 36, color=t["accent"])
    img.save(out, "JPEG", quality=92)
    return out


def scene_summary(t: dict, picks: list[tuple[Repo, dict]], trend: str, out: Path) -> Path:
    img, d = _canvas(t)
    y = text_block(d, "한눈에 보기", font(96), 230, t["ink"], 980, W)
    y = text_block(d, "이번 주에 늘어난 별", font(44, bold=False), y, t["sub"], 960, W)
    top = max(r.stars_week for r, _ in picks)
    y += 50
    for i, (r, _) in enumerate(picks, 1):
        name = r.name.split("/")[1]
        f = font(42)
        label = name if d.textlength(name, font=f) < 860 else name[:22] + "…"
        d.text((90, y), f"{i}  {label}", font=f, fill=t["ink"])
        y += 62
        bw = int(720 * r.stars_week / top)
        d.rounded_rectangle((90, y, 90 + max(bw, 24), y + 46), radius=23, fill=t["accent"])
        d.text((90 + max(bw, 24) + 18, y), f"+{r.stars_week:,}", font=font(40), fill=t["ink"])
        y += 100
    if trend:
        chip(d, trend, W // 2, y + 30, 40, color=t["accent"])
    img.save(out, "JPEG", quality=92)
    return out


def scene_outro(t: dict, day: date, out: Path) -> Path:
    img, d = _canvas(t)
    y = text_block(d, "매주 일요일\n깃허브 인기 레포", font(100), 520, t["ink"], 980, W, 1.25)
    y = text_block(d, "팔로우하고 다음 주도 받아 보세요", font(50, bold=False), y + 40, t["sub"], 960, W)
    y = chip(d, "출처: GitHub Trending (주간)", W // 2, y + 90, 38, color=t["accent"])
    text_block(d, f"{day:%Y.%m.%d} 기준 · 이번 주 별 증가순", font(38, bold=False), y + 26, t["sub"], 960, W)
    img.save(out, "JPEG", quality=92)
    return out


# ---------------------------------------------------------------- 조립

def _ff(args: list[str]) -> None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {p.stderr[-800:]}")


def voice(scenes: list[Scene], work: Path, voice_name: str, rate: str) -> bool:
    """장면마다 TTS. 하나라도 실패하면 음성 없이(자막·BGM만) 간다 → False."""
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
    # 영상: 장면 이미지를 길이만큼 이어 붙인다
    lst = work / "scenes.txt"
    lst.write_text("".join(f"file '{s.image}'\nduration {s.seconds:.3f}\n" for s in scenes)
                   + f"file '{scenes[-1].image}'\n", encoding="utf-8")
    # 내레이션: 장면 시작 시각에 맞춰 깔기 (0.25초 늦게 시작)
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
    # BGM은 목소리가 나올 때 사이드체인으로 줄인다
    _ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(narr), "-i", str(music),
         "-filter_complex",
         "[0:v]fps=30,format=yuv420p[v];[1:a]asplit=2[n1][n2];[2:a]volume=0.32[m];"
         "[m][n1]sidechaincompress=threshold=0.03:ratio=8:attack=30:release=350[md];"
         "[md][n2]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    return out


def build(repos: list[Repo], prev: dict[str, Repo], readmes: dict[str, str], day: date, out_dir: Path,
          claude_cfg: dict[str, Any], voice_name: str = tts.DEFAULT_VOICE, rate: str = "+15%",
          seed: int | None = None) -> tuple[Path, Path, str, list[str]]:
    """릴스 경로, 커버, 캡션, 장면 설명을 돌려준다."""
    t = themes.get("tech")
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    script = write_script(repos[:9], readmes, claude_cfg)
    problems = validate_caption(script["caption"])
    if problems:
        raise CaptionError("캡션 검증 실패: " + ", ".join(problems))
    picks = pick(repos, script)
    period = f"{day - timedelta(days=6):%m.%d} ~ {day:%m.%d} · GitHub Trending 주간"

    scenes = [Scene(scene_intro(t, period, out_dir / "cover.jpg"),
                    script.get("intro_say") or "이번 주 깃허브에서 별이 가장 많이 늘어난 레포 다섯 개, 5위부터 볼게요.")]
    desc = ["표지: 이번 주 깃허브 인기 레포 TOP 5"]
    for rank in range(TOP, 0, -1):
        r, info = picks[rank - 1]
        c = change(r, prev)
        img = scene_repo(t, rank, r, info, c, work / f"rank{rank}.jpg")
        say = f"{rank}위, {info.get('name_ko') or r.name.split('/')[1]}. {info.get('say', '')} 이번 주에만 별이 {r.stars_week:,}개 늘었어요."
        scenes.append(Scene(img, say))
        desc.append(f"{rank}위 {r.name} — {info.get('desc', '')} (+{r.stars_week:,})")
    scenes.append(Scene(scene_summary(t, picks, script.get("trend", ""), work / "summary.jpg"),
                        f"정리하면, 이번 주는 {script.get('trend') or '이런 레포들이 인기였어요'}."))
    scenes.append(Scene(scene_outro(t, day, work / "outro.jpg"),
                        script.get("outro_say") or "매주 일요일에 정리해 드릴게요."))
    desc += ["한눈에 보기 막대그래프", "마무리: 출처·팔로우"]

    voiced = voice(scenes, work, voice_name, rate)
    seed = seed if seed is not None else int(day.strftime("%Y%m%d"))
    reel = assemble(scenes, out_dir / "reel.mp4", work, seed)
    if not voiced:
        desc.append("(TTS 실패로 음성 없이 BGM만)")
    return reel, out_dir / "cover.jpg", script["caption"].strip(), desc
