"""무료 한국어 TTS (edge-tts: Microsoft Edge 읽어주기 음성, API 키 없음).

비공식 엔드포인트라 언젠가 막힐 수 있다 → 실패하면 TTSError, 호출 쪽에서 자막만으로 진행할 수 있게 한다.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

DEFAULT_VOICE = "ko-KR-SunHiNeural"


class TTSError(RuntimeError):
    pass


def synth(text: str, out: Path, voice: str = DEFAULT_VOICE, rate: str = "+8%") -> Path:
    import edge_tts

    async def run() -> None:
        await edge_tts.Communicate(text, voice, rate=rate).save(str(out))

    try:
        asyncio.run(run())
    except Exception as e:  # noqa: BLE001 — 네트워크·서비스 오류 모두
        raise TTSError(f"TTS 실패: {e}") from e
    if not out.exists() or out.stat().st_size < 1000:
        raise TTSError("TTS 결과가 비어 있음")
    return out


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())
