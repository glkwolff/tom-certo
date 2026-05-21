"""Unit tests for the phase 3C comparator (aligner + scorer).

The tests build their gabaritos with :class:`GabaritoBuilder` and synthesise
user pitch contours directly so they do not depend on the ``[audio]`` extra.
The aligner and scorer both fall back to numpy implementations when
``mir_eval`` is unavailable — these tests target that fallback path.
"""

from __future__ import annotations

import numpy as np

from auladcanto.domain.comparator.aligner import Aligner, AlinhamentoTrecho
from auladcanto.domain.comparator.score import ComparisonResult, Scorer
from auladcanto.domain.gabarito import (
    GabaritoBuilder,
    NotaSeries,
    QualidadeGabarito,
    TrechoDuo,
    TrechoSolo,
    TrechoUnissono,
)


def _series_constant(hz: float, n: int, start_s: float, step_s: float) -> NotaSeries:
    return NotaSeries(
        pitches_hz=[hz] * n,
        tempos_s=[start_s + i * step_s for i in range(n)],
    )


def _gabarito_mixed() -> object:
    return (
        GabaritoBuilder(
            musica="Mix",
            artista="Test",
            tom_original="C",
            bpm=120.0,
            qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
        )
        .add_solo(inicio_s=0.0, fim_s=2.0, voz=_series_constant(440.0, 21, 0.0, 0.1))
        .add_duo(
            inicio_s=2.0,
            fim_s=4.0,
            voz_aguda=_series_constant(660.0, 21, 2.0, 0.1),
            voz_grave=_series_constant(330.0, 21, 2.0, 0.1),
            intervalo_semitons=7,
        )
        .add_unissono(
            inicio_s=4.0,
            fim_s=6.0,
            voz=_series_constant(523.25, 21, 4.0, 0.1),
        )
        .build()
    )


def _user_inputs_from_ref(
    ref_freqs: list[float], ref_times: list[float], batch_start_s: float
) -> tuple[list[float], list[int]]:
    user_pitches = list(ref_freqs)
    user_timestamps = [round((t - batch_start_s) * 1000.0) for t in ref_times]
    return user_pitches, user_timestamps


# ---------------------------------------------------------------------------
# Aligner
# ---------------------------------------------------------------------------


def test_selecionar_trecho_para_timestamp_returns_correct_trecho() -> None:
    aligner = Aligner()
    gabarito = _gabarito_mixed()

    solo = aligner.selecionar_trecho_para_timestamp(gabarito, 0.5)
    duo = aligner.selecionar_trecho_para_timestamp(gabarito, 2.5)
    unissono = aligner.selecionar_trecho_para_timestamp(gabarito, 4.5)
    out_of_bounds = aligner.selecionar_trecho_para_timestamp(gabarito, 10.0)

    assert isinstance(solo, TrechoSolo)
    assert isinstance(duo, TrechoDuo)
    assert isinstance(unissono, TrechoUnissono)
    assert out_of_bounds is None


def test_alinhar_batch_for_solo_trecho_produces_equal_length_arrays() -> None:
    aligner = Aligner()
    gabarito = _gabarito_mixed()
    solo_trecho = gabarito.trechos[0]
    user_pitches, user_timestamps = _user_inputs_from_ref(
        solo_trecho.voz.pitches_hz, solo_trecho.voz.tempos_s, batch_start_s=0.0
    )

    alinhamentos = aligner.alinhar_batch(
        gabarito=gabarito,
        batch_start_s=0.0,
        batch_duration_s=2.0,
        user_pitches_hz=user_pitches,
        user_timestamps_ms=user_timestamps,
        voz_escolhida="n/a",
    )

    assert len(alinhamentos) == 1
    alinhamento = alinhamentos[0]
    assert alinhamento.voz_usada == "solo"
    assert alinhamento.ref_freqs.size == alinhamento.user_freqs.size
    assert alinhamento.ref_times.size == alinhamento.user_times.size
    assert alinhamento.ref_freqs.size > 0
    assert np.allclose(alinhamento.ref_times, alinhamento.user_times)


