"""Frame-by-frame pitch detection and pitch-vs-gabarito comparison.

This module implements phase 3B's pitch analyzer. The plan (section 3.5,
decision D2) elects ``aubio.pitch`` (YIN) as the realtime detector. ``aubio``
lives in the ``[audio]`` optional extra because its C build still fails on
fresh Python releases (cf. pyproject ``audio`` extra gating ``aubio`` to
``python_version < '3.13'``); the rest of the package has to keep working
without it so unit tests do not need a heavy native dependency installed.

The class therefore takes two paths:

* If ``aubio`` imports cleanly, ``detect_pitches`` delegates to
  ``aubio.pitch`` configured for YIN. This is the production path.
* Otherwise a pure-numpy autocorrelation detector is used. It is good
  enough to lock onto a clean sinusoid in tests and to make the pipeline
  observable without the audio extra installed.

``compute_metrics`` is independent of which detector produced the
``PitchDetection`` list; it only consumes detections and an optional
reference contour. Cents error uses ``1200 * log2(detected / reference)``
which is the standard definition from MIDI tuning.

Octave precision (``precisao_oitava_pct``) collapses pitches to their
chroma equivalents (``pitch % 1200`` cents from C) so a note sung an
octave away still counts as "the right note, wrong octave" — that is the
metric the SKILL.md persona uses to distinguish "wrong note" from
"intonation drift".

``ataque_predominante`` is delegated to an optional callable supplied by
phase 3B-b's ``ataque.py`` so this module stays decoupled from onset
detection; a missing classifier returns ``"indeterminado"`` per the schema.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

import numpy as np

from auladcanto.domain.batch import AtaquePredominante, MomentoCritico, PitchMetrics

_CENTS_TOLERANCE = 50.0
_CENTS_PER_OCTAVE = 1200.0
_MIN_PITCH_HZ = 50.0
_MAX_PITCH_HZ = 1500.0
_MIN_CONFIDENCE = 0.3
_TOP_MOMENTOS = 5


@dataclass(frozen=True)
class PitchDetection:
    """One pitch estimate produced for a single analysis frame.

    ``pitch_hz`` is 0.0 when the detector returned no usable estimate (silent
    or noisy frame); ``confianca`` is the detector's own confidence score in
    the ``[0.0, 1.0]`` range.
    """

    timestamp_ms: int
    pitch_hz: float
    confianca: float


AtaqueClassifier: TypeAlias = Callable[[list[PitchDetection]], AtaquePredominante]


def _try_import_aubio() -> object | None:
    """Import ``aubio`` lazily so the analyzer works without the audio extra."""
    try:
        import aubio
    except ImportError:
        return None
    return aubio  # type: ignore[no-any-return]


def _cents_error(detected_hz: float, reference_hz: float) -> float:
    """Return the cents distance from ``detected_hz`` up to ``reference_hz``."""
    if detected_hz <= 0.0 or reference_hz <= 0.0:
        return float("inf")
    return _CENTS_PER_OCTAVE * math.log2(detected_hz / reference_hz)


def _chroma_cents_error(detected_hz: float, reference_hz: float) -> float:
    """Return the cents distance reduced to a single octave."""
    raw = _cents_error(detected_hz, reference_hz)
    if not math.isfinite(raw):
        return float("inf")
    folded = ((raw + _CENTS_PER_OCTAVE / 2.0) % _CENTS_PER_OCTAVE) - _CENTS_PER_OCTAVE / 2.0
    return folded


class PitchAnalyzer:
    """Frame-by-frame pitch tracker plus pitch-vs-gabarito metrics.

    The analyzer is stateless across calls: a single instance can be reused
    across batches because configuration (sample rate, hop size) is the only
    state it carries. The expensive aubio object, when present, is created
    per call so concurrent analyzers do not have to share mutable state.
    """

    def __init__(self, sample_rate: int = 44100, hop_size: int = 512) -> None:
        if sample_rate <= 0:
            raise ValueError(f"PitchAnalyzer: sample_rate must be > 0 (got {sample_rate})")
        if hop_size <= 0:
            raise ValueError(f"PitchAnalyzer: hop_size must be > 0 (got {hop_size})")
        self._sample_rate = sample_rate
        self._hop_size = hop_size
        self._aubio = _try_import_aubio()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def hop_size(self) -> int:
        return self._hop_size

    @property
    def has_aubio(self) -> bool:
        return self._aubio is not None

    def detect_pitches(self, samples: np.ndarray) -> list[PitchDetection]:
        """Slice ``samples`` into hops and return one detection per hop."""
        if samples.size == 0:
            return []
        mono = samples.reshape(-1).astype(np.float32, copy=False)
        if self._aubio is not None:
            return self._detect_with_aubio(mono)
        return self._detect_with_autocorr(mono)

    def compute_metrics(
        self,
        detections: list[PitchDetection],
        reference_hz: list[float] | None = None,
        reference_notes: list[str] | None = None,
        ataque_classifier: AtaqueClassifier | None = None,
    ) -> PitchMetrics:
        """Aggregate frame detections into the ``PitchMetrics`` sub-object."""
        ataque: AtaquePredominante = (
            ataque_classifier(detections) if ataque_classifier is not None else "indeterminado"
        )

        if not detections or reference_hz is None or not reference_hz:
            return PitchMetrics(
                notas_corretas_pct=0.0,
                precisao_oitava_pct=0.0,
                desvio_padrao_cents=0.0,
                ataque_predominante=ataque,
                momentos_criticos=[],
            )

        comparisons = self._align_and_compare(detections, reference_hz)
        if not comparisons:
            return PitchMetrics(
                notas_corretas_pct=0.0,
                precisao_oitava_pct=0.0,
                desvio_padrao_cents=0.0,
                ataque_predominante=ataque,
                momentos_criticos=[],
            )

        notas_corretas_pct = (
            100.0
            * sum(1 for c in comparisons if abs(c.cents_error) <= _CENTS_TOLERANCE)
            / len(comparisons)
        )
        precisao_oitava_pct = (
            100.0
            * sum(1 for c in comparisons if abs(c.chroma_error) <= _CENTS_TOLERANCE)
            / len(comparisons)
        )
        desvio_padrao_cents = float(np.std([c.cents_error for c in comparisons]))

        momentos = self._top_momentos(comparisons, reference_notes)

        return PitchMetrics(
            notas_corretas_pct=round(notas_corretas_pct, 4),
            precisao_oitava_pct=round(precisao_oitava_pct, 4),
            desvio_padrao_cents=round(desvio_padrao_cents, 4),
            ataque_predominante=ataque,
            momentos_criticos=momentos,
        )

    def _detect_with_aubio(self, samples: np.ndarray) -> list[PitchDetection]:
        aubio = self._aubio
        assert aubio is not None
        pitch_o = aubio.pitch(  # type: ignore[attr-defined]
            "yin",
            self._hop_size * 4,
            self._hop_size,
            self._sample_rate,
        )
        pitch_o.set_unit("Hz")
        pitch_o.set_tolerance(0.8)

        detections: list[PitchDetection] = []
        ms_per_hop = 1000.0 * self._hop_size / self._sample_rate
        for index, start in enumerate(range(0, samples.size - self._hop_size + 1, self._hop_size)):
            frame = samples[start : start + self._hop_size]
            estimated = float(pitch_o(frame)[0])
            confidence = float(pitch_o.get_confidence())
            if not (_MIN_PITCH_HZ <= estimated <= _MAX_PITCH_HZ):
                estimated = 0.0
                confidence = 0.0
            detections.append(
                PitchDetection(
                    timestamp_ms=round(index * ms_per_hop),
                    pitch_hz=estimated,
                    confianca=confidence,
                )
            )
        return detections

    def _detect_with_autocorr(self, samples: np.ndarray) -> list[PitchDetection]:
        window_size = self._hop_size * 4
        min_lag = max(1, int(self._sample_rate / _MAX_PITCH_HZ))
        max_lag = min(window_size - 1, int(self._sample_rate / _MIN_PITCH_HZ))
        if max_lag <= min_lag:
            return []

        detections: list[PitchDetection] = []
        ms_per_hop = 1000.0 * self._hop_size / self._sample_rate
        for index, start in enumerate(range(0, samples.size - window_size + 1, self._hop_size)):
            frame = samples[start : start + window_size]
            pitch_hz, confidence = self._estimate_frame_autocorr(frame, min_lag, max_lag)
            detections.append(
                PitchDetection(
                    timestamp_ms=round(index * ms_per_hop),
                    pitch_hz=pitch_hz,
                    confianca=confidence,
                )
            )
        return detections

    def _estimate_frame_autocorr(
        self,
        frame: np.ndarray,
        min_lag: int,
        max_lag: int,
    ) -> tuple[float, float]:
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        if rms < 1e-4:
            return 0.0, 0.0

        centered = frame.astype(np.float64) - float(np.mean(frame))
        norm = float(np.dot(centered, centered))
        if norm <= 0.0:
            return 0.0, 0.0

        autocorr = np.correlate(centered, centered, mode="full")
        autocorr = autocorr[autocorr.size // 2 :]
        if autocorr.size <= max_lag:
            return 0.0, 0.0

        window = autocorr[min_lag : max_lag + 1]
        peak_offset = int(np.argmax(window))
        peak_lag = min_lag + peak_offset
        if peak_lag <= 0:
            return 0.0, 0.0

        refined_lag = self._parabolic_refine(autocorr, peak_lag)
        if refined_lag <= 0.0:
            return 0.0, 0.0

        pitch_hz = self._sample_rate / refined_lag
        if not (_MIN_PITCH_HZ <= pitch_hz <= _MAX_PITCH_HZ):
            return 0.0, 0.0

        confidence = max(0.0, min(1.0, float(autocorr[peak_lag] / norm)))
        if confidence < _MIN_CONFIDENCE:
            return 0.0, confidence
        return pitch_hz, confidence

    @staticmethod
    def _parabolic_refine(autocorr: np.ndarray, peak_lag: int) -> float:
        if peak_lag <= 0 or peak_lag >= autocorr.size - 1:
            return float(peak_lag)
        left = float(autocorr[peak_lag - 1])
        center = float(autocorr[peak_lag])
        right = float(autocorr[peak_lag + 1])
        denominator = left - 2.0 * center + right
        if denominator == 0.0:
            return float(peak_lag)
        offset = 0.5 * (left - right) / denominator
        return float(peak_lag) + offset

    @staticmethod
    def _align_and_compare(
        detections: list[PitchDetection],
        reference_hz: list[float],
    ) -> list[_PitchComparison]:
        usable = [d for d in detections if d.pitch_hz > 0.0]
        if not usable or not reference_hz:
            return []

        comparisons: list[_PitchComparison] = []
        ref_len = len(reference_hz)
        det_len = len(usable)
        for det_index, detection in enumerate(usable):
            ref_index = min(ref_len - 1, int(det_index * ref_len / max(1, det_len)))
            target = reference_hz[ref_index]
            if target <= 0.0:
                continue
            cents = _cents_error(detection.pitch_hz, target)
            chroma = _chroma_cents_error(detection.pitch_hz, target)
            if not math.isfinite(cents):
                continue
            comparisons.append(
                _PitchComparison(
                    timestamp_ms=detection.timestamp_ms,
                    detected_hz=detection.pitch_hz,
                    reference_hz=target,
                    reference_index=ref_index,
                    cents_error=cents,
                    chroma_error=chroma,
                )
            )
        return comparisons

    @staticmethod
    def _top_momentos(
        comparisons: list[_PitchComparison],
        reference_notes: list[str] | None,
    ) -> list[MomentoCritico]:
        offending = [c for c in comparisons if abs(c.cents_error) > _CENTS_TOLERANCE]
        scored = sorted(offending, key=lambda c: abs(c.cents_error), reverse=True)
        worst = scored[:_TOP_MOMENTOS]
        momentos: list[MomentoCritico] = []
        for comparison in worst:
            label = _resolve_note_label(reference_notes, comparison.reference_index)
            momentos.append(
                MomentoCritico(
                    timestamp_ms=comparison.timestamp_ms,
                    nota_alvo=label,
                    erro_cents=round(comparison.cents_error),
                )
            )
        momentos.sort(key=lambda m: m.timestamp_ms)
        return momentos


@dataclass(frozen=True)
class _PitchComparison:
    timestamp_ms: int
    detected_hz: float
    reference_hz: float
    reference_index: int
    cents_error: float
    chroma_error: float


def _resolve_note_label(reference_notes: list[str] | None, index: int) -> str:
    if reference_notes is None or not reference_notes:
        return "?"
    safe_index = max(0, min(len(reference_notes) - 1, index))
    return reference_notes[safe_index]


__all__ = [
    "AtaqueClassifier",
    "PitchAnalyzer",
    "PitchDetection",
]
