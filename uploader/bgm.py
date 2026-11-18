"""릴스 배경음악을 코드로 합성한다 — 저작권 걱정 없고, 시드마다 조성·템포·코드 진행·드럼·음색이 달라진다.

로파이/신스팝 느낌의 4마디 루프를 만들어 길이만큼 반복하고, 끝은 페이드아웃.
numpy만 쓰므로 파이에서도 1분짜리가 몇 초면 만들어진다.
"""
from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

SR = 44100

# 코드 진행 (조성의 음계 도수, 0=I). 밝은 것과 살짝 감성적인 것을 섞는다.
PROGRESSIONS = [
    [0, 5, 3, 4],   # I vi IV V
    [0, 4, 5, 3],   # I V vi IV
    [5, 3, 0, 4],   # vi IV I V
    [0, 3, 4, 3],   # I IV V IV
    [1, 4, 0, 5],   # ii V I vi
    [0, 2, 5, 4],   # I iii vi V
]
MAJOR = [0, 2, 4, 5, 7, 9, 11]
DRUMS = {  # 16분음표 16칸: 킥, 스네어, 하이햇
    "four": ("1000100010001000", "0000100000001000", "1010101010101010"),
    "boom": ("1000001010000000", "0000100000001000", "1011101010111010"),
    "half": ("1000000000100000", "0000000010000000", "1010101010101010"),
    "skip": ("1000000110000100", "0000100000001001", "0010001000100010"),
}


def _note_hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a)[: n]
    if r < n:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def _tone(hz: float, dur: float, kind: str, bright: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    if kind == "saw":  # 배음 몇 개로 부드러운 톱니
        w = sum(np.sin(2 * np.pi * hz * k * t) / k * bright ** (k - 1) for k in range(1, 6))
    elif kind == "square":
        w = sum(np.sin(2 * np.pi * hz * k * t) / k * bright ** (k - 1) for k in (1, 3, 5))
    elif kind == "epiano":
        w = np.sin(2 * np.pi * hz * t + 0.8 * np.exp(-3 * t) * np.sin(2 * np.pi * hz * 2 * t))
    else:  # sine pluck
        w = np.sin(2 * np.pi * hz * t)
    return w


def _kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * (50 + 90 * np.exp(-30 * t)) * t) * np.exp(-9 * t)


def _noise_hit(n: int, decay: float, rng: np.random.Generator, hp: bool) -> np.ndarray:
    x = rng.standard_normal(n)
    if hp:
        x = np.diff(x, prepend=0)
    return x * np.exp(-decay * np.arange(n) / SR)


def _add(buf: np.ndarray, x: np.ndarray, at: int, gain: float) -> None:
    end = min(len(buf), at + len(x))
    if at < end:
        buf[at:end] += x[: end - at] * gain


def compose(seconds: float, seed: int) -> np.ndarray:
    """seconds 길이의 스테레오가 아닌 모노 float 배열(-1..1)."""
    rnd = random.Random(seed)
    rng = np.random.default_rng(seed)
    bpm = rnd.choice([84, 88, 92, 96, 100, 104, 110, 118])
    root = 48 + rnd.randrange(12)                  # C3 ~ B3
    prog = rnd.choice(PROGRESSIONS)
    kick_p, snare_p, hat_p = DRUMS[rnd.choice(list(DRUMS))]
    pad_kind = rnd.choice(["saw", "epiano", "sine"])
    lead_kind = rnd.choice(["square", "sine", "epiano"])
    bright = rnd.uniform(0.35, 0.7)
    arp_shape = rnd.choice([[0, 1, 2, 1], [0, 2, 1, 2], [0, 1, 2, 3], [2, 1, 0, 1]])
    arp_rate = rnd.choice([2, 4])                  # 박당 음 수

    beat = 60 / bpm
    step = beat / 4
    bar = beat * 4
    loop = np.zeros(int(bar * 4 * SR) + SR)

    for b, degree in enumerate(prog):
        t0 = int(b * bar * SR)
        chord = [root + MAJOR[(degree + i) % 7] + 12 * ((degree + i) // 7) for i in (0, 2, 4)]
        # 패드 (코드 전체, 한 마디)
        n = int(bar * SR)
        pad = sum(_tone(_note_hz(m + 12), bar, pad_kind, bright) for m in chord) * _env(n, 0.08, 0.3)
        _add(loop, pad, t0, 0.08)
        # 베이스 (근음, 박마다)
        for k in range(4):
            nb = int(beat * 0.9 * SR)
            bass = _tone(_note_hz(chord[0] - 12), beat * 0.9, "sine", 0.3) * _env(nb, 0.01, 0.15)
            _add(loop, bass, t0 + int(k * beat * SR), 0.28)
        # 아르페지오 리드
        for k in range(4 * arp_rate):
            m = chord[arp_shape[k % 4] % 3] + 24
            if arp_shape[k % 4] == 3:
                m = chord[0] + 36
            d = beat / arp_rate
            nl = int(d * 0.8 * SR)
            lead = _tone(_note_hz(m), d * 0.8, lead_kind, bright) * _env(nl, 0.005, d * 0.5)
            _add(loop, lead, t0 + int(k * d * SR), 0.06)
        # 드럼
        for s in range(16):
            at = t0 + int(s * step * SR)
            if kick_p[s] == "1":
                _add(loop, _kick(int(0.3 * SR)), at, 0.55)
            if snare_p[s] == "1":
                _add(loop, _noise_hit(int(0.2 * SR), 22, rng, False), at, 0.12)
            if hat_p[s] == "1":
                _add(loop, _noise_hit(int(0.05 * SR), 90, rng, True), at, 0.035 * (1.3 if s % 4 == 2 else 1))

    loop = loop[: int(bar * 4 * SR)]
    reps = int(np.ceil(seconds * SR / len(loop)))
    out = np.tile(loop, reps)[: int(seconds * SR)]
    fade = min(len(out), int(1.5 * SR))
    out[-fade:] *= np.linspace(1, 0, fade)
    out[: int(0.3 * SR)] *= np.linspace(0, 1, int(0.3 * SR))
    peak = np.max(np.abs(out)) or 1
    return out / peak * 0.8


def write(seconds: float, seed: int, out: Path) -> Path:
    x = compose(seconds, seed)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out


def describe(seed: int) -> str:
    """로그용 한 줄 (어떤 곡이 나왔는지)."""
    rnd = random.Random(seed)
    bpm = rnd.choice([84, 88, 92, 96, 100, 104, 110, 118])
    key = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][rnd.randrange(12)]
    return f"{key} major {bpm}bpm (seed {seed})"
