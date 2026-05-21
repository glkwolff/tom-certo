"""Unit tests for the vocal polyphony detector and the altitude separator."""

from __future__ import annotations

import pytest

from auladcanto.domain.gabarito import NotaSeries
from auladcanto.domain.preparation.polifonia import (
    DeteccaoPitch,
    classificar_trechos,
    detectar_polifonia_temporal,
)
from auladcanto.domain.preparation.separacao import separar_por_altura

# ---------------------------------------------------------------------------
# detectar_polifonia_temporal
# ---------------------------------------------------------------------------


def _mono_stream(n: int, spacing_s: float = 0.2, pitch_hz: float = 440.0) -> list[DeteccaoPitch]:
    return [
        DeteccaoPitch(timestamp_s=i * spacing_s, pitch_hz=pitch_hz, confianca=0.9) for i in range(n)
    ]


def test_detectar_polifonia_marks_mono_stream_as_non_polyphonic() -> None:
    """A stream with one detection per moment has no polyphonic windows."""
    deteccoes = _mono_stream(n=10, spacing_s=0.2)
    janelas = detectar_polifonia_temporal(deteccoes, janela_s=0.5, overlap_threshold_s=0.05)

    assert len(janelas) > 0
    assert all(not j.is_polifonica for j in janelas)


def test_detectar_polifonia_marks_simultaneous_pitches_as_polyphonic() -> None:
    """Two detections within the overlap threshold produce a polyphonic window."""
    deteccoes = [
        DeteccaoPitch(timestamp_s=0.10, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.12, pitch_hz=329.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.30, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.32, pitch_hz=329.63, confianca=0.9),
    ]
    janelas = detectar_polifonia_temporal(deteccoes, janela_s=0.5, overlap_threshold_s=0.05)

    assert len(janelas) == 1
    janela = janelas[0]
    assert janela.is_polifonica is True
    assert any(len(grupo) >= 2 for grupo in janela.pitches_simultaneos)


def test_detectar_polifonia_filters_low_confidence_detections() -> None:
    """Detections below ``min_confianca`` are dropped before grouping."""
    deteccoes = [
        DeteccaoPitch(timestamp_s=0.10, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.11, pitch_hz=329.63, confianca=0.3),
    ]
    janelas = detectar_polifonia_temporal(
        deteccoes, janela_s=0.5, overlap_threshold_s=0.05, min_confianca=0.6
    )

    assert len(janelas) == 1
    assert janelas[0].is_polifonica is False


def test_detectar_polifonia_returns_empty_when_all_below_threshold() -> None:
    """No detection above threshold yields no windows at all."""
    deteccoes = [
        DeteccaoPitch(timestamp_s=0.10, pitch_hz=261.63, confianca=0.2),
        DeteccaoPitch(timestamp_s=0.20, pitch_hz=329.63, confianca=0.1),
    ]
    janelas = detectar_polifonia_temporal(deteccoes, min_confianca=0.6)

    assert janelas == []


def test_detectar_polifonia_rejects_non_positive_janela() -> None:
    """``janela_s`` must be strictly positive."""
    with pytest.raises(ValueError, match="janela_s"):
        detectar_polifonia_temporal(_mono_stream(n=3), janela_s=0.0)


# ---------------------------------------------------------------------------
# classificar_trechos
# ---------------------------------------------------------------------------


def test_classificar_trechos_merges_contiguous_windows_of_same_type() -> None:
    """Adjacent windows sharing a label collapse into a single span."""
    deteccoes = _mono_stream(n=20, spacing_s=0.2)
    janelas = detectar_polifonia_temporal(deteccoes, janela_s=0.5, overlap_threshold_s=0.05)

    trechos = classificar_trechos(janelas)

    assert len(trechos) == 1
    tipo, inicio, fim = trechos[0]
    assert tipo == "solo"
    assert inicio == pytest.approx(janelas[0].inicio_s)
    assert fim == pytest.approx(janelas[-1].fim_s)


def test_classificar_trechos_distinguishes_unissono_from_duo() -> None:
    """Tight intervals classify as unissono; wide intervals as duo."""
    unissono_deteccoes = [
        DeteccaoPitch(timestamp_s=0.10, pitch_hz=440.00, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.11, pitch_hz=442.50, confianca=0.9),
    ]
    janelas_unissono = detectar_polifonia_temporal(
        unissono_deteccoes, janela_s=0.5, overlap_threshold_s=0.05
    )
    trechos_unissono = classificar_trechos(janelas_unissono, intervalo_unissono_cents=30.0)
    assert [tipo for tipo, _, _ in trechos_unissono] == ["unissono"]

    duo_deteccoes = [
        DeteccaoPitch(timestamp_s=0.10, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.11, pitch_hz=329.63, confianca=0.9),
    ]
    janelas_duo = detectar_polifonia_temporal(duo_deteccoes, janela_s=0.5, overlap_threshold_s=0.05)
    trechos_duo = classificar_trechos(janelas_duo, intervalo_unissono_cents=30.0)
    assert [tipo for tipo, _, _ in trechos_duo] == ["duo"]


