"""Timing / tempo analyzer (phase 3B).

Given a 30 s batch of audio (or a precomputed list of onset timestamps), this
module emits a :class:`auladcanto.domain.batch.TimingMetrics` payload with the
user's effective BPM, deviation from the score, whether they accelerated
inside the batch, and a 0-1 rhythmic irregularity score.

Onset detection uses ``aubio.tempo`` when the optional ``[audio]`` extra is
installed; otherwise it falls back to a simple energy-based peak picker over
~20 ms windows. The fallback is deliberately conservative (high threshold,
short refractory period) so unit tests can drive the pipeline without aubio.

BPM estimation deliberately uses the *median* of inter-onset intervals rather
than the mean — a single missed or doubled onset would otherwise dominate a
mean over the typical 30-60 onsets per batch.
"""

from __future__ import annotations

import numpy as np

from auladcanto.domain.batch import TimingMetrics

_DEFAULT_ENERGY_WINDOW_MS = 20.0
_DEFAULT_ENERGY_THRESHOLD = 0.05
_DEFAULT_REFRACTORY_MS = 80.0
_ACCELERATION_THRESHOLD_BPM = 5.0
_IRREGULARIDADE_MAX = 1.0


class TimingAnalyzer:
    """Compute BPM and rhythmic regularity metrics for a 30 s batch."""

    def __init__(
        self,
        sample_rate: int = 44100,
        energy_window_ms: float = _DEFAULT_ENERGY_WINDOW_MS,
        energy_threshold: float = _DEFAULT_ENERGY_THRESHOLD,
        refractory_ms: float = _DEFAULT_REFRACTORY_MS,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError(f"TimingAnalyzer: sample_rate must be > 0 (got {sample_rate})")
        if energy_window_ms <= 0.0:
            raise ValueError(
                f"TimingAnalyzer: energy_window_ms must be > 0 (got {energy_window_ms})"
            )
        if energy_threshold < 0.0:
            raise ValueError(
                f"TimingAnalyzer: energy_threshold must be >= 0 (got {energy_threshold})"
            )
        if refractory_ms < 0.0:
            raise ValueError(f"TimingAnalyzer: refractory_ms must be >= 0 (got {refractory_ms})")
        self._sample_rate = sample_rate
        self._energy_window_ms = energy_window_ms
        self._energy_threshold = energy_threshold
        self._refractory_ms = refractory_ms

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def detect_onsets(self, samples: np.ndarray) -> list[float]:
        """Return onset timestamps in seconds within the batch.

        Uses ``aubio.tempo`` when available; otherwise falls back to an
        energy-based peak picker. Returned timestamps are strictly increasing.
        """
        view = samples.reshape(-1) if samples.ndim > 1 else samples
        if view.size == 0:
            return []
        aubio_onsets = self._detect_onsets_aubio(view)
        if aubio_onsets is not None:
            return aubio_onsets
        return self._detect_onsets_energy(view)

    def compute_metrics(
        self,
        onsets_s: list[float],
        bpm_gabarito: float,
        batch_duration_s: float = 30.0,
    ) -> TimingMetrics:
        """Derive :class:`TimingMetrics` from a list of onset timestamps.

        * ``bpm_usuario`` — 60 / median(inter-onset intervals), 0 if undefined.
        * ``desvio_bpm`` — ``bpm_usuario - bpm_gabarito``.
        * ``acelerando_no_batch`` — True when the BPM of the second half of
          the batch exceeds the first half by more than 5 BPM.
        * ``irregularidade_ritmica`` — ``stddev / mean`` of the intervals,
          clamped to [0, 1]; 0 means perfectly regular.
        """
        if bpm_gabarito < 0.0:
            raise ValueError(f"TimingAnalyzer: bpm_gabarito must be >= 0 (got {bpm_gabarito})")
        if batch_duration_s <= 0.0:
            raise ValueError(
                f"TimingAnalyzer: batch_duration_s must be > 0 (got {batch_duration_s})"
            )

        intervals = self._intervals(onsets_s)
        bpm_usuario = self._bpm_from_intervals(intervals)
        desvio_bpm = bpm_usuario - bpm_gabarito
        acelerando = self._detect_acceleration(onsets_s, batch_duration_s)
        irregularidade = self._irregularidade(intervals)

        return TimingMetrics(
            bpm_usuario=bpm_usuario,
            bpm_gabarito=float(bpm_gabarito),
            desvio_bpm=desvio_bpm,
            acelerando_no_batch=acelerando,
            irregularidade_ritmica=irregularidade,
        )

    def _detect_onsets_aubio(self, samples: np.ndarray) -> list[float] | None:
        try:
            import aubio
        except ImportError:
            return None

        hop_size = 512
        window_size = 1024
        try:
            tempo = aubio.tempo("default", window_size, hop_size, self._sample_rate)
        except (RuntimeError, ValueError):
            return None

        audio = samples.astype(np.float32, copy=False)
        onsets: list[float] = []
        for start in range(0, audio.size - hop_size + 1, hop_size):
            frame = audio[start : start + hop_size]
            if frame.size < hop_size:
                break
            if tempo(frame):
                onsets.append(float(tempo.get_last_s()))
        return onsets

    def _detect_onsets_energy(self, samples: np.ndarray) -> list[float]:
        window_size = max(1, int(self._sample_rate * self._energy_window_ms / 1000.0))
        refractory_windows = max(1, round(self._refractory_ms / self._energy_window_ms))
        num_windows = samples.size // window_size
        if num_windows < 2:
            return []
        trimmed = samples[: num_windows * window_size].astype(np.float64, copy=False)
        frames = trimmed.reshape(num_windows, window_size)
        energies = np.sqrt(np.mean(np.square(frames), axis=1))

        threshold = float(self._energy_threshold)
        onsets: list[float] = []
        last_idx = -refractory_windows
        for i in range(1, num_windows):
            prev_energy = float(energies[i - 1])
            cur_energy = float(energies[i])
            if (
                cur_energy >= threshold
                and cur_energy > prev_energy
                and (i - last_idx) >= refractory_windows
            ):
                onsets.append((i * window_size) / float(self._sample_rate))
                last_idx = i
        return onsets

    @staticmethod
    def _intervals(onsets_s: list[float]) -> np.ndarray:
        if len(onsets_s) < 2:
            return np.zeros(0, dtype=np.float64)
        arr = np.asarray(onsets_s, dtype=np.float64)
        diffs = np.diff(arr)
        return diffs[diffs > 0.0]

    @staticmethod
    def _bpm_from_intervals(intervals: np.ndarray) -> float:
        if intervals.size == 0:
            return 0.0
        median_interval = float(np.median(intervals))
        if median_interval <= 0.0:
            return 0.0
        return 60.0 / median_interval

    def _detect_acceleration(
        self,
        onsets_s: list[float],
        batch_duration_s: float,
    ) -> bool:
        if len(onsets_s) < 4:
            return False
        midpoint = batch_duration_s / 2.0
        first_half = [t for t in onsets_s if t <= midpoint]
        second_half = [t for t in onsets_s if t > midpoint]
        if len(first_half) < 2 or len(second_half) < 2:
            return False
        bpm_first = self._bpm_from_intervals(self._intervals(first_half))
        bpm_second = self._bpm_from_intervals(self._intervals(second_half))
        if bpm_first <= 0.0 or bpm_second <= 0.0:
            return False
        return (bpm_second - bpm_first) > _ACCELERATION_THRESHOLD_BPM

    @staticmethod
    def _irregularidade(intervals: np.ndarray) -> float:
        if intervals.size < 2:
            return 0.0
        mean_interval = float(np.mean(intervals))
        if mean_interval <= 0.0:
            return 0.0
        std_interval = float(np.std(intervals))
        ratio = std_interval / mean_interval
        if ratio < 0.0:
            return 0.0
        if ratio > _IRREGULARIDADE_MAX:
            return _IRREGULARIDADE_MAX
        return ratio


__all__ = [
    "TimingAnalyzer",
]
