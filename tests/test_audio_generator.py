import wave

from tools.generate_showcase_audio import SAMPLE_RATE, generate


def test_generated_showcase_wavs_are_stereo_48k_and_nonempty(tmp_path):
    paths = generate(tmp_path)
    assert {path.name for path in paths} == {
        "neon-circuit-bgm.wav",
        "sci-fi-impact.wav",
        "ui-confirm.wav",
    }
    expected_durations = {
        "ui-confirm.wav": 0.72,
        "sci-fi-impact.wav": 1.8,
        "neon-circuit-bgm.wav": 12.0,
    }
    for path in paths:
        with wave.open(str(path), "rb") as stream:
            assert stream.getnchannels() == 2
            assert stream.getsampwidth() == 2
            assert stream.getframerate() == SAMPLE_RATE
            assert stream.getnframes() == round(expected_durations[path.name] * SAMPLE_RATE)
            assert any(stream.readframes(256))
