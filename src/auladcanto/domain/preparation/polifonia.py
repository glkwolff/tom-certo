"""Vocal polyphony detection over a stream of pitch detections.

Decision D8 of the implementation plan (see
``docs/maestro/plans/auladcanto-mcp-mvp.md``) calls for full hybrid handling
of vocal duos: a song may switch between solo, unisono, and two-voice
passages, and the gabarito must encode each as a separate trecho so the
comparator (phase 3C) can be told which voice the user is targeting.

This module owns the *time-domain* side of that decision. It takes a flat
sequence of pitch detections (any number per timestamp, produced by an
upstream pitch tracker such as CREPE running on the demucs ``vocals`` stem)
and groups them into fixed-width windows. Within each window it clusters
detections by time proximity so two near-simultaneous pitches register as
co-occurring rather than as two consecutive frames.

The companion :func:`classificar_trechos` helper collapses the per-window
results into contiguous trecho-shaped spans (``solo`` / ``unissono`` /
``duo``) ready to be turned into ``Trecho*`` instances by the audio
pipeline.

The frequency-domain split — given a polyphonic window, decide which pitch
belongs to ``voz_aguda`` vs ``voz_grave`` — lives in
:mod:`auladcanto.domain.preparation.separacao` to keep responsibilities
single-purpose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

TipoTrechoDetectado = Literal["solo", "duo", "unissono"]


@dataclass(frozen=True)
class DeteccaoPitch:
    """A single pitch observation at one instant of time.

    ``confianca`` follows the convention used by both CREPE and Basic Pitch:
    a value in ``[0.0, 1.0]`` where higher means the tracker is more sure
    the frame is voiced and the estimated frequency is reliable.
    """

    timestamp_s: float
    pitch_hz: float
    confianca: float


@dataclass(frozen=True)
class JanelaPolifonica:
    """Result of analysing one fixed-width slice of the detection stream.

    ``pitches_simultaneos`` is a list of *groups*: each inner list is the
    set of pitches that the windower decided were co-occurring (i.e. fell
    within ``overlap_threshold_s`` of each other). A window is flagged
    polyphonic if any group has more than one pitch.
    """

    inicio_s: float
    fim_s: float
    pitches_simultaneos: list[list[float]]
    is_polifonica: bool


def detectar_polifonia_temporal(
    deteccoes: list[DeteccaoPitch],
    janela_s: float = 0.5,
    overlap_threshold_s: float = 0.05,
    min_confianca: float = 0.6,
) -> list[JanelaPolifonica]:
    """Slice ``deteccoes`` into windows and flag polyphonic ones.

    The stream is partitioned into back-to-back windows of ``janela_s``
    seconds starting at the earliest detection. Within each window
    detections are sorted by time and grouped greedily: a new group is
    opened whenever the next detection is more than ``overlap_threshold_s``
    away from the running group head. Any group with more than one pitch
    marks the window as polyphonic.

    Detections with ``confianca`` strictly below ``min_confianca`` are
    discarded before grouping — these are usually unvoiced frames mis-fired
    by the tracker and would otherwise inflate the polyphony rate.

    Returns an empty list when no detection clears the confidence threshold.
    """
    if janela_s <= 0.0:
        raise ValueError(f"janela_s must be positive (got {janela_s})")
    if overlap_threshold_s < 0.0:
        raise ValueError(f"overlap_threshold_s must be non-negative (got {overlap_threshold_s})")

    filtradas = [d for d in deteccoes if d.confianca >= min_confianca]
    if not filtradas:
        return []

    filtradas.sort(key=lambda d: d.timestamp_s)
    inicio_global = filtradas[0].timestamp_s
    fim_global = filtradas[-1].timestamp_s

    n_janelas = max(1, int((fim_global - inicio_global) // janela_s) + 1)
    buckets: list[list[DeteccaoPitch]] = [[] for _ in range(n_janelas)]
    for det in filtradas:
        bucket_idx = min(int((det.timestamp_s - inicio_global) // janela_s), n_janelas - 1)
        buckets[bucket_idx].append(det)

    janelas: list[JanelaPolifonica] = []
    for bucket_idx, bucket in enumerate(buckets):
        janela_inicio = inicio_global + bucket_idx * janela_s
        janela_fim = janela_inicio + janela_s
        grupos = _agrupar_simultaneos(bucket, overlap_threshold_s)
        is_poli = any(len(g) > 1 for g in grupos)
        janelas.append(
            JanelaPolifonica(
                inicio_s=janela_inicio,
                fim_s=janela_fim,
                pitches_simultaneos=grupos,
                is_polifonica=is_poli,
            )
        )

    return janelas


def classificar_trechos(
    janelas: list[JanelaPolifonica],
    intervalo_unissono_cents: float = 30.0,
) -> list[tuple[TipoTrechoDetectado, float, float]]:
    """Collapse per-window polyphony flags into contiguous trecho spans.

    Each window is first labelled as one of:

    * ``"solo"`` — ``is_polifonica`` is ``False``.
    * ``"unissono"`` — polyphonic but every simultaneous group has all
      pitches within ``intervalo_unissono_cents`` of each other (typical
      backing-vocal doubling).
    * ``"duo"`` — polyphonic with at least one group spanning more than
      ``intervalo_unissono_cents`` between its extreme pitches.

    Adjacent windows with the same label are merged into a single span so
    callers receive trecho-shaped tuples ``(tipo, inicio_s, fim_s)`` ready
    to be assembled into ``Trecho*`` instances.

    Returns an empty list when ``janelas`` is empty.
    """
    if not janelas:
        return []

    labels: list[TipoTrechoDetectado] = [
        _classificar_janela(j, intervalo_unissono_cents) for j in janelas
    ]

    trechos: list[tuple[TipoTrechoDetectado, float, float]] = []
    span_tipo = labels[0]
    span_inicio = janelas[0].inicio_s
    span_fim = janelas[0].fim_s

    for janela, tipo in zip(janelas[1:], labels[1:], strict=True):
        if tipo == span_tipo:
            span_fim = janela.fim_s
        else:
            trechos.append((span_tipo, span_inicio, span_fim))
            span_tipo = tipo
            span_inicio = janela.inicio_s
            span_fim = janela.fim_s

    trechos.append((span_tipo, span_inicio, span_fim))
    return trechos


def _agrupar_simultaneos(
    deteccoes: list[DeteccaoPitch],
    overlap_threshold_s: float,
) -> list[list[float]]:
    if not deteccoes:
        return []

    grupos: list[list[float]] = []
    grupo_atual: list[float] = [deteccoes[0].pitch_hz]
    grupo_head = deteccoes[0].timestamp_s

    for det in deteccoes[1:]:
        if det.timestamp_s - grupo_head <= overlap_threshold_s:
            grupo_atual.append(det.pitch_hz)
        else:
            grupos.append(grupo_atual)
            grupo_atual = [det.pitch_hz]
            grupo_head = det.timestamp_s

    grupos.append(grupo_atual)
    return grupos


def _classificar_janela(
    janela: JanelaPolifonica,
    intervalo_unissono_cents: float,
) -> TipoTrechoDetectado:
    if not janela.is_polifonica:
        return "solo"

    for grupo in janela.pitches_simultaneos:
        if len(grupo) < 2:
            continue
        pitches_validos = [p for p in grupo if p > 0.0]
        if len(pitches_validos) < 2:
            continue
        spread = _spread_em_cents(pitches_validos)
        if spread > intervalo_unissono_cents:
            return "duo"

    return "unissono"


def _spread_em_cents(pitches_hz: list[float]) -> float:
    minimo = min(pitches_hz)
    maximo = max(pitches_hz)
    if minimo <= 0.0:
        return 0.0
    return 1200.0 * math.log2(maximo / minimo)


__all__ = [
    "DeteccaoPitch",
    "JanelaPolifonica",
    "TipoTrechoDetectado",
    "classificar_trechos",
    "detectar_polifonia_temporal",
]
