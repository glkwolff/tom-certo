"""Attack classification analyzer (phase 3B).

A "note attack" is the short window of pitch values right after an onset
where the singer either lands directly on the target note or slides into it.
Classical singing pedagogy distinguishes four shapes:

* ``direto`` — voice arrives on pitch within a tolerance (default ±20 cents)
  and stays there. Considered the cleanest attack.
* ``under_shoot`` — voice starts noticeably flat (>tolerance below the target)
  and rises toward the target. Common in untrained singers; the slide is
  audible.
* ``over_shoot`` — mirror image of ``under_shoot``: voice starts sharp and
  drops. Often heard from emotional singing or excessive support.
* ``instavel`` — voice never settles into the tolerance band of the target.

A note is ``indeterminado`` (the ``AtaquePredominante`` literal from
:mod:`auladcanto.domain.batch`) when the window contains too few valid pitch
samples to draw any conclusion. The pitch analyzer in phase 3B-a calls this
classifier as a plain callable (``__call__``), one onset window at a time;
the comparator and report pipeline can call :meth:`classify_events` and
:meth:`predominant` to build the per-batch summary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from auladcanto.domain.batch import AtaquePredominante

_MIN_FRAMES_FOR_CLASSIFICATION = 4
_EDGE_FRAMES = 3


@dataclass(frozen=True)
class AtaqueEvent:
    """Classification of a single note onset, with the inputs that produced it."""

    timestamp_ms: int
    nota_alvo_hz: float
    cents_inicial: float
    cents_final: float
    classificacao: AtaquePredominante


class AtaqueClassifier:
    """Classify the shape of a note attack from a short window of pitch values.

    Given the first ~150 ms of pitch readings after an onset, the classifier
    compares the average of the first ``_EDGE_FRAMES`` frames (``cents_inicial``)
    to the average of the last ``_EDGE_FRAMES`` frames (``cents_final``) versus
    the target note. A window that starts within tolerance and stays there is
    ``direto``; one that starts flat and rises is ``under_shoot``; one that
    starts sharp and drops is ``over_shoot``; anything else is ``instavel``.
    Windows without enough valid (positive) pitch samples produce
    ``indeterminado``.
    """

    def __init__(self, tolerance_cents: float = 20.0) -> None:
        if tolerance_cents <= 0.0:
            raise ValueError(
                f"AtaqueClassifier: tolerance_cents must be > 0 (got {tolerance_cents})"
            )
        self._tolerance_cents = tolerance_cents

    @property
    def tolerance_cents(self) -> float:
        return self._tolerance_cents

    def __call__(
        self,
        pitch_window_hz: list[float],
        nota_alvo_hz: float,
    ) -> AtaquePredominante:
        """Classify a single onset window — the callable shape used by PitchAnalyzer."""
        if nota_alvo_hz <= 0.0:
            return "indeterminado"
        cents_inicial, cents_final, valid = self._edges_in_cents(pitch_window_hz, nota_alvo_hz)
        if not valid:
            return "indeterminado"
        return self._classify_from_edges(cents_inicial, cents_final)

    def classify_events(
        self,
        onsets_with_targets: list[tuple[int, list[float], float]],
    ) -> list[AtaqueEvent]:
        """Bulk classification returning detailed :class:`AtaqueEvent` instances.

        Each input tuple is ``(timestamp_ms, pitch_window, nota_alvo_hz)``.
        Windows that classify as ``indeterminado`` still produce an event so
        callers can audit how many onsets were rejected.
        """
        events: list[AtaqueEvent] = []
        for timestamp_ms, pitch_window, nota_alvo_hz in onsets_with_targets:
            if nota_alvo_hz <= 0.0:
                events.append(
                    AtaqueEvent(
                        timestamp_ms=timestamp_ms,
                        nota_alvo_hz=nota_alvo_hz,
                        cents_inicial=0.0,
                        cents_final=0.0,
                        classificacao="indeterminado",
                    )
                )
                continue
            cents_inicial, cents_final, valid = self._edges_in_cents(pitch_window, nota_alvo_hz)
            classificacao: AtaquePredominante = (
                self._classify_from_edges(cents_inicial, cents_final) if valid else "indeterminado"
            )
            events.append(
                AtaqueEvent(
                    timestamp_ms=timestamp_ms,
                    nota_alvo_hz=nota_alvo_hz,
                    cents_inicial=cents_inicial,
                    cents_final=cents_final,
                    classificacao=classificacao,
                )
            )
        return events

    def predominant(self, events: list[AtaqueEvent]) -> AtaquePredominante:
        """Return the majority classification across ``events``.

        ``indeterminado`` events are excluded from the vote; if no event has a
        confident classification the result is ``indeterminado``. Ties are
        broken by the order in which the classes first appear in ``events``
        (Counter.most_common keeps insertion order for equal counts in
        CPython 3.7+).
        """
        if not events:
            return "indeterminado"
        confident = [
            event.classificacao for event in events if event.classificacao != "indeterminado"
        ]
        if not confident:
            return "indeterminado"
        counts = Counter(confident)
        most_common, _ = counts.most_common(1)[0]
        return most_common

    def _edges_in_cents(
        self,
        pitch_window_hz: list[float],
        nota_alvo_hz: float,
    ) -> tuple[float, float, bool]:
        valid = [float(p) for p in pitch_window_hz if p > 0.0]
        if len(valid) < _MIN_FRAMES_FOR_CLASSIFICATION:
            return 0.0, 0.0, False
        edge = min(_EDGE_FRAMES, len(valid) // 2)
        edge = max(edge, 1)
        cents = 1200.0 * np.log2(np.asarray(valid, dtype=np.float64) / float(nota_alvo_hz))
        cents_inicial = float(np.mean(cents[:edge]))
        cents_final = float(np.mean(cents[-edge:]))
        return cents_inicial, cents_final, True

    def _classify_from_edges(
        self,
        cents_inicial: float,
        cents_final: float,
    ) -> AtaquePredominante:
        tol = self._tolerance_cents
        inicial_dentro = abs(cents_inicial) <= tol
        final_dentro = abs(cents_final) <= tol
        if inicial_dentro and final_dentro:
            return "direto"
        if cents_inicial < -tol and final_dentro:
            return "under_shoot"
        if cents_inicial > tol and final_dentro:
            return "over_shoot"
        return "instavel"


__all__ = [
    "AtaqueClassifier",
    "AtaqueEvent",
]