def test_classificar_trechos_emits_solo_then_duo_when_stream_transitions() -> None:
    """A solo prelude followed by a duo span produces two adjacent trechos."""
    deteccoes = [
        DeteccaoPitch(timestamp_s=0.05, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.25, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.80, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.82, pitch_hz=329.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=1.00, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=1.02, pitch_hz=329.63, confianca=0.9),
    ]
    janelas = detectar_polifonia_temporal(deteccoes, janela_s=0.5, overlap_threshold_s=0.05)
    trechos = classificar_trechos(janelas)

    tipos = [t for t, _, _ in trechos]
    assert tipos == ["solo", "duo"]
    assert trechos[0][1] < trechos[0][2] <= trechos[1][1] < trechos[1][2]


def test_classificar_trechos_empty_input_yields_empty_list() -> None:
    assert classificar_trechos([]) == []


# ---------------------------------------------------------------------------
# separar_por_altura
# ---------------------------------------------------------------------------


def test_separar_por_altura_returns_high_voice_above_low_voice() -> None:
    """Two stable notes split into the higher voice and the lower voice."""
    deteccoes: list[DeteccaoPitch] = []
    for i in range(30):
        t = i * 0.05
        deteccoes.append(DeteccaoPitch(timestamp_s=t, pitch_hz=261.63, confianca=0.9))
        deteccoes.append(DeteccaoPitch(timestamp_s=t + 0.001, pitch_hz=329.63, confianca=0.9))

    separacao = separar_por_altura(deteccoes, janela_agrupamento_s=0.05)

    assert len(separacao.voz_aguda) == len(separacao.voz_grave)
    assert len(separacao.voz_aguda) > 0
    assert all(p == pytest.approx(329.63) for p in separacao.voz_aguda.pitches_hz)
    assert all(p == pytest.approx(261.63) for p in separacao.voz_grave.pitches_hz)
    assert separacao.qualidade_separacao == pytest.approx(1.0)


def test_separar_por_altura_low_quality_when_voices_cross() -> None:
    """Wildly inconsistent intervals between voices yield a low quality score."""
    deteccoes: list[DeteccaoPitch] = []
    pares = [
        (261.63, 523.25),
        (293.66, 311.13),
        (329.63, 349.23),
        (261.63, 880.00),
        (220.00, 246.94),
        (174.61, 1046.50),
    ]
    for i, (baixa, alta) in enumerate(pares):
        t = i * 0.1
        deteccoes.append(DeteccaoPitch(timestamp_s=t, pitch_hz=baixa, confianca=0.9))
        deteccoes.append(DeteccaoPitch(timestamp_s=t + 0.001, pitch_hz=alta, confianca=0.9))

    separacao = separar_por_altura(deteccoes, janela_agrupamento_s=0.05)

    assert separacao.qualidade_separacao < 0.5


def test_separar_por_altura_emits_consistent_lengths_for_nota_series() -> None:
    """``NotaSeries`` invariants hold: parallel pitch/tempo arrays stay aligned."""
    deteccoes = [
        DeteccaoPitch(timestamp_s=0.00, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.001, pitch_hz=329.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.10, pitch_hz=261.63, confianca=0.9),
        DeteccaoPitch(timestamp_s=0.101, pitch_hz=329.63, confianca=0.9),
    ]
    separacao = separar_por_altura(deteccoes, janela_agrupamento_s=0.05)

    assert isinstance(separacao.voz_aguda, NotaSeries)
    assert isinstance(separacao.voz_grave, NotaSeries)
    assert len(separacao.voz_aguda.pitches_hz) == len(separacao.voz_aguda.tempos_s)
    assert len(separacao.voz_grave.pitches_hz) == len(separacao.voz_grave.tempos_s)
    assert len(separacao.voz_aguda) == len(separacao.voz_grave)


def test_separar_por_altura_empty_input_returns_empty_series() -> None:
    """No detections means two empty series and a perfect quality score."""
    separacao = separar_por_altura([], janela_agrupamento_s=0.05)

    assert len(separacao.voz_aguda) == 0
    assert len(separacao.voz_grave) == 0
    assert separacao.qualidade_separacao == pytest.approx(1.0)
