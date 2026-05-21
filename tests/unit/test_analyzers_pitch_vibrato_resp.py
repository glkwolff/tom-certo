"""Unit tests for phase 3B-a analyzers: pitch, vibrato, respiracao.

The tests synthesise their own audio so they do not depend on the
``[audio]`` extra. The pitch analyzer falls back to a numpy autocorrelation
detector when ``aubio`` is unavailable; these tests target that path.
"""

from __future__ import annotations

import numpy as np

from auladcanto.domain.analysis.pitch import PitchAnalyzer, PitchDetection
from auladcanto.domain.analysis.respiracao import RespiracaoAnalyzer
from auladcanto.domain.analysis.vibrato import VibratoAnalyzer

_SAMPLE_RATE = 44_100
_HOP_SIZE = 512
_FRAME_RATE_HZ = 44.0


def _tone(frequency_hz: float, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    n = int(_SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float64) / _SAMPLE_RATE
    return (amplitude * np.sin(2.0 * np.pi * frequency_hz * t)).astype(np.float32)


def _oscillating_pitch_series(
    base_hz: float, vibrato_hz: float, depth_cents: float, num_frames: int
) -> list[float]:
    t = np.arange(num_frames, dtype=np.float64) / _FRAME_RATE_HZ
    cents = depth_cents * np.sin(2.0 * np.pi * vibrato_hz * t)
    return (base_hz * (2.0 ** (cents / 1200.0))).tolist()


def test_pitch_analyzer_detects_440hz_sine_wave() -> None:
    analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)

    detections = analyzer.detect_pitches(_tone(440.0, 0.5))

    voiced = [d for d in detections if d.pitch_hz > 0.0]
    assert len(voiced) >= 20
    median_pitch = float(np.median([d.pitch_hz for d in voiced]))
    assert abs(median_pitch - 440.0) < 2.0
    assert all(d.confianca >= 0.5 for d in voiced)


def test_pitch_metrics_perfect_match_reference() -> None:
    analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)
    detections = analyzer.detect_pitches(_tone(440.0, 0.5))

    metrics = analyzer.compute_metrics(
        detections,
        reference_hz=[440.0] * len(detections),
        reference_notes=["A4"] * len(detections),
    )

    assert metrics.notas_corretas_pct >= 99.0
    assert metrics.precisao_oitava_pct >= 99.0
    assert metrics.desvio_padrao_cents < 10.0
    assert metrics.ataque_predominante == "indeterminado"
    assert metrics.momentos_criticos == []


def test_pitch_metrics_octave_off_keeps_chroma_match() -> None:
    analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)
    detections = analyzer.detect_pitches(_tone(440.0, 0.5))

    metrics = analyzer.compute_metrics(
        detections,
        reference_hz=[880.0] * len(detections),
        reference_notes=["A5"] * len(detections),
    )

    assert metrics.notas_corretas_pct < 5.0
    assert metrics.precisao_oitava_pct >= 99.0


def test_pitch_metrics_empty_samples_returns_zeroed_metrics() -> None:
    analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)

    detections = analyzer.detect_pitches(np.empty(0, dtype=np.float32))
    metrics = analyzer.compute_metrics(detections, reference_hz=[440.0])

    assert detections == []
    assert metrics.notas_corretas_pct == 0.0
    assert metrics.precisao_oitava_pct == 0.0
    assert metrics.desvio_padrao_cents == 0.0
    assert metrics.momentos_criticos == []


def test_pitch_metrics_top_momentos_returned_for_strong_deviation() -> None:
    analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)
    detections = analyzer.detect_pitches(_tone(440.0, 0.5))
    reference_hz = [220.0] * len(detections)
    reference_notes = [f"NOTE_{i}" for i in range(len(detections))]

    metrics = analyzer.compute_metrics(
        detections,
        reference_hz=reference_hz,
        reference_notes=reference_notes,
    )

    assert metrics.notas_corretas_pct < 5.0
    assert 1 <= len(metrics.momentos_criticos) <= 5
    for moment in metrics.momentos_criticos:
        assert abs(moment.erro_cents) > 50
        assert moment.nota_alvo.startswith("NOTE_")


def test_pitch_metrics_uses_provided_ataque_classifier() -> None:
    analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)
    detections = analyzer.detect_pitches(_tone(440.0, 0.3))

    def _classifier(_dets: list[PitchDetection]) -> str:
        return "direto"

    metrics = analyzer.compute_metrics(
        detections,
        reference_hz=[440.0] * len(detections),
        ataque_classifier=_classifier,  # type: ignore[arg-type]
    )

    assert metrics.ataque_predominante == "direto"


def test_vibrato_analyzer_flat_series_returns_not_detected() -> None:
    analyzer = VibratoAnalyzer(frame_rate_hz=_FRAME_RATE_HZ)

    metrics = analyzer.analyze([440.0] * 200)

    assert metrics.detectado is False
    assert metrics.frequencia_hz is None
    assert metrics.naturalidade is None


