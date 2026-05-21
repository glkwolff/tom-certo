"""Mental-transposition detector (phase 3B).

Some students consistently sing a fixed number of semitones away from the
reference — for instance, comfortably down a minor third because the original
key sits above their range. That looks identical to "being out of tune" if you
only inspect average pitch error, but it has a very different remedy: offer
to transpose the song instead of asking the student to push their range.

The detector bins per-frame cents offsets into the nearest semitone bucket
and checks whether one non-zero bucket dominates the batch. The
:class:`auladcanto.domain.batch.TransposicaoDetectada` payload reports whether
the heuristic fired, by how many semitones, and with what confidence
(fraction of valid frames in the winning bucket).
"""

from __future__ import annotations

import numpy as np

from auladcanto.domain.batch import TransposicaoDetectada

_CENTS_PER_SEMITONE = 100.0
_DEFAULT_MIN_CONFIANCA = 0.7
_MAX_CONFIANCA = 1.0


class TransposicaoDetector:
    """Detect a consistent semitone offset between user pitch and reference pitch."""

    def __init__(self, min_confianca: float = _DEFAULT_MIN_CONFIANCA) -> None:
        if not 0.0 < min_confianca <= _MAX_CONFIANCA:
            raise ValueError(
                f"TransposicaoDetector: min_confianca must be in (0, 1] (got {min_confianca})"
            )
        self._min_confianca = min_confianca

    @property
    def min_confianca(self) -> float:
        return self._min_confianca

    def detect(
        self,
        user_pitches_hz: list[float],
        reference_pitches_hz: list[float],
    ) -> TransposicaoDetectada:
        """Return a :class:`TransposicaoDetectada` for the batch.

        Pairs are formed positionally up to ``min(len(user), len(reference))``;
        any frame where either side is non-positive (commonly ``0.0`` from a
        silent or unvoiced frame) is skipped. With fewer than two valid
        frame pairs the heuristic abstains and returns ``detectada=False``.
        """
        if not user_pitches_hz or not reference_pitches_hz:
            return TransposicaoDetectada(detectada=False, semitons=0, confianca=0.0)

        pairs = self._valid_pairs(user_pitches_hz, reference_pitches_hz)
        if pairs.shape[0] < 2:
            return TransposicaoDetectada(detectada=False, semitons=0, confianca=0.0)

        cents_offset = 1200.0 * np.log2(pairs[:, 0] / pairs[:, 1])
        semitone_bins = np.round(cents_offset / _CENTS_PER_SEMITONE).astype(np.int64)
        unique, counts = np.unique(semitone_bins, return_counts=True)
        total = int(pairs.shape[0])
        dominant_idx = int(np.argmax(counts))
        dominant_bin = int(unique[dominant_idx])
        dominant_count = int(counts[dominant_idx])
        confianca = dominant_count / float(total)

        if dominant_bin == 0 or confianca < self._min_confianca:
            return TransposicaoDetectada(
                detectada=False,
                semitons=0,
                confianca=min(_MAX_CONFIANCA, max(0.0, confianca)),
            )

        return TransposicaoDetectada(
            detectada=True,
            semitons=dominant_bin,
            confianca=min(_MAX_CONFIANCA, max(0.0, confianca)),
        )

    @staticmethod
    def _valid_pairs(
        user_pitches_hz: list[float],
        reference_pitches_hz: list[float],
    ) -> np.ndarray:
        length = min(len(user_pitches_hz), len(reference_pitches_hz))
        if length == 0:
            return np.zeros((0, 2), dtype=np.float64)
        user_arr = np.asarray(user_pitches_hz[:length], dtype=np.float64)
        ref_arr = np.asarray(reference_pitches_hz[:length], dtype=np.float64)
        mask = (user_arr > 0.0) & (ref_arr > 0.0)
        if not bool(np.any(mask)):
            return np.zeros((0, 2), dtype=np.float64)
        return np.stack([user_arr[mask], ref_arr[mask]], axis=1)


__all__ = [
    "TransposicaoDetector",
]
