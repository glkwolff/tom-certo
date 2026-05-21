"""Breath-event detection from a 30s audio window.

Singing teachers care about breaths for two reasons that the persona
inside SKILL.md will surface to the student:

1. **How long** the breath is — too short (< 100 ms) reads as a panicky
   gasp; 100-200 ms is a healthy mid-phrase intake; 200-500 ms is a
   preparatory inhale for the next big line.
2. **Whether there are any breaths at all** in a long stretch. Singing
   for 8+ seconds without taking a breath is the textbook recipe for the
   "throat-locked / running out of air" feeling.

The detector is intentionally low-tech: a 20 ms-RMS envelope thresholded
against a single ``silence_threshold`` value (defaulting to ``0.05``,
matching the calibration default in ``PreferenciasAluno``). Any
contiguous below-threshold span between 40 ms and 500 ms long is
classified as a breath; longer spans are treated as "the student stopped
singing" and skipped entirely.
"""

from __future__ import annotations

import numpy as np

from auladcanto.domain.batch import RespiracaoMetrics, Respiro, TipoRespiro

_RMS_WINDOW_MS = 20
_MIN_BREATH_MS = 40
_MAX_BREATH_MS = 500
_NORMAL_MIN_MS = 100
_PREPARATORIO_MIN_MS = 200
_ALERTA_GAP_MS = 8000


class RespiracaoAnalyzer:
    """Detect breath events as short silences in the RMS envelope."""

    def __init__(self, sample_rate: int = 44100, silence_threshold: float = 0.05) -> None:
        if sample_rate <= 0:
            raise ValueError(f"RespiracaoAnalyzer: sample_rate must be > 0 (got {sample_rate})")
        if silence_threshold < 0.0:
            raise ValueError(
                f"RespiracaoAnalyzer: silence_threshold must be >= 0 (got {silence_threshold})"
            )
        self._sample_rate = sample_rate
        self._silence_threshold = silence_threshold
        self._rms_window_samples = max(1, int(sample_rate * _RMS_WINDOW_MS / 1000))

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def silence_threshold(self) -> float:
        return self._silence_threshold

    def analyze(self, samples: np.ndarray) -> RespiracaoMetrics:
        """Return the respiracao sub-object for one ``ClosedBatch`` of samples."""
        if samples.size == 0:
            return RespiracaoMetrics(
                respiros_detectados=0,
                respiros=[],
                alerta_sem_respiro=False,
            )

        envelope = self._compute_envelope(samples)
        silent_spans = self._find_silent_spans(envelope)
        respiros = self._classify_spans(silent_spans, envelope.size)
        alerta = self._has_long_voiced_gap(silent_spans, envelope.size, samples.size)

        return RespiracaoMetrics(
            respiros_detectados=len(respiros),
            respiros=respiros,
            alerta_sem_respiro=alerta,
        )

    def _compute_envelope(self, samples: np.ndarray) -> np.ndarray:
        mono = samples.reshape(-1).astype(np.float64, copy=False)
        window = self._rms_window_samples
        if mono.size < window:
            return np.array([float(np.sqrt(np.mean(mono**2)))], dtype=np.float64)

        num_windows = mono.size // window
        trimmed = mono[: num_windows * window].reshape(num_windows, window)
        rms: np.ndarray = np.sqrt(np.mean(trimmed**2, axis=1))
        return rms

    def _find_silent_spans(self, envelope: np.ndarray) -> list[tuple[int, int]]:
        below = envelope < self._silence_threshold
        spans: list[tuple[int, int]] = []
        start: int | None = None
        for index, is_silent in enumerate(below):
            if is_silent and start is None:
                start = index
            elif not is_silent and start is not None:
                spans.append((start, index))
                start = None
        if start is not None:
            spans.append((start, envelope.size))
        return spans

    def _classify_spans(
        self,
        spans: list[tuple[int, int]],
        envelope_length: int,
    ) -> list[Respiro]:
        del envelope_length
        respiros: list[Respiro] = []
        for start, end in spans:
            duration_ms = self._envelope_index_to_ms(end - start)
            if duration_ms < _MIN_BREATH_MS or duration_ms > _MAX_BREATH_MS:
                continue
            timestamp_ms = self._envelope_index_to_ms(start)
            respiros.append(
                Respiro(
                    timestamp_ms=timestamp_ms,
                    duracao_ms=duration_ms,
                    tipo=self._classify_duration(duration_ms),
                )
            )
        return respiros

    def _has_long_voiced_gap(
        self,
        spans: list[tuple[int, int]],
        envelope_length: int,
        sample_count: int,
    ) -> bool:
        total_ms = round(1000 * sample_count / self._sample_rate)
        breath_intervals_ms = [
            (
                self._envelope_index_to_ms(start),
                self._envelope_index_to_ms(end),
                self._envelope_index_to_ms(end - start),
            )
            for start, end in spans
            if _MIN_BREATH_MS <= self._envelope_index_to_ms(end - start) <= _MAX_BREATH_MS
        ]

        if not breath_intervals_ms:
            return total_ms >= _ALERTA_GAP_MS

        prev_end_ms = 0
        for start_ms, end_ms, _duration in breath_intervals_ms:
            if start_ms - prev_end_ms >= _ALERTA_GAP_MS:
                return True
            prev_end_ms = end_ms
        if total_ms - prev_end_ms >= _ALERTA_GAP_MS:
            return True

        _ = envelope_length
        return False

    def _envelope_index_to_ms(self, index: int) -> int:
        return round(index * _RMS_WINDOW_MS)

    @staticmethod
    def _classify_duration(duration_ms: int) -> TipoRespiro:
        if duration_ms < _NORMAL_MIN_MS:
            return "rapido_insuficiente"
        if duration_ms < _PREPARATORIO_MIN_MS:
            return "normal"
        return "preparatorio_longo"


__all__ = [
    "RespiracaoAnalyzer",
]