def test_vibrato_analyzer_6hz_oscillation_is_natural() -> None:
    analyzer = VibratoAnalyzer(frame_rate_hz=_FRAME_RATE_HZ)
    series = _oscillating_pitch_series(440.0, 6.0, 50.0, num_frames=220)

    metrics = analyzer.analyze(series)

    assert metrics.detectado is True
    assert metrics.frequencia_hz is not None
    assert abs(metrics.frequencia_hz - 6.0) < 0.5
    assert metrics.naturalidade == "natural"


def test_vibrato_analyzer_3hz_oscillation_is_lento_tremulo() -> None:
    analyzer = VibratoAnalyzer(frame_rate_hz=_FRAME_RATE_HZ)
    series = _oscillating_pitch_series(440.0, 3.0, 60.0, num_frames=300)

    metrics = analyzer.analyze(series)

    assert metrics.detectado is True
    assert metrics.naturalidade == "lento_tremulo"
    assert metrics.frequencia_hz is not None
    assert metrics.frequencia_hz < 5.0


def test_vibrato_analyzer_9hz_oscillation_is_rapido_tenso() -> None:
    analyzer = VibratoAnalyzer(frame_rate_hz=_FRAME_RATE_HZ)
    series = _oscillating_pitch_series(440.0, 9.0, 50.0, num_frames=220)

    metrics = analyzer.analyze(series)

    assert metrics.detectado is True
    assert metrics.naturalidade == "rapido_tenso"
    assert metrics.frequencia_hz is not None
    assert metrics.frequencia_hz > 7.0


def test_vibrato_analyzer_short_series_returns_not_detected() -> None:
    analyzer = VibratoAnalyzer(frame_rate_hz=_FRAME_RATE_HZ)

    metrics = analyzer.analyze([440.0, 441.0, 439.0])

    assert metrics.detectado is False
    assert metrics.frequencia_hz is None
    assert metrics.naturalidade is None


def test_vibrato_analyzer_rejects_invalid_frame_rate() -> None:
    import pytest

    with pytest.raises(ValueError, match="frame_rate_hz"):
        VibratoAnalyzer(frame_rate_hz=0.0)


def test_respiracao_analyzer_continuous_signal_has_no_respiros() -> None:
    analyzer = RespiracaoAnalyzer(sample_rate=_SAMPLE_RATE, silence_threshold=0.05)

    metrics = analyzer.analyze(_tone(440.0, 2.0, amplitude=0.3))

    assert metrics.respiros_detectados == 0
    assert metrics.respiros == []
    assert metrics.alerta_sem_respiro is False


def test_respiracao_analyzer_detects_single_normal_breath() -> None:
    analyzer = RespiracaoAnalyzer(sample_rate=_SAMPLE_RATE, silence_threshold=0.05)
    samples = _tone(440.0, 2.0, amplitude=0.3).copy()
    samples[int(_SAMPLE_RATE * 0.5) : int(_SAMPLE_RATE * 0.65)] = 0.0

    metrics = analyzer.analyze(samples)

    assert metrics.respiros_detectados == 1
    only = metrics.respiros[0]
    assert only.tipo == "normal"
    assert 100 <= only.duracao_ms < 200
    assert 400 <= only.timestamp_ms <= 540


def test_respiracao_analyzer_long_voiced_stretch_triggers_alert() -> None:
    analyzer = RespiracaoAnalyzer(sample_rate=_SAMPLE_RATE, silence_threshold=0.05)

    metrics = analyzer.analyze(_tone(440.0, 9.0, amplitude=0.3))

    assert metrics.respiros_detectados == 0
    assert metrics.alerta_sem_respiro is True


def test_respiracao_analyzer_classifies_three_breath_types() -> None:
    analyzer = RespiracaoAnalyzer(sample_rate=_SAMPLE_RATE, silence_threshold=0.05)
    samples = _tone(440.0, 3.0, amplitude=0.3).copy()
    samples[int(_SAMPLE_RATE * 0.4) : int(_SAMPLE_RATE * 0.45)] = 0.0
    samples[int(_SAMPLE_RATE * 1.0) : int(_SAMPLE_RATE * 1.15)] = 0.0
    samples[int(_SAMPLE_RATE * 2.0) : int(_SAMPLE_RATE * 2.30)] = 0.0

    metrics = analyzer.analyze(samples)

    tipos = [r.tipo for r in metrics.respiros]
    assert tipos == ["rapido_insuficiente", "normal", "preparatorio_longo"]
    assert metrics.respiros_detectados == 3
    assert metrics.alerta_sem_respiro is False


def test_respiracao_analyzer_skips_too_long_silences() -> None:
    analyzer = RespiracaoAnalyzer(sample_rate=_SAMPLE_RATE, silence_threshold=0.05)
    samples = _tone(440.0, 3.0, amplitude=0.3).copy()
    samples[int(_SAMPLE_RATE * 1.0) : int(_SAMPLE_RATE * 1.80)] = 0.0

    metrics = analyzer.analyze(samples)

    assert metrics.respiros_detectados == 0


def test_respiracao_analyzer_rejects_invalid_arguments() -> None:
    import pytest

    with pytest.raises(ValueError, match="sample_rate"):
        RespiracaoAnalyzer(sample_rate=0)
    with pytest.raises(ValueError, match="silence_threshold"):
        RespiracaoAnalyzer(silence_threshold=-0.1)
