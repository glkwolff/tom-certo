"""Vibrato detection from a frame-by-frame pitch contour.

Vibrato is the periodic oscillation a trained singer adds around a sustained
note. Pedagogically:

* **5-7 Hz** with about ``±50`` cents amplitude is healthy/natural.
* **< 4 Hz** reads as ``lento_tremulo`` (slow tremolo, often nerves or
  poor support).
* **> 7 Hz** reads as ``rapido_tenso`` (tight throat, over-pressed).

The detector takes the pitch contour produced by
:class:`auladcanto.domain.analysis.pitch.PitchAnalyzer`, converts it to
cents relative to its own mean (so the amplitude is independent of the
note the student is on) and runs an FFT to find the dominant frequency
inside the ``[2, 12]`` Hz oscillation band. A simple energy ratio
(oscillation-band over total) decides whether the oscillation is real
or just noise; the dominant frequency is then classified against the
``[5, 7]`` Hz natural band to label naturalness.
"""

from __future__ import annotations

import math

import numpy as np

from auladcanto.domain.batch import VibratoMetrics, VibratoNaturalidade

_DETECTION_BAND_HZ = (2.0, 12.0)
_NATURAL_BAND_HZ = (5.0, 7.0)
_ENERGY_RATIO_THRESHOLD = 0.3
_MIN_FRAMES = 20


class VibratoAnalyzer:
    """Detect vibrato on a pitch-frame series and classify its naturalness.

    ``frame_rate_hz`` is the pitch-detector's output rate (frames per second).
    For the default :class:`PitchAnalyzer` configuration (sample_rate=44100,
    hop_size=512) that works out to roughly ``86`` frames/sec; the analyzer
    accepts a configurable value because tests synthesise series at a chosen
    rate to keep their signals short.
    """

    def __init__(self, frame_rate_hz: float = 44.0) -> None:
        if frame_rate_hz <= 0.0:
            raise ValueError(f"VibratoAnalyzer: frame_rate_hz must be > 0 (got {frame_rate_hz})")
        self._frame_rate_hz = frame_rate_hz

    @property
    def frame_rate_hz(self) -> float:
        return self._frame_rate_hz

    def analyze(self, pitch_series_hz: list[float]) -> VibratoMetrics:
        """Return the vibrato sub-object for a pitch contour."""
        usable = [hz for hz in pitch_series_hz if hz > 0.0]
        if len(usable) < _MIN_FRAMES:
            return VibratoMetrics(detectado=False, frequencia_hz=None, naturalidade=None)

        cents_series = self._to_cents_series(usable)
        if cents_series.size < _MIN_FRAMES:
            return VibratoMetrics(detectado=False, frequencia_hz=None, naturalidade=None)

        spectrum, freqs = self._compute_spectrum(cents_series)
        if spectrum.size == 0:
            return VibratoMetrics(detectado=False, frequencia_hz=None, naturalidade=None)

        band_mask = (freqs >= _DETECTION_BAND_HZ[0]) & (freqs <= _DETECTION_BAND_HZ[1])
        if not bool(np.any(band_mask)):
            return VibratoMetrics(detectado=False, frequencia_hz=None, naturalidade=None)

        total_energy = float(np.sum(spectrum))
        if total_energy <= 0.0:
            return VibratoMetrics(detectado=False, frequencia_hz=None, naturalidade=None)

        band_spectrum = spectrum[band_mask]
        band_freqs = freqs[band_mask]
        band_energy = float(np.sum(band_spectrum))
        ratio = band_energy / total_energy
        if ratio < _ENERGY_RATIO_THRESHOLD:
            return VibratoMetrics(detectado=False, frequencia_hz=None, naturalidade=None)

        dominant_index = int(np.argmax(band_spectrum))
        dominant_hz = float(band_freqs[dominant_index])
        return VibratoMetrics(
            detectado=True,
            frequencia_hz=round(dominant_hz, 4),
            naturalidade=self._classify_naturalidade(dominant_hz),
        )

    @staticmethod
    def _to_cents_series(pitch_series_hz: list[float]) -> np.ndarray:
        arr = np.asarray(pitch_series_hz, dtype=np.float64)
        reference = float(np.mean(arr))
        if reference <= 0.0:
            return np.empty(0, dtype=np.float64)
        cents = 1200.0 * np.log2(arr / reference)
        cents -= float(np.mean(cents))
        return cents

    def _compute_spectrum(self, cents_series: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        windowed = cents_series * np.hanning(cents_series.size)
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(cents_series.size, d=1.0 / self._frame_rate_hz)
        if spectrum.size > 0:
            spectrum[0] = 0.0
        return spectrum, freqs

    @staticmethod
    def _classify_naturalidade(dominant_hz: float) -> VibratoNaturalidade:
        if math.isclose(dominant_hz, _NATURAL_BAND_HZ[0]) or math.isclose(
            dominant_hz, _NATURAL_BAND_HZ[1]
        ):
            return "natural"
        if _NATURAL_BAND_HZ[0] <= dominant_hz <= _NATURAL_BAND_HZ[1]:
            return "natural"
        if dominant_hz < _NATURAL_BAND_HZ[0]:
            return "lento_tremulo"
        return "rapido_tenso"


__all__ = [
    "VibratoAnalyzer",
]