def test_alinhar_batch_duo_with_voz_aguda_uses_voz_aguda() -> None:
    aligner = Aligner()
    gabarito = _gabarito_mixed()
    duo_trecho = gabarito.trechos[1]
    assert isinstance(duo_trecho, TrechoDuo)
    user_pitches, user_timestamps = _user_inputs_from_ref(
        duo_trecho.voz_aguda.pitches_hz, duo_trecho.voz_aguda.tempos_s, batch_start_s=2.0
    )

    alinhamentos = aligner.alinhar_batch(
        gabarito=gabarito,
        batch_start_s=2.0,
        batch_duration_s=2.0,
        user_pitches_hz=user_pitches,
        user_timestamps_ms=user_timestamps,
        voz_escolhida="aguda",
    )

    assert len(alinhamentos) == 1
    alinhamento = alinhamentos[0]
    assert alinhamento.voz_usada == "aguda"
    assert np.allclose(alinhamento.ref_freqs, np.full(alinhamento.ref_freqs.shape, 660.0))


def test_alinhar_batch_duo_with_voz_grave_uses_voz_grave() -> None:
    aligner = Aligner()
    gabarito = _gabarito_mixed()
    duo_trecho = gabarito.trechos[1]
    assert isinstance(duo_trecho, TrechoDuo)
    user_pitches, user_timestamps = _user_inputs_from_ref(
        duo_trecho.voz_grave.pitches_hz, duo_trecho.voz_grave.tempos_s, batch_start_s=2.0
    )

    alinhamentos = aligner.alinhar_batch(
        gabarito=gabarito,
        batch_start_s=2.0,
        batch_duration_s=2.0,
        user_pitches_hz=user_pitches,
        user_timestamps_ms=user_timestamps,
        voz_escolhida="grave",
    )

    assert len(alinhamentos) == 1
    alinhamento = alinhamentos[0]
    assert alinhamento.voz_usada == "grave"
    assert np.allclose(alinhamento.ref_freqs, np.full(alinhamento.ref_freqs.shape, 330.0))


def test_alinhar_batch_dtw_handles_slower_user_take() -> None:
    aligner = Aligner(use_dtw=True)
    gabarito = (
        GabaritoBuilder(
            musica="DTW",
            artista="Test",
            tom_original="C",
            bpm=120.0,
            qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
        )
        .add_solo(
            inicio_s=0.0,
            fim_s=1.2,
            voz=NotaSeries(
                pitches_hz=[440.0, 466.16, 493.88, 523.25, 554.37, 587.33],
                tempos_s=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            ),
        )
        .build()
    )
    slowdown = 1.2
    user_freqs = [440.0, 466.16, 493.88, 523.25, 554.37, 587.33]
    user_times = [round(slowdown * t * 1000.0) for t in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]]

    alinhamentos = aligner.alinhar_batch(
        gabarito=gabarito,
        batch_start_s=0.0,
        batch_duration_s=2.0,
        user_pitches_hz=user_freqs,
        user_timestamps_ms=user_times,
        voz_escolhida="n/a",
    )

    assert len(alinhamentos) == 1
    alinhamento = alinhamentos[0]
    assert alinhamento.ref_freqs.size == alinhamento.user_freqs.size
    voiced = alinhamento.user_freqs > 0.0
    assert bool(np.any(voiced))


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


def _alinhamento_constant(
    ref_hz: float,
    user_hz: float,
    n_frames: int = 21,
    start_s: float = 0.0,
    step_s: float = 0.1,
) -> AlinhamentoTrecho:
    trecho = TrechoSolo(
        inicio_s=start_s,
        fim_s=start_s + (n_frames - 1) * step_s,
        voz=NotaSeries(
            pitches_hz=[ref_hz] * n_frames,
            tempos_s=[start_s + i * step_s for i in range(n_frames)],
        ),
    )
    ref_times = np.asarray([start_s + i * step_s for i in range(n_frames)], dtype=np.float64)
    return AlinhamentoTrecho(
        trecho=trecho,
        ref_times=ref_times,
        ref_freqs=np.full(n_frames, ref_hz, dtype=np.float64),
        user_times=ref_times.copy(),
        user_freqs=np.full(n_frames, user_hz, dtype=np.float64),
        voz_usada="solo",
    )


def test_scorer_perfect_match_returns_full_precision() -> None:
    scorer = Scorer()
    alinhamento = _alinhamento_constant(ref_hz=440.0, user_hz=440.0)

    result = scorer.score_trecho(alinhamento)

    assert result.precisao_pitch_pct >= 99.0
    assert result.precisao_oitava_pct >= 99.0
    assert result.notas_cantadas_pct == 100.0
    assert result.cantou_no_silencio_pct == 0.0
    assert result.desvio_padrao_cents < 1e-6
    assert result.momentos_criticos == []


def test_scorer_octave_up_keeps_chroma_but_loses_raw_pitch() -> None:
    scorer = Scorer()
    alinhamento = _alinhamento_constant(ref_hz=440.0, user_hz=880.0)

    result = scorer.score_trecho(alinhamento)

    assert result.precisao_pitch_pct < 5.0
    assert result.precisao_oitava_pct >= 99.0


