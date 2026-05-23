"""End-to-end smoke test wiring capture → buffer → analyzer → comparator → report.

This test stitches together every layer the MVP needs to live without going
through the MCP transport or a real microphone:

* A tiny solo :class:`Gabarito` is built with :class:`GabaritoBuilder` (two
  reference notes on A4).
* A :class:`FakeCapture` feeds matching synthetic 440 Hz audio for 30 s.
* :class:`BatchBuffer` accumulates the audio and emits one
  :class:`ClosedBatch` (no need to drive the real PortAudio stack).
* :class:`PitchAnalyzer` produces frame-by-frame detections.
* :class:`Aligner` + :class:`Scorer` compare against the gabarito's solo
  trecho and yield a :class:`PitchMetrics` payload.
* The final :class:`BatchReport` is assembled, validated, and inspected.

The point is to catch breakages that only show up when components interact —
e.g. a mismatch in sample-rate handling, in the units of timestamps, in the
shape of the resampled user contour, or in the schema-v1 payload contract.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import numpy as np
import pytest

from auladcanto.domain.analysis.buffer import BatchBuffer, ClosedBatch
from auladcanto.domain.analysis.capture import CaptureConfig, FakeCapture
from auladcanto.domain.analysis.pitch import PitchAnalyzer
from auladcanto.domain.analysis.respiracao import RespiracaoAnalyzer
from auladcanto.domain.analysis.timing import TimingAnalyzer
from auladcanto.domain.analysis.vibrato import VibratoAnalyzer
from auladcanto.domain.batch import (
    BatchReport,
    VolumeMetrics,
)
from auladcanto.domain.comparator.aligner import Aligner
from auladcanto.domain.comparator.score import Scorer
from auladcanto.domain.gabarito import (
    GabaritoBuilder,
    NotaSeries,
    QualidadeGabarito,
)

_SAMPLE_RATE = 44_100
_HOP_SIZE = 512
_BATCH_DURATION_S = 30
_FRAMES_PER_SECOND = _SAMPLE_RATE / _HOP_SIZE


def _make_tone(frequency_hz: float, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    n = int(_SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float64) / _SAMPLE_RATE
    return (amplitude * np.sin(2.0 * np.pi * frequency_hz * t)).astype(np.float32)


@pytest.mark.integration
async def test_full_pipeline_capture_to_batch_report_with_solo_gabarito() -> None:
    """A synthetic 30 s take of A4 against a 2-note A4 solo gabarito scores high.

    Asserts the wiring from FakeCapture all the way to the schema-v1
    :class:`BatchReport`:

    * The buffer closes exactly one 30 s batch.
    * The gabarito keeps its high-confidence quality envelope.
    * The aligner produces a single :class:`AlinhamentoTrecho` for the solo.
    * The scorer reports near-perfect pitch precision (~440 Hz vs ~440 Hz).
    * The assembled :class:`BatchReport` validates and round-trips through JSON.
    """
    # 1) Build a tiny solo gabarito with two reference notes on A4.
    voz = NotaSeries(
        pitches_hz=[440.0, 440.0],
        tempos_s=[0.0, float(_BATCH_DURATION_S - 1)],
    )
    gabarito = (
        GabaritoBuilder(
            musica="A4 Sustained",
            artista="Smoke Test",
            tom_original="A",
            bpm=120.0,
            qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
        )
        .add_solo(inicio_s=0.0, fim_s=float(_BATCH_DURATION_S), voz=voz)
        .build()
    )
    assert gabarito.qualidade_gabarito.nivel == "alta"

    # 2) Feed FakeCapture with 30 s of matching 440 Hz audio and let the
    # BatchBuffer close exactly one batch.
    audio = _make_tone(440.0, _BATCH_DURATION_S, amplitude=0.5)
    capture = FakeCapture(audio, CaptureConfig(sample_rate=_SAMPLE_RATE, chunk_size=_HOP_SIZE))
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=3600,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()
    await buffer.run(queue)

    batches: list[ClosedBatch] = []
    while not queue.empty():
        batches.append(queue.get_nowait())

    assert len(batches) == 1
    closed = batches[0]
    assert closed.total_samples == _SAMPLE_RATE * _BATCH_DURATION_S
    assert closed.sample_rate == _SAMPLE_RATE

    # 3) Run the pitch analyzer over the closed batch.
    pitch_analyzer = PitchAnalyzer(sample_rate=_SAMPLE_RATE, hop_size=_HOP_SIZE)
    detections = pitch_analyzer.detect_pitches(closed.samples)
    assert len(detections) > 0

    # 4) Align user pitches against the gabarito and score the alignment.
    user_pitches_hz = [d.pitch_hz for d in detections]
    user_timestamps_ms = [d.timestamp_ms for d in detections]

    aligner = Aligner()
    alinhamentos = aligner.alinhar_batch(
        gabarito=gabarito,
        batch_start_s=0.0,
        batch_duration_s=float(_BATCH_DURATION_S),
        user_pitches_hz=user_pitches_hz,
        user_timestamps_ms=user_timestamps_ms,
        voz_escolhida="solo",
    )
    assert len(alinhamentos) == 1
    assert alinhamentos[0].voz_usada == "solo"
    assert alinhamentos[0].ref_freqs.size == alinhamentos[0].user_freqs.size

    scorer = Scorer()
    per_trecho = [scorer.score_trecho(a) for a in alinhamentos]
    aggregate = scorer.aggregate(per_trecho)
    pitch_metrics = scorer.to_pitch_metrics(aggregate, ataque_predominante="direto")
    assert pitch_metrics.notas_corretas_pct >= 90.0
    assert pitch_metrics.precisao_oitava_pct >= 90.0

    # 5) Fill in the remaining sub-objects so we can assemble a v1 report.
    vibrato_metrics = VibratoAnalyzer(frame_rate_hz=_FRAMES_PER_SECOND).analyze(user_pitches_hz)
    respiracao_metrics = RespiracaoAnalyzer(
        sample_rate=_SAMPLE_RATE,
        silence_threshold=0.05,
    ).analyze(closed.samples)
    timing_analyzer = TimingAnalyzer(sample_rate=_SAMPLE_RATE)
    onsets = timing_analyzer.detect_onsets(closed.samples)
    timing_metrics = timing_analyzer.compute_metrics(
        onsets,
        bpm_gabarito=gabarito.bpm,
        batch_duration_s=float(_BATCH_DURATION_S),
    )
    volume_metrics = VolumeMetrics(
        media_normalizada=min(1.0, float(np.mean(np.abs(closed.samples)))),
        quedas_abruptas=0,
        projecao_geral="boa",
    )

    report = BatchReport(
        schema_version=1,
        batch_numero=closed.batch_numero,
        timestamp=datetime.now(UTC),
        musica_id="smoke_test",
        duracao_segundos=_BATCH_DURATION_S,
        posicao_musica="batch único",
        voz_escolhida="solo",
        timing=timing_metrics,
        pitch=pitch_metrics,
        vibrato=vibrato_metrics,
        respiracao=respiracao_metrics,
        volume=volume_metrics,
    )

    # 6) Schema invariants: schema_version is v1, JSON round-trip is lossless,
    # gabarito quality survived end-to-end.
    assert report.schema_version == 1
    assert BatchReport.from_json(report.to_json()) == report
    assert gabarito.qualidade_gabarito.nivel == "alta"
