"""Generate deterministic, dependency-free WAV assets for the Wwise showcase."""

from __future__ import annotations

import argparse
import math
import random
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 48_000


def _envelope(time: float, duration: float, attack: float, release: float) -> float:
    return min(1.0, time / attack, max(0.0, (duration - time) / release))


def _write_wav(path: Path, frames: list[tuple[float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = bytearray()
    for left, right in frames:
        for sample in (left, right):
            pcm.extend(struct.pack("<h", round(max(-1.0, min(1.0, sample)) * 32767)))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(pcm)


def _ui_confirm() -> list[tuple[float, float]]:
    duration = 0.72
    frames = []
    for index in range(round(duration * SAMPLE_RATE)):
        time = index / SAMPLE_RATE
        first = math.sin(2 * math.pi * (660 + 110 * time) * time) * math.exp(-7 * time)
        offset = max(0.0, time - 0.16)
        second = math.sin(2 * math.pi * (990 + 180 * offset) * offset) * math.exp(-8 * offset)
        sparkle = math.sin(2 * math.pi * 1980 * time) * math.exp(-14 * time)
        envelope = _envelope(time, duration, 0.005, 0.08)
        frames.append(
            (
                envelope * (0.42 * first + 0.32 * second + 0.08 * sparkle),
                envelope * (0.38 * first + 0.36 * second + 0.09 * sparkle),
            )
        )
    return frames


def _sci_fi_impact() -> list[tuple[float, float]]:
    duration = 1.8
    rng = random.Random(20260730)
    frames = []
    phase = 0.0
    for index in range(round(duration * SAMPLE_RATE)):
        time = index / SAMPLE_RATE
        frequency = 115 * math.exp(-1.35 * time) + 28
        phase += 2 * math.pi * frequency / SAMPLE_RATE
        low = math.sin(phase) * math.exp(-2.1 * time)
        metal = (
            math.sin(2 * math.pi * 613 * time) + 0.6 * math.sin(2 * math.pi * 947 * time)
        ) * math.exp(-5.2 * time)
        noise = (rng.random() * 2 - 1) * math.exp(-7.5 * time)
        transient = math.sin(2 * math.pi * 42 * time) * math.exp(-18 * time)
        envelope = _envelope(time, duration, 0.002, 0.2)
        left = math.tanh(envelope * (0.9 * low + 0.23 * metal + 0.28 * noise + transient))
        right = math.tanh(envelope * (0.92 * low - 0.18 * metal + 0.24 * noise + transient))
        frames.append((0.78 * left, 0.78 * right))
    return frames


def _footstep(duration: float, seed: int, body_frequency: float) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    frames = []
    for index in range(round(duration * SAMPLE_RATE)):
        time = index / SAMPLE_RATE
        body = math.sin(2 * math.pi * (body_frequency - 22 * time) * time) * math.exp(-15 * time)
        grit = (rng.random() * 2 - 1) * math.exp(-24 * time)
        sole = math.sin(2 * math.pi * 820 * time) * math.exp(-34 * time)
        envelope = _envelope(time, duration, 0.0015, 0.06)
        frames.append(
            (
                0.78 * envelope * (0.72 * body + 0.26 * grit + 0.08 * sole),
                0.78 * envelope * (0.68 * body + 0.22 * grit - 0.07 * sole),
            )
        )
    return frames


def _neon_circuit_bgm() -> list[tuple[float, float]]:
    duration = 12.0
    beat = 0.5
    chords = [
        (146.83, 174.61, 220.00),
        (116.54, 146.83, 174.61),
        (130.81, 164.81, 196.00),
    ]
    rng = random.Random(0xDCC)
    frames = []
    for index in range(round(duration * SAMPLE_RATE)):
        time = index / SAMPLE_RATE
        bar = int(time / (beat * 4))
        chord = chords[bar % len(chords)]
        local_beat = (time % beat) / beat
        pad = (
            sum(
                math.sin(2 * math.pi * frequency * time + voice * 0.37)
                + 0.22 * math.sin(2 * math.pi * frequency * 2 * time)
                for voice, frequency in enumerate(chord)
            )
            / 3.66
        )
        bass = math.sin(2 * math.pi * chord[0] / 2 * time)
        step = int(time / (beat / 2))
        arp_frequency = chord[step % len(chord)] * (2 if step % 4 else 1)
        arp_phase = time % (beat / 2)
        arp = math.sin(2 * math.pi * arp_frequency * time) * math.exp(-7 * arp_phase)
        kick = math.sin(2 * math.pi * (58 - 22 * local_beat) * time) * math.exp(-10 * local_beat)
        snare_phase = (time - beat) % (beat * 2)
        snare = (rng.random() * 2 - 1) * math.exp(-18 * snare_phase) if snare_phase < 0.12 else 0
        master = _envelope(time, duration, 0.35, 0.65)
        left = math.tanh(0.34 * pad + 0.28 * bass + 0.20 * arp + 0.16 * kick + 0.07 * snare)
        right = math.tanh(0.36 * pad + 0.27 * bass - 0.17 * arp + 0.16 * kick + 0.07 * snare)
        frames.append((master * left, master * right))
    return frames


def generate(output_dir: Path) -> list[Path]:
    assets = {
        "ui-confirm.wav": _ui_confirm(),
        "sci-fi-impact.wav": _sci_fi_impact(),
        "neon-circuit-bgm.wav": _neon_circuit_bgm(),
        "footstep-01.wav": _footstep(0.34, 101, 92),
        "footstep-02.wav": _footstep(0.37, 202, 84),
        "footstep-03.wav": _footstep(0.32, 303, 101),
    }
    paths = []
    for name, frames in assets.items():
        path = output_dir / name
        _write_wav(path, frames)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", type=Path, default=Path("showcase/audio"))
    args = parser.parse_args()
    for path in generate(args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