def test_scorer_user_silent_against_voiced_ref_yields_zero_notas_cantadas() -> None:
    scorer = Scorer()
    alinhamento = _alinhamento_constant(ref_hz=440.0, user_hz=0.0)

    result = scorer.score_trecho(alinhamento)

    assert result.notas_cantadas_pct == 0.0
    assert result.precisao_pitch_pct == 0.0


def test_scorer_user_voiced_against_silent_ref_yields_full_false_positives() -> None:
    scorer = Scorer()
    alinhamento = _alinhamento_constant(ref_hz=0.0, user_hz=440.0)

    result = scorer.score_trecho(alinhamento)

    assert result.cantou_no_silencio_pct >= 99.0
    assert result.notas_cantadas_pct == 0.0


def test_scorer_top_momentos_returns_top_five_worst() -> None:
    scorer = Scorer()
    n_frames = 10
    ref_freqs = np.full(n_frames, 440.0, dtype=np.float64)
    deviations_cents = np.asarray(
        [0.0, 80.0, 200.0, 350.0, 500.0, 700.0, 60.0, 5.0, 1000.0, 1200.0],
        dtype=np.float64,
    )
    user_freqs = ref_freqs * (2.0 ** (deviations_cents / 1200.0))
    ref_times = np.asarray([0.1 * i for i in range(n_frames)], dtype=np.float64)
    trecho = TrechoSolo(
        inicio_s=0.0,
        fim_s=1.0,
        voz=NotaSeries(pitches_hz=ref_freqs.tolist(), tempos_s=ref_times.tolist()),
    )
    alinhamento = AlinhamentoTrecho(
        trecho=trecho,
        ref_times=ref_times,
        ref_freqs=ref_freqs,
        user_times=ref_times.copy(),
        user_freqs=user_freqs,
        voz_usada="solo",
    )

    result = scorer.score_trecho(alinhamento)

    assert len(result.momentos_criticos) == 5
    assert all(abs(m.erro_cents) > 50 for m in result.momentos_criticos)
    timestamps = [m.timestamp_ms for m in result.momentos_criticos]
    assert timestamps == sorted(timestamps)


def test_scorer_aggregate_is_duration_weighted() -> None:
    scorer = Scorer()
    short_result = ComparisonResult(
        precisao_pitch_pct=100.0,
        precisao_oitava_pct=100.0,
        notas_cantadas_pct=100.0,
        cantou_no_silencio_pct=0.0,
        score_geral_pct=100.0,
        desvio_padrao_cents=0.0,
        momentos_criticos=[],
        duracao_s=1.0,
    )
    long_result = ComparisonResult(
        precisao_pitch_pct=0.0,
        precisao_oitava_pct=0.0,
        notas_cantadas_pct=0.0,
        cantou_no_silencio_pct=100.0,
        score_geral_pct=0.0,
        desvio_padrao_cents=0.0,
        momentos_criticos=[],
        duracao_s=3.0,
    )
    medium_result = ComparisonResult(
        precisao_pitch_pct=50.0,
        precisao_oitava_pct=50.0,
        notas_cantadas_pct=50.0,
        cantou_no_silencio_pct=50.0,
        score_geral_pct=50.0,
        desvio_padrao_cents=10.0,
        momentos_criticos=[],
        duracao_s=2.0,
    )

    aggregate = scorer.aggregate([short_result, long_result, medium_result])

    expected_precisao = (100.0 * 1.0 + 0.0 * 3.0 + 50.0 * 2.0) / 6.0
    assert aggregate.precisao_pitch_pct == round(expected_precisao, 4)
    assert aggregate.duracao_s == 6.0


def test_scorer_to_pitch_metrics_converts_to_schema_v1() -> None:
    scorer = Scorer()
    aggregate = ComparisonResult(
        precisao_pitch_pct=85.5,
        precisao_oitava_pct=92.0,
        notas_cantadas_pct=88.0,
        cantou_no_silencio_pct=3.0,
        score_geral_pct=89.5,
        desvio_padrao_cents=22.3,
        momentos_criticos=[],
        duracao_s=30.0,
    )

    metrics = scorer.to_pitch_metrics(aggregate, ataque_predominante="direto")

    assert metrics.notas_corretas_pct == 85.5
    assert metrics.precisao_oitava_pct == 92.0
    assert metrics.desvio_padrao_cents == 22.3
    assert metrics.ataque_predominante == "direto"
    assert metrics.momentos_criticos == []
