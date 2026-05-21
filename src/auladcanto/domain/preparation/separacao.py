"""Naive pitch-altitude voice separation for two-voice vocal passages.

Given a flat list of polyphonic pitch detections (the output the upstream
tracker emits when more than one voice is sounding), this module splits the
detections into two parallel :class:`NotaSeries` instances — one for the
higher voice (``voz_aguda``) and one for the lower voice (``voz_grave``).

The strategy is intentionally simple: for every short agrupamento window we
take the highest pitch as the aguda and the lowest as the grave. When only a
single pitch is detected in a window the same pitch is replicated into both
voices so the timeline stays aligned (the singer can pick either part and
still see a target). The companion ``qualidade_separacao`` score reports
whether the gap between the two voices stayed consistent — a stable
interval suggests a clean separation, while a chaotic or crossing gap means
the upstream tracker is probably misattributing partials and downstream
consumers should treat the result with caution.

This is the dumb-but-honest baseline expected by decision D8 of the
implementation plan. A future iteration may replace it with a tracker that
follows continuous voice contours (e.g. via the Viterbi pass over a CRF on
top of CREPE's harmonic confidence map), but for the MVP a single trecho
flagged ``duo`` with this separation is already enough for the comparator
to give per-voice feedback.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from auladcanto.domain.gabarito import NotaSeries
from auladcanto.domain.preparation.polifonia import DeteccaoPitch


@dataclass(frozen=True)
class SeparacaoVozes:
    """Two-voice split of a polyphonic detection stream.

    ``voz_aguda`` and ``voz_grave`` share the same length and timeline so
    they can be aligned frame-by-frame by the comparator.
    ``qualidade_separacao`` lives in ``[0.0, 1.0]``: ``1.0`` means the
    interval between the two voices was perfectly constant, ``0.0`` means
    the voices crossed or swung erratically. The comparator surfaces this
    score as an alert on the gabarito quality envelope.
    """

    voz_aguda: NotaSeries
    voz_grave: NotaSeries
    qualidade_separacao: float


def separar_por_altura(
    deteccoes_polifonicas: list[DeteccaoPitch],
    janela_agrupamento_s: float = 0.05,
) -> SeparacaoVozes:
    """Split a polyphonic detection stream into ``aguda`` and ``grave`` voices.

    Detections are bucketed into windows of ``janela_agrupamento_s`` seconds
    starting at the earliest detection. Inside each bucket the maximum
    frequency is recorded as the aguda sample and the minimum as the grave;
    single-pitch buckets emit the same value on both tracks. Each bucket
    contributes one sample whose timestamp is the average of the original
    detection timestamps in the bucket, preserving causal ordering.

    ``qualidade_separacao`` is derived from the per-frame cents-gap between
    the two voices: a low coefficient of variation maps to a high score.
    The full formula is documented inline; see also the unit tests in
    ``tests/unit/test_polifonia.py`` for representative cases.

    Empty input yields empty :class:`NotaSeries` and a perfect quality
    score (there is nothing to disagree about).
    """
    if janela_agrupamento_s <= 0.0:
        raise ValueError(f"janela_agrupamento_s must be positive (got {janela_agrupamento_s})")

    if not deteccoes_polifonicas:
        return SeparacaoVozes(
            voz_aguda=NotaSeries(pitches_hz=[], tempos_s=[]),
            voz_grave=NotaSeries(pitches_hz=[], tempos_s=[]),
            qualidade_separacao=1.0,
        )

    ordenadas = sorted(deteccoes_polifonicas, key=lambda d: d.timestamp_s)

    aguda_pitches: list[float] = []
    aguda_tempos: list[float] = []
    grave_pitches: list[float] = []
    grave_tempos: list[float] = []

    bucket: list[DeteccaoPitch] = [ordenadas[0]]
    bucket_inicio = ordenadas[0].timestamp_s
    for det in ordenadas[1:]:
        if det.timestamp_s - bucket_inicio < janela_agrupamento_s:
            bucket.append(det)
        else:
            _flush_bucket(bucket, aguda_pitches, aguda_tempos, grave_pitches, grave_tempos)
            bucket = [det]
            bucket_inicio = det.timestamp_s

    _flush_bucket(bucket, aguda_pitches, aguda_tempos, grave_pitches, grave_tempos)

    qualidade = _calcular_qualidade(aguda_pitches, grave_pitches)

    return SeparacaoVozes(
        voz_aguda=NotaSeries(pitches_hz=aguda_pitches, tempos_s=aguda_tempos),
        voz_grave=NotaSeries(pitches_hz=grave_pitches, tempos_s=grave_tempos),
        qualidade_separacao=qualidade,
    )


def _flush_bucket(
    bucket: list[DeteccaoPitch],
    aguda_pitches: list[float],
    aguda_tempos: list[float],
    grave_pitches: list[float],
    grave_tempos: list[float],
) -> None:
    pitches = [d.pitch_hz for d in bucket]
    tempos = [d.timestamp_s for d in bucket]
    tempo_medio = float(np.mean(tempos))
    aguda_pitches.append(max(pitches))
    grave_pitches.append(min(pitches))
    aguda_tempos.append(tempo_medio)
    grave_tempos.append(tempo_medio)


def _calcular_qualidade(aguda: list[float], grave: list[float]) -> float:
    if not aguda or not grave:
        return 1.0

    aguda_arr = np.asarray(aguda, dtype=np.float64)
    grave_arr = np.asarray(grave, dtype=np.float64)
    validos = (aguda_arr > 0.0) & (grave_arr > 0.0)
    if not np.any(validos):
        return 1.0

    aguda_validos = aguda_arr[validos]
    grave_validos = grave_arr[validos]
    gap_cents = 1200.0 * np.log2(aguda_validos / grave_validos)

    if np.any(gap_cents < 0.0):
        return 0.0

    if gap_cents.size < 2:
        return 1.0

    media = float(np.mean(gap_cents))
    if media <= 0.0:
        return 0.0

    desvio = float(np.std(gap_cents))
    coef_variacao = desvio / media
    qualidade = 1.0 / (1.0 + coef_variacao)
    return float(np.clip(qualidade, 0.0, 1.0))


__all__ = [
    "SeparacaoVozes",
    "separar_por_altura",
]
