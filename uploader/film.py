"""카드뉴스 → 영상 릴스 (Lemo-Opuscar, 같은 네트워크의 윈도우 PC에서 제작).

캐러셀 카드를 그대로 넘기는 slide_reel 대신, 같은 원고로 모션 그래픽 영상을 새로 만든다.
제작에 30~60분이 걸려서 비동기로 돈다:
  start()  카드뉴스를 만들 때 — 대본(Claude) → 타입캐스트 내레이션 → 윈도우로 보내고 작업 예약 실행
  poll()   `plan`(5분마다)이 부를 때 — 끝났으면 받아서 릴스 초안, 늦거나 실패하면 slide_reel로 대신
윈도우 PC는 .env FILM_SSH_HOST (예: user@100.x.x.x)로 ssh 접속한다 (Claude Code + lemo-opuscar 플러그인 설치).
작업 스케줄러 'lemo-film'이 films\\run.ps1 을 실행하고, run.ps1 은 films\\current.txt 에 적힌 폴더의 prompt.txt 로
claude -p 를 돌린다 (ssh 세션에서 바로 띄우면 훅 때문에 세션이 멈춘다).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import compose, tts, typecast
from .caption import call_claude, parse_json
from .github_reel import validate_caption

log = logging.getLogger("uploader")

JOBS = "film_jobs"            # kv: 진행 중인 작업 목록 (JSON)
TASK = "lemo-film"            # 윈도우 작업 스케줄러 이름
ROOT = r"C:\Users\USER\films"
MAX_HOURS = 3.0               # 이보다 오래 걸리면 포기
DEADLINE_HOURS = 2.0          # 게시 이만큼 전까지 안 끝나면 slide_reel로 대신

SCRIPT_PROMPT = """너는 한국어 테크 인스타그램 릴스 작가다. 아래 카드뉴스와 같은 내용을 35~45초 모션 그래픽 영상으로 만든다.
너는 내레이션 대본과 캡션만 쓴다 (화면은 다른 사람이 대본에 맞춰 만든다).

# 카드뉴스 원고
{content}

# 규칙
- lines 7~9줄. 첫 줄은 스크롤을 멈추는 훅(질문·놀라운 사실), 가운데는 핵심을 하나씩, 마지막 줄은 가볍게 저장·팔로우 유도.
- 한 줄은 해요체 1~2문장, 15~40자. 전체 합쳐 300자 이내 (말하는 시간 40초 안팎).
- text: 화면용 표기(영어 이름 그대로). say: 소리 내 읽을 표기 (영어 이름·숫자를 한국어 발음으로, 쉼표로 호흡).
- 원고에 없는 사실·숫자는 만들지 않는다.
- caption: 릴스 캡션. 첫 줄 검색 키워드가 들어간 훅 → 핵심 3~4줄 → 출처 한 줄 → 해시태그 딱 5개(검색 키워드는 본문 문장에). 900자 이내, 이모지 3개 이하.

# 출력 (JSON 하나만)
{{"lines": [{{"text": "...", "say": "..."}}], "caption": "..."}}"""

FILM_PROMPT = """/lemo-opuscar:lemo-opuscar 를 써서 세로 릴스 영상 한 편을 만들어라. 사람에게 묻지 말고 아래 답으로 진행한다 (비대화형).

- 언어: 한국어 (화면 글자도 한국어, 제품 이름만 영어 그대로)
- 스타일: 라이브러리 스타일 Dark Tech Keynote (dark-keynote)
- 스토리보드 확인: 아니요, 바로 완성까지
- 화면: 세로 1080x1920, 30fps, 길이는 내레이션에 맞춰 (35~50초)
- 자막 트랙은 넣지 않는다 (화면 핵심 문구는 스타일대로)

# 내레이션은 이미 녹음돼 있다 (중요)
- 이 폴더의 film\\lines.json 과 film\\voice\\l1.wav … l{n}.wav 가 최종 내레이션이다 (사람 목소리 TTS).
- 프로젝트 폴더는 반드시 film 으로 쓴다. TTS(tts_zh.py 등)를 돌리지 말고 voice 파일을 바꾸거나 다시 만들지 마라.
  장면 길이와 효과음은 이 파일 길이에 맞춘다. 음성 확인(asr_check)은 해도 된다.
- 최종 영상은 film\\final.mp4, 표지로 쓸 정지 화면(제목이 크게 보이는 장면)은 film\\poster.jpg 로 저장한다.

# 내용
{content}

