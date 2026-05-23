"""Golden tests for fully assembled :class:`BatchReport` payloads.

These scenarios pipe deterministic synthetic audio (and matching reference
contours) through the *entire* phase 3B analysis chain — pitch + vibrato +
respiracao + timing + ataque + transposicao — then assemble the resulting
sub-objects into a :class:`BatchReport`. They are the regression guard rails
that catch contract drift between any analyzer and the schema-v1 payload the
MCP layer consumes.

Each scenario exercises one pedagogical archetype:

* ``test_scenario_pitch_perfect_steady_voice`` — a clean 440 Hz sustained
  tone against a 440 Hz reference. Pitch metrics should be near perfect, no
  vibrato should fire (the tone is flat), and the breath-alert flag should
  trigger because there are no silent gaps in 30 s of audio.
* ``test_scenario_vibrato_tenso_8hz`` — 440 Hz modulated by an 8 Hz pitch
  oscillation. Vibrato should be detected and labelled ``rapido_tenso``.
* ``test_scenario_frase_sem_respiro`` — a continuous loud tone with zero
  detectable breaths over the full 30 s window; the respiracao analyzer must
  raise the ``alerta_sem_respiro`` flag.

The helpers below construct everything from numpy arrays so the tests do not
depend on the ``[audio]`` extra (aubio / mir_eval). They target the pure-numpy
fallbacks of every analyzer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from auladcanto.domain.analysis.ataque import AtaqueClassifier
from auladcanto.domain.analysis.pitch import PitchAnalyzer
from auladcanto.domain.analysis.respiracao import RespiracaoAnalyzer
from auladcanto.domain.analysis.timing import TimingAnalyzer
from auladcanto.domain.analysis.transposicao import TransposicaoDetector
from auladcanto.domain.analysis.vibrato import VibratoAnalyzer
from auladcanto.domain.batch import (
    BatchReport,
    VolumeMetrics,
)

_SAMPLE_RATE = 44_100
_HOP_SIZE = 512
_BATCH_DURATION_S = 30
_FRAMES_PER_SECOND = _SAMPLE_RATE / _HOP_SIZE  # ~86 Hz pitch-frame rate


def _sine_tone(frequency_hz: float, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    """Return a mono float32 sine wave of ``seconds`` duration."""
    n = int(_SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float64) / _SAMPLE_RATE
    return (amplitude * np.sin(2.0 * np.pi * frequency_hz * t)).astype(np.float32)


def _vibrato_modulated_tone(
    base_hz: float,
    vibrato_hz: float,
    depth_cents: float,
    seconds: float,
    amplitude: float = 0.5,
) -> np.ndarray:
    """Return a mono float32 tone whose pitch oscillates around ``base_hz``.

    Implemented via phase integration so the resulting waveform is a true
    frequency-modulated sinusoid; the instantaneous frequency follows
    ``base_hz * 2^(depth_cents * sin(2π * vibrato_hz * t) / 1200)``.
    """
    n = int(_SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float64) / _SAMPLE_RATE
    instantaneous_hz = base_hz * np.power(
        2.0, depth_cents * np.sin(2.0 * np.pi * vibrato_hz * t) / 1200.0
    )
    phase = 2.0 * np.pi * np.cumsum(instantaneous_hz) / _SAMPLE_RATE
    return (amplitude * np.sin(phase)).astype(np.float32)


def _assemble_batch_report(
    samples: np.ndarray,
    ref_freqs: list[float],
    *,
    bpm_gabarito: float = 120.0,
    voz_escolhida: str = "solo",
    posicao_musica: str = "test",
    batch_number: int = 1,
) -> BatchReport:
    """Run the full analysis chain and return the assembled v1 batch report.

    Wires :class:`PitchAnalyzer`, :class:`VibratoAnalyzer`,
    :class:`RespiracaoAnalyzer`, :class:`TimingAnalyzer`,
    :class:`AtaqueClassifier`, and :class:`TransposicaoDetector` in the same
    order the production batch pipeline does, then folds their outputs into a
    :class:`BatchReport`. The volume metrics are computed from the raw sample
    array since there is no dedicated VolumeAnalyzer in the analyzer chain.
    """
    pitch_analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)
    vibrato_analyzer = VibratoAnalyzer(frame_rate_hz=_FRAMES_PER_SECOND)
    resp_analyzer = RespiracaoAnalyzer(sample_rate=_SAMPLE_RATE, silence_threshold=0.05)
    timing_analyzer = TimingAnalyzer(sample_rate=_SAMPLE_RATE)
    ataque_classifier = AtaqueClassifier()
    transp_detector = TransposicaoDetector()

    detections = pitch_analyzer.detect_pitches(samples)
    pitch_series = [d.pitch_hz for d in detections]
    voiced_pitches = [hz for hz in pitch_series if hz > 0.0]

    # Pitch metrics need a reference list aligned 1:1 with detections so the
    # internal _align_and_compare picks up consistent targets.
    reference_hz = [ref_freqs[0]] * len(detections) if ref_freqs else []
    pitch_metrics = pitch_analyzer.compute_metrics(
        detections,
        reference_hz=reference_hz,
        reference_notes=["A4"] * len(reference_hz),
        ataque_classifier=lambda _dets: (
            ataque_classifier(voiced_pitches[:15], reference_hz[0])
            if reference_hz
            else "indeterminado"
        ),
    )

    vibrato_metrics = vibrato_analyzer.analyze(pitch_series)
    respiracao_metrics = resp_analyzer.analyze(samples)

    onsets = timing_analyzer.detect_onsets(samples)
    timing_metrics = timing_analyzer.compute_metrics(
        onsets,
        bpm_gabarito=bpm_gabarito,
        batch_duration_s=float(_BATCH_DURATION_S),
    )

    transp = transp_detector.detect(voiced_pitches, reference_hz if reference_hz else [])

    volume_metrics = VolumeMetrics(
        media_normalizada=min(1.0, float(np.mean(np.abs(samples)))),
        quedas_abruptas=0,
        projecao_geral="boa",
    )

    return BatchReport(
        schema_version=1,
        batch_numero=batch_number,
        timestamp=datetime.now(UTC),
        musica_id="test_song",
        duracao_segundos=_BATCH_DURATION_S,
        posicao_musica=posicao_musica,
        voz_escolhida=voz_escolhida,  # type: ignore[arg-type]
        timing=timing_metrics,
        pitch=pitch_metrics,
        vibrato=vibrato_metrics,
        respiracao=respiracao_metrics,
        volume=volume_metrics,
        transposicao_detectada=transp,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.golden
def test_scenario_pitch_perfect_steady_voice() -> None:
    """A flat 440 Hz tone against a 440 Hz reference yields near-perfect pitch."""
    samples = _sine_tone(440.0, _BATCH_DURATION_S, amplitude=0.5)
    ref_freqs = [440.0]

    report = _assemble_batch_report(samples, ref_freqs)

    # Pitch is essentially perfect against the matching reference.
    assert report.pitch.notas_corretas_pct >= 90.0
    assert report.pitch.desvio_padrao_cents <= 10.0
    # A sustained, perfectly flat tone has no vibrato.
    assert report.vibrato.detectado is False
    # 30s of continuous voiced audio without any silent gap must trigger the
    # "no breath in long stretch" pedagogical alert.
    assert report.respiracao.alerta_sem_respiro is True
    assert report.respiracao.respiros_detectados == 0
    # User and reference are identical → no mental transposition.
    assert report.transposicao_detectada is not None
    assert report.transposicao_detectada.detectada is False
    # Sanity: schema is v1 and the report round-trips through JSON.
    assert report.schema_version == 1
    roundtripped = BatchReport.from_json(report.to_json())
    assert roundtripped == report


@pytest.mark.golden
def test_scenario_vibrato_tenso_8hz() -> None:
    """An 8 Hz vibrato around 440 Hz is detected and labelled ``rapido_tenso``."""
    samples = _vibrato_modulated_tone(
        base_hz=440.0,
        vibrato_hz=8.0,
        depth_cents=60.0,
        seconds=_BATCH_DURATION_S,
        amplitude=0.5,
    )
    ref_freqs = [440.0]

    report = _assemble_batch_report(samples, ref_freqs)

    assert report.vibrato.detectado is True
    assert report.vibrato.frequencia_hz is not None
    # The detector picks the dominant FFT bin in the [2, 12] Hz oscillation
    # band; an 8 Hz modulation should land within ~0.5 Hz of that bin.
    assert abs(report.vibrato.frequencia_hz - 8.0) <= 0.5
    assert report.vibrato.naturalidade == "rapido_tenso"


@pytest.mark.golden
def test_scenario_frase_sem_respiro() -> None:
    """A continuous loud tone has zero breaths and triggers the alert."""
    samples = _sine_tone(440.0, _BATCH_DURATION_S, amplitude=0.6)
    ref_freqs = [440.0]

    report = _assemble_batch_report(samples, ref_freqs)

    assert report.respiracao.respiros_detectados == 0
    assert report.respiracao.respiros == []
    assert report.respiracao.alerta_sem_respiro is True
