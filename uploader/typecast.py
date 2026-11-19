"""타입캐스트(Typecast) 한국어 내레이션 — 문맥 감정(Smart) 모드.

.env TYPECAST_API_KEY 가 있어야 한다. 무료 플랜은 매달 15,000 크레딧, 한국어 1자 ≈ 1크레딧.
같은 목소리·문장은 cache_dir 에 저장해 다시 렌더해도 크레딧을 쓰지 않는다.
키가 없거나 실패하면 edge-tts(무료)로 대신 읽는다.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import requests

from . import tts

log = logging.getLogger("uploader")

API = "https://api.typecast.ai/v1/text-to-speech"
# 코딩 에이전트가 만든 연동임을 알리는 고정 표기 (타입캐스트 문서 권장)
UA = "typecast-direct/1 requests typecast-integration/1 (source=api-docs; generated_by=claude-code)"
DEFAULT_VOICE = "tc_692799c46508f6b9468c54c7"  # 다은 (릴스·쇼츠, 20대 여성)


def synth(text: str, out: Path, cache_dir: Path, voice: str = DEFAULT_VOICE, prev: str = "", nxt: str = "",
          tempo: float = 1.1, fallback_voice: str = "ko-KR-SunHiNeural", fallback_rate: str = "+12%") -> Path:
    """text 를 mp3 로. 반환 경로는 캐시 파일(타입캐스트) 또는 out(edge-tts 대체)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / (hashlib.sha1(f"{voice}|{tempo}|{text}".encode()).hexdigest()[:16] + ".mp3")
    if f.exists():
        return f
    key = os.environ.get("TYPECAST_API_KEY")
    if key:
        body = {"voice_id": voice, "text": text, "model": "ssfm-v31", "language": "kor",
                "prompt": {"emotion_type": "smart", "previous_text": prev, "next_text": nxt},
                "output": {"audio_format": "mp3", "audio_tempo": tempo}}
        try:
            r = requests.post(API, headers={"X-API-KEY": key, "User-Agent": UA}, json=body, timeout=120)
            if r.ok and r.content:
                f.write_bytes(r.content)
                return f
            log.warning("타입캐스트 실패 %s: %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("타입캐스트 오류: %s", e)
    log.warning("타입캐스트 대신 edge-tts 사용: %s", text[:30])
    return tts.synth(text, out, fallback_voice, fallback_rate)