- 사실은 위 원고에 있는 것만. 회사·제품 로고, Clawd 캐릭터, 실존 인물은 그리지 않는다 (이름은 글자로만).
- 원고의 출처: {sources}

끝나면 마지막 줄에 FINAL: <final.mp4 경로> 를 출력."""

RUN_PS1 = r"""$ErrorActionPreference = "Continue"
$dir = (Get-Content -Raw -Encoding UTF8 "$env:USERPROFILE\films\current.txt").Trim()
Set-Location $dir
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
$prompt = Get-Content -Raw -Encoding UTF8 "$dir\prompt.txt"
"START $(Get-Date -Format o)" | Out-File -Encoding utf8 "$dir\status.txt"
& "$env:USERPROFILE\.local\bin\claude.exe" -p $prompt --permission-mode bypassPermissions --output-format stream-json --verbose *> "$dir\run.log"
"END $(Get-Date -Format o) exit=$LASTEXITCODE" | Out-File -Append -Encoding utf8 "$dir\status.txt"
"""


class FilmError(RuntimeError):
    pass


def host() -> str | None:
    return os.environ.get("FILM_SSH_HOST") or None


def ssh(cmd: str, timeout: int = 60) -> str:
    """윈도우 PowerShell 명령 하나."""
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host() or "", cmd],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise FilmError(f"ssh 실패: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def scp(src: list[str], dst: str, timeout: int = 300) -> None:
    r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *src, dst],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise FilmError(f"scp 실패: {r.stderr.strip()[:300]}")


def remote(path: str) -> str:
    """scp 대상 경로 (C:/Users/USER/films/...)."""
    return f"{host()}:C:/Users/USER/films/{path}"


def write_script(content: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    body = {k: content.get(k) for k in ("title", "slides", "sources")}
    prompt = SCRIPT_PROMPT.format(content=json.dumps(body, ensure_ascii=False)[:12000])
    last: list[str] = []
    for _ in range(2):
        data = parse_json(call_claude(prompt, cfg))
        lines = [{"text": str(x.get("text", "")).strip(), "say": str(x.get("say") or x.get("text", "")).strip()}
                 for x in data.get("lines") or [] if isinstance(x, dict)]
        lines = [x for x in lines if x["text"]]
        last = validate_caption(str(data.get("caption", "")))
        if not 5 <= len(lines) <= 10:
            last.append(f"lines가 {len(lines)}줄 (7~9줄)")
        if not last:
            return {"lines": lines, "caption": str(data["caption"])}
        prompt += "\n\n# 직전 결과의 문제 (고쳐서 다시)\n- " + "\n- ".join(last)
    raise ValueError("영상 대본 검증 실패: " + ", ".join(last))


def record_voice(lines: list[dict[str, str]], out: Path, voice_id: str = typecast.DEFAULT_VOICE) -> dict[str, str]:
    """줄마다 타입캐스트 → voice/l{i}.wav (48kHz 모노). 반환: 파일 이름 → md5 (윈도우에서 바뀌었는지 확인용)."""
    if not os.environ.get("TYPECAST_API_KEY"):
        raise FilmError("TYPECAST_API_KEY 없음 (영상 내레이션은 타입캐스트로만)")
    (out / "voice").mkdir(parents=True, exist_ok=True)
    says = [x["say"] for x in lines]
    sums = {}
    for i, say in enumerate(says):
        mp3 = typecast.synth(say, out / f"l{i + 1}.mp3", out / "tts", voice_id,
                             says[i - 1] if i else "", says[i + 1] if i + 1 < len(says) else "")
        if mp3.parent.name != "tts":  # 캐시(타입캐스트)가 아니면 edge-tts로 대신 읽은 것
            raise FilmError("타입캐스트 실패")
        wav = out / "voice" / f"l{i + 1}.wav"
        compose.run_ffmpeg(["-i", str(mp3), "-ac", "1", "-ar", "48000", str(wav)])
        sums[wav.name] = hashlib.md5(wav.read_bytes()).hexdigest()
    (out / "lines.json").write_text(json.dumps([{"id": f"l{i + 1}", **x} for i, x in enumerate(lines)],
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    return sums


def film_prompt(content: dict[str, Any], n: int) -> str:
    body = {k: content.get(k) for k in ("title", "subtitle", "slides")}
    src = "; ".join(str(s.get("name") or s.get("title") or s.get("url") or s) if isinstance(s, dict) else str(s)
                    for s in content.get("sources") or []) or "원고 참고"
    return FILM_PROMPT.format(n=n, content=json.dumps(body, ensure_ascii=False, indent=1)[:10000], sources=src[:1500])


def busy() -> bool:
    return "Running" in ssh(f"(Get-ScheduledTask {TASK}).State")


def launch(job: dict[str, Any]) -> None:
    """작업 폴더를 current.txt 에 적고 예약 작업을 실행한다 (run.ps1 은 매번 덮어써 최신으로)."""
    local = Path(job["local"])
    (local / "run.ps1").write_text(RUN_PS1, encoding="utf-8-sig")
    (local / "current.txt").write_text(f"C:\\Users\\USER\\films\\{job['name']}", encoding="utf-8")
    scp([str(local / "run.ps1"), str(local / "current.txt")], remote(""))
    ssh(f"$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass "
        f"-WindowStyle Hidden -File \"{ROOT}\\run.ps1\"'; "
        f"Register-ScheduledTask -TaskName {TASK} -Action $a -Force | Out-Null; Start-ScheduledTask {TASK}")
    job.update(state="running", started=time.time())
    log.info("영상 제작 시작 %s", job["name"])


def start(pl: Any, content: dict[str, Any], cards: list[Path], source: str, title: str, theme: str,
          reel_at: float) -> dict[str, Any]:
    """대본·내레이션을 만들어 윈도우로 보내고 (비어 있으면) 바로 제작을 시작한다. 실패하면 FilmError."""
    if not host():
        raise FilmError("FILM_SSH_HOST 없음")
    name = f"{source}-{time.strftime('%Y%m%d-%H%M%S')}".replace(":", "-")
    local = pl.cfg.data_dir / "films" / name
    film = local / "film"
    film.mkdir(parents=True, exist_ok=True)
    script = write_script(content, pl.cfg.caption)
    sums = record_voice(script["lines"], film, pl.cfg.raw.get("whatif", {}).get("voice", typecast.DEFAULT_VOICE))
    (local / "prompt.txt").write_text(film_prompt(content, len(script["lines"])), encoding="utf-8")
    (local / "content.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    ssh(f"New-Item -ItemType Directory -Force '{ROOT}\\{name}\\film\\voice' | Out-Null")
    scp([str(local / "prompt.txt")], remote(f"{name}/"))
    scp([str(film / "lines.json")], remote(f"{name}/film/"))
    scp([str(p) for p in sorted((film / "voice").glob("*.wav"))], remote(f"{name}/film/voice/"))
    job = {"name": name, "local": str(local), "source": source, "title": title, "theme": theme, "reel_at": reel_at,
           "caption": script["caption"], "cards": [str(c) for c in cards], "voice": sums, "state": "queued",
           "created": time.time()}
    if not busy():
        launch(job)
    jobs = load(pl)
    jobs.append(job)
    save(pl, jobs)
    return job


def load(pl: Any) -> list[dict[str, Any]]:
    try:
        return json.loads(pl.db.kv_get(JOBS) or "[]")
    except ValueError:
        return []


def save(pl: Any, jobs: list[dict[str, Any]]) -> None:
    pl.db.kv_set(JOBS, json.dumps(jobs, ensure_ascii=False))


def check(job: dict[str, Any]) -> str:
    """원격 상태: running | done | failed."""
    base = f"{ROOT}\\{job['name']}"
    out = ssh(f"$s = Get-Content -Raw '{base}\\status.txt' -ErrorAction SilentlyContinue; "
              f"$st = (Get-ScheduledTask {TASK}).State; "
              f"$f = Test-Path '{base}\\film\\final.mp4'; \"$st|$f|$s\"")
    state, final, status = (out.strip().split("|", 2) + ["", ""])[:3]
    if state == "Running" and "END" not in status:
        return "running"
    if final == "True":
        return "done"
    return "failed"


def fetch(job: dict[str, Any]) -> tuple[Path, Path | None]:
    """완성본·표지를 받아 오고, 내레이션이 우리가 보낸 그대로인지 확인한다."""
    base = f"{ROOT}\\{job['name']}\\film"
    sums = ssh(f"Get-ChildItem '{base}\\voice\\l*.wav' | ForEach-Object {{ $_.Name + ' ' + "
               f"(Get-FileHash $_.FullName -Algorithm MD5).Hash.ToLower() }}")
    got = dict(line.split() for line in sums.splitlines() if line.strip())
    if any(got.get(k) != v for k, v in job["voice"].items()):
        raise FilmError("윈도우에서 내레이션 파일이 바뀜 (타입캐스트 목소리가 아님)")
    out = Path(job["local"]) / "out"
    out.mkdir(exist_ok=True)
    scp([remote(f"{job['name']}/film/final.mp4")], str(out / "final.mp4"))
    poster = out / "poster.jpg"
    try:
        scp([remote(f"{job['name']}/film/poster.jpg")], str(poster))
    except FilmError:
        poster = None
    return out / "final.mp4", poster


def verify(video: Path) -> None:
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    dur = float(info.get("format", {}).get("duration") or 0)
    if (v.get("width"), v.get("height")) != (1080, 1920):
        raise FilmError(f"화면 크기 {v.get('width')}x{v.get('height')}")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise FilmError("소리 없음")
    if not 15 <= dur <= 90:
        raise FilmError(f"길이 {dur:.1f}초")


def finish(pl: Any, job: dict[str, Any]) -> Any:
    from . import photos

    video, poster = fetch(job)
    verify(video)
    cover = Path(job["local"]) / "out" / "cover.jpg"
    if poster and poster.exists():
        cover.write_bytes(poster.read_bytes())
    else:
        compose.video_cover(video, min(9.5, tts.duration(video) / 3), cover)
    content = json.loads((Path(job["local"]) / "content.json").read_text(encoding="utf-8"))
    caption = job["caption"]
    if content.get("photo_credit"):
        pcs = content["photo_credit"]
        pcs = pcs if isinstance(pcs, list) else [pcs]
        caption = photos.add_credit(caption, [photos.Photo(Path("cover" if pc.get("cover", True) else "slide"),
                                                           pc["author"], pc["page"]) for pc in pcs])
    pid, work = pl._new_id(job["source"], "reels")
    staged = work / "out" / "1.mp4"
    staged.write_bytes(video.read_bytes())
    return pl._finalize(pid, job["source"], "REELS", f"[{job['title']} · 영상] {content.get('title', '')}", caption,
                        [staged], ["video"], cover, {"check": "none", "topic_theme": job["theme"], "film": job["name"]},
                        job["reel_at"])


def fallback(pl: Any, job: dict[str, Any], why: str) -> None:
    """영상이 안 되면 예전처럼 카드로 릴스를 만든다."""
    log.warning("영상 릴스 실패(%s) → 카드 릴스로 대신 %s", why, job["name"])
    pl.record_failure(f"{job['source']}:film", FilmError(why))
    try:
        content = json.loads((Path(job["local"]) / "content.json").read_text(encoding="utf-8"))
        pl.generate_slide_reel(content, [Path(c) for c in job["cards"]], job["source"], job["title"], job["theme"],
                               job["reel_at"])
    except Exception as e:  # noqa: BLE001
        log.exception("카드 릴스도 실패")
        pl.record_failure(f"{job['source']}:reel", e)


def poll(pl: Any) -> None:
    """진행 중인 작업 확인 (한 번에 하나씩). 끝난 건 초안으로, 늦거나 실패한 건 카드 릴스로."""
    jobs = load(pl)
    if not jobs:
        return
    now = time.time()
    keep = []
    for job in jobs:
        late = now > job["reel_at"] - DEADLINE_HOURS * 3600
        try:
            if job["state"] == "running":
                st = check(job)
                if st == "running" and not late and now - job["started"] < MAX_HOURS * 3600:
                    keep.append(job)
                    continue
                if st == "done":
                    try:
                        finish(pl, job)
                    except (FilmError, subprocess.SubprocessError, OSError, ValueError) as e:
                        fallback(pl, job, f"받기·검사 실패: {e}")
                    continue
                if st == "running":
                    ssh(f"Stop-ScheduledTask {TASK}")
                fallback(pl, job, "시간 초과" if st == "running" else "제작 실패 (final.mp4 없음)")
                continue
            if late:
                fallback(pl, job, "대기 중 게시 시각이 다가옴")
                continue
            keep.append(job)
        except (FilmError, subprocess.SubprocessError, OSError, ValueError) as e:
            if late or now - job.get("started", job["created"]) > MAX_HOURS * 3600:
                fallback(pl, job, str(e))
            else:
                log.warning("영상 작업 확인 실패 %s: %s (다음에 다시)", job["name"], e)
                keep.append(job)
    queued = [j for j in keep if j["state"] == "queued"]
    if queued and not any(j["state"] == "running" for j in keep):
        try:
            if not busy():
                launch(queued[0])
        except (FilmError, subprocess.SubprocessError, OSError) as e:
            log.warning("영상 제작 시작 실패: %s", e)
    save(pl, keep)
