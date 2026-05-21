"""Mir-eval-style melody metrics over aligned user/reference pitch arrays.

The :class:`Scorer` consumes one or more :class:`AlinhamentoTrecho` objects
produced by :class:`auladcanto.domain.comparator.aligner.Aligner` and emits a
:class:`ComparisonResult` per trecho plus an aggregate over the batch. The
fields mirror the MIREX melody-extraction conventions so the implementation
can delegate to ``mir_eval.melody.evaluate`` when the ``[audio]`` extra is
installed, with a numpy fallback that produces equivalent numbers without the
heavy dependency.

The output is then converted into the schema-v1
:class:`auladcanto.domain.batch.PitchMetrics` so the comparator's contribution
to the batch report is a drop-in replacement for the one produced by
:class:`auladcanto.domain.analysis.pitch.PitchAnalyzer`. The ataque
classification is supplied externally because it depends on onsets, not on
sustained pitch — see :mod:`auladcanto.domain.analysis.ataque`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from auladcanto.domain.batch import AtaquePredominante, MomentoCritico, PitchMetrics
from auladcanto.domain.comparator.aligner import AlinhamentoTrecho

_CENTS_PER_OCTAVE = 1200.0
_DEFAULT_TOLERANCE_CENTS = 50.0
_TOP_MOMENTOS = 5
_C0_HZ = 16.3516
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_MIN_AGGREGATE_DURATION_S = 1e-6


def _try_import_mir_eval() -> object | None:
    """Import ``mir_eval`` lazily so the scorer works without the audio extra."""
    try:
        import mir_eval
    except ImportError:
        return None
    return mir_eval  # type: ignore[no-any-return]


@dataclass(frozen=True)
class ComparisonResult:
    """Aggregate metrics for one alignment (or one batch of alignments).

    The percentages are in ``[0, 100]``. ``desvio_padrao_cents`` is the
    standard deviation of voiced-frame cents errors; ``momentos_criticos``
    carries the top-N worst voiced-frame errors as schema-v1
    :class:`MomentoCritico` entries. ``duracao_s`` records how long the
    underlying time slice was so :meth:`Scorer.aggregate` can compute a
    duration-weighted batch summary.
    """

    precisao_pitch_pct: float
    precisao_oitava_pct: float
    notas_cantadas_pct: float
    cantou_no_silencio_pct: float
    score_geral_pct: float
    desvio_padrao_cents: float
    momentos_criticos: list[MomentoCritico] = field(default_factory=list)
    duracao_s: float = 0.0


class Scorer:
    """Compute MIREX-style melody metrics over aligned arrays.

    The scorer is stateless; one instance per session is enough. ``mir_eval``
    is imported once on construction and used when available; otherwise the
    same metrics are computed with vectorised numpy operations.
    """

    def __init__(self, tolerance_cents: float = _DEFAULT_TOLERANCE_CENTS) -> None:
        if tolerance_cents <= 0.0:
            raise ValueError(f"Scorer: tolerance_cents must be > 0 (got {tolerance_cents})")
        self._tolerance_cents = tolerance_cents
        self._mir_eval = _try_import_mir_eval()

    @property
    def tolerance_cents(self) -> float:
        return self._tolerance_cents

    @property
    def has_mir_eval(self) -> bool:
        return self._mir_eval is not None

    def score_trecho(self, alinhamento: AlinhamentoTrecho) -> ComparisonResult:
        """Return metrics for a single aligned trecho."""
        ref_freqs = np.asarray(alinhamento.ref_freqs, dtype=np.float64)
        user_freqs = np.asarray(alinhamento.user_freqs, dtype=np.float64)
        ref_times = np.asarray(alinhamento.ref_times, dtype=np.float64)
        duracao = float(ref_times[-1] - ref_times[0]) if ref_times.size >= 2 else 0.0

        if ref_freqs.size == 0 or user_freqs.size == 0:
            return ComparisonResult(
                precisao_pitch_pct=0.0,
                precisao_oitava_pct=0.0,
                notas_cantadas_pct=0.0,
                cantou_no_silencio_pct=0.0,
                score_geral_pct=0.0,
                desvio_padrao_cents=0.0,
                momentos_criticos=[],
                duracao_s=duracao,
            )

        ref_voicing = ref_freqs > 0.0
        user_voicing = user_freqs > 0.0

        notas_cantadas_pct = _voicing_recall(ref_voicing, user_voicing)
        cantou_no_silencio_pct = _voicing_false_positive(ref_voicing, user_voicing)
        precisao_pitch_pct, precisao_oitava_pct, desvio_padrao_cents, errors = (
            self._compute_pitch_accuracies(ref_freqs, user_freqs, ref_voicing, user_voicing)
        )
        score_geral_pct = _overall_accuracy(
            precisao_pitch_pct=precisao_pitch_pct,
            notas_cantadas_pct=notas_cantadas_pct,
            cantou_no_silencio_pct=cantou_no_silencio_pct,
        )
        momentos = self._top_momentos(
            errors=errors,
            ref_times=ref_times,
            ref_freqs=ref_freqs,
        )

        return ComparisonResult(
            precisao_pitch_pct=round(precisao_pitch_pct, 4),
            precisao_oitava_pct=round(precisao_oitava_pct, 4),
            notas_cantadas_pct=round(notas_cantadas_pct, 4),
            cantou_no_silencio_pct=round(cantou_no_silencio_pct, 4),
            score_geral_pct=round(score_geral_pct, 4),
            desvio_padrao_cents=round(desvio_padrao_cents, 4),
            momentos_criticos=momentos,
            duracao_s=duracao,
        )

    def aggregate(self, results: list[ComparisonResult]) -> ComparisonResult:
        """Combine per-trecho results into a duration-weighted batch summary.

        Trechos with non-positive ``duracao_s`` (single-sample slices and
        empty alignments) contribute with weight ``1.0`` so they still
        influence the average; in production the per-trecho durations are
        large enough that this rounding has no visible effect.
        """
        if not results:
            return ComparisonResult(
                precisao_pitch_pct=0.0,
                precisao_oitava_pct=0.0,
                notas_cantadas_pct=0.0,
                cantou_no_silencio_pct=0.0,
                score_geral_pct=0.0,
                desvio_padrao_cents=0.0,
                momentos_criticos=[],
                duracao_s=0.0,
            )

        weights = np.asarray(
            [max(_MIN_AGGREGATE_DURATION_S, result.duracao_s) for result in results],
            dtype=np.float64,
        )
        total_weight = float(np.sum(weights))

        def _weighted(values: list[float]) -> float:
            return float(np.dot(weights, np.asarray(values, dtype=np.float64)) / total_weight)

        all_momentos: list[MomentoCritico] = []
        for result in results:
            all_momentos.extend(result.momentos_criticos)
        all_momentos.sort(key=lambda m: abs(m.erro_cents), reverse=True)
        top_momentos = sorted(all_momentos[:_TOP_MOMENTOS], key=lambda m: m.timestamp_ms)

        return ComparisonResult(
            precisao_pitch_pct=round(_weighted([r.precisao_pitch_pct for r in results]), 4),
            precisao_oitava_pct=round(_weighted([r.precisao_oitava_pct for r in results]), 4),
            notas_cantadas_pct=round(_weighted([r.notas_cantadas_pct for r in results]), 4),
            cantou_no_silencio_pct=round(_weighted([r.cantou_no_silencio_pct for r in results]), 4),
            score_geral_pct=round(_weighted([r.score_geral_pct for r in results]), 4),
            desvio_padrao_cents=round(_weighted([r.desvio_padrao_cents for r in results]), 4),
            momentos_criticos=top_momentos,
            duracao_s=float(np.sum([r.duracao_s for r in results])),
        )

    def to_pitch_metrics(
        self,
        aggregate: ComparisonResult,
        ataque_predominante: AtaquePredominante = "indeterminado",
    ) -> PitchMetrics:
        """Convert an aggregated :class:`ComparisonResult` to schema-v1 ``PitchMetrics``."""
        return PitchMetrics(
            notas_corretas_pct=round(_clamp_pct(aggregate.precisao_pitch_pct), 4),
            precisao_oitava_pct=round(_clamp_pct(aggregate.precisao_oitava_pct), 4),
            desvio_padrao_cents=round(max(0.0, aggregate.desvio_padrao_cents), 4),
            ataque_predominante=ataque_predominante,
            momentos_criticos=list(aggregate.momentos_criticos),
        )

    def _compute_pitch_accuracies(
        self,
        ref_freqs: np.ndarray,
        user_freqs: np.ndarray,
        ref_voicing: np.ndarray,
        user_voicing: np.ndarray,
    ) -> tuple[float, float, float, np.ndarray]:
        comparable = ref_voicing & user_voicing
        errors = np.zeros(ref_freqs.shape, dtype=np.float64)
        if not bool(np.any(comparable)):
            return 0.0, 0.0, 0.0, errors

        ref_cents = _hz_array_to_cents(ref_freqs)
        user_cents = _hz_array_to_cents(user_freqs)
        diff = user_cents - ref_cents
        errors = np.where(comparable, diff, 0.0)

        voiced_diff = diff[comparable]
        chroma_diff = _fold_to_chroma(voiced_diff)
        denominator = float(np.sum(ref_voicing))
        if denominator == 0.0:
            return 0.0, 0.0, 0.0, errors
        raw_hits = float(np.sum(np.abs(voiced_diff) <= self._tolerance_cents))
        chroma_hits = float(np.sum(np.abs(chroma_diff) <= self._tolerance_cents))
        precisao_pitch_pct = 100.0 * raw_hits / denominator
        precisao_oitava_pct = 100.0 * chroma_hits / denominator
        desvio_padrao_cents = float(np.std(voiced_diff)) if voiced_diff.size > 0 else 0.0
        return precisao_pitch_pct, precisao_oitava_pct, desvio_padrao_cents, errors

    def _top_momentos(
        self,
        errors: np.ndarray,
        ref_times: np.ndarray,
        ref_freqs: np.ndarray,
    ) -> list[MomentoCritico]:
        if errors.size == 0:
            return []
        offending_mask = np.abs(errors) > self._tolerance_cents
        if not bool(np.any(offending_mask)):
            return []

        offending_indices = np.flatnonzero(offending_mask)
        sorted_indices = offending_indices[np.argsort(-np.abs(errors[offending_indices]))]
        top_indices = sorted_indices[:_TOP_MOMENTOS]

        momentos: list[MomentoCritico] = []
        for index in top_indices:
            timestamp_ms = round(float(ref_times[index]) * 1000.0)
            nota_alvo = _hz_to_note_name(float(ref_freqs[index]))
            momentos.append(
                MomentoCritico(
                    timestamp_ms=max(0, timestamp_ms),
                    nota_alvo=nota_alvo,
                    erro_cents=round(float(errors[index])),
                )
            )
        momentos.sort(key=lambda m: m.timestamp_ms)
        return momentos


def _voicing_recall(ref_voicing: np.ndarray, user_voicing: np.ndarray) -> float:
    denominator = float(np.sum(ref_voicing))
    if denominator == 0.0:
        return 0.0
    numerator = float(np.sum(ref_voicing & user_voicing))
    return 100.0 * numerator / denominator


def _voicing_false_positive(ref_voicing: np.ndarray, user_voicing: np.ndarray) -> float:
    silent = ~ref_voicing
    denominator = float(np.sum(silent))
    if denominator == 0.0:
        return 0.0
    numerator = float(np.sum(silent & user_voicing))
    return 100.0 * numerator / denominator


def _overall_accuracy(
    *,
    precisao_pitch_pct: float,
    notas_cantadas_pct: float,
    cantou_no_silencio_pct: float,
) -> float:
    voicing_term = (notas_cantadas_pct + (100.0 - cantou_no_silencio_pct)) / 2.0
    return _clamp_pct((precisao_pitch_pct + voicing_term) / 2.0)


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, value))


def _hz_array_to_cents(freqs: np.ndarray) -> np.ndarray:
    safe = np.where(freqs > 0.0, freqs, np.nan)
    cents = 1200.0 * np.log2(safe / _C0_HZ)
    return np.where(np.isnan(cents), 0.0, cents)


def _fold_to_chroma(cents: np.ndarray) -> np.ndarray:
    folded = ((cents + _CENTS_PER_OCTAVE / 2.0) % _CENTS_PER_OCTAVE) - _CENTS_PER_OCTAVE / 2.0
    return folded


def _hz_to_note_name(freq_hz: float) -> str:
    if freq_hz <= 0.0:
        return "?"
    midi = 69.0 + 12.0 * math.log2(freq_hz / 440.0)
    midi_round = round(midi)
    note = _NOTE_NAMES[midi_round % 12]
    octave = midi_round // 12 - 1
    return f"{note}{octave}"


__all__ = [
    "ComparisonResult",
    "Scorer",
]
