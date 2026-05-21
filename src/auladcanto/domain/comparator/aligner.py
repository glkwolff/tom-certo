"""Temporal alignment of user pitch contour against a :class:`Gabarito`.

The aligner is the bridge between the comparator and the rest of the analysis
stack. It receives the user's raw frame-by-frame pitch detections (each tagged
with a millisecond timestamp relative to the start of the current 30s batch)
plus the canonical reference (a :class:`Gabarito` from phase 1), and returns
one :class:`AlinhamentoTrecho` per ``Trecho`` that intersects the batch.

Each :class:`AlinhamentoTrecho` carries the *reference* series clipped to the
intersection (``ref_times`` + ``ref_freqs``) and the *user* series resampled
onto that same time grid (``user_times`` + ``user_freqs``). The two arrays
have the same length, which makes the downstream
:class:`auladcanto.domain.comparator.score.Scorer` a straight vectorised loop.

Two paths cover resampling:

* When ``mir_eval`` is installed (``[audio]`` extra),
  ``mir_eval.melody.resample_melody_series`` is used because it already encodes
  the standard MIREX voicing convention (frequencies of ``0`` mean unvoiced).
* Otherwise a tiny numpy ``np.interp`` fallback is used. It mirrors the same
  convention by carrying the unvoiced mask separately.

The ``use_dtw`` flag enables a dynamic-time-warping pass over the (cents,
voicing) representation before resampling. DTW is useful when the user sings
the same melody but at a noticeably different tempo than the gabarito; the
warping path stretches/compresses the user's time axis so a slow take is
compared against the right reference frames. The implementation is pure-numpy
``O(n²)`` because the per-trecho sequences are short (≤ a few hundred frames).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from auladcanto.domain.batch import VozEscolhida
from auladcanto.domain.gabarito import (
    Gabarito,
    NotaSeries,
    Trecho,
    TrechoDuo,
    TrechoSolo,
    TrechoUnissono,
)

_MIN_FRAMES_FOR_DTW = 2


def _try_import_mir_eval() -> object | None:
    """Import ``mir_eval`` lazily so the aligner works without the audio extra."""
    try:
        import mir_eval
    except ImportError:
        return None
    return mir_eval  # type: ignore[no-any-return]


@dataclass(frozen=True)
class AlinhamentoTrecho:
    """User vs reference series aligned on a single trecho's time grid.

    ``ref_times`` and ``ref_freqs`` are the reference contour clipped to the
    intersection of the trecho with the current batch. ``user_times`` is
    identical to ``ref_times`` (the user series has been resampled onto the
    reference grid) and ``user_freqs`` holds the resampled user frequencies in
    Hz; a value of ``0.0`` signals an unvoiced frame on either side, matching
    the convention used by ``mir_eval.melody``.

    ``voz_usada`` records which reference voice was selected from the trecho —
    relevant for ``TrechoDuo`` where the student picks the aguda or grave
    voice and the other one is exposed only as context.
    """

    trecho: Trecho
    ref_times: np.ndarray
    ref_freqs: np.ndarray
    user_times: np.ndarray
    user_freqs: np.ndarray
    voz_usada: VozEscolhida


class Aligner:
    """Align user pitch detections to the trechos of a :class:`Gabarito`.

    The aligner is stateless across calls; one instance can be reused for an
    entire session because configuration (DTW on/off) is the only state it
    carries. The ``mir_eval`` library is loaded once on construction so the
    hot path does not pay the import cost again per batch.
    """

    def __init__(self, use_dtw: bool = False) -> None:
        self._use_dtw = use_dtw
        self._mir_eval = _try_import_mir_eval()

    @property
    def use_dtw(self) -> bool:
        return self._use_dtw

    @property
    def has_mir_eval(self) -> bool:
        return self._mir_eval is not None

    def selecionar_trecho_para_timestamp(
        self,
        gabarito: Gabarito,
        t_s: float,
    ) -> Trecho | None:
        """Return the trecho whose ``[inicio_s, fim_s]`` contains ``t_s``.

        The interval is closed on the left and open on the right so the
        boundary between two adjacent trechos belongs to the *next* trecho —
        consistent with the non-overlap rule enforced by
        :class:`Gabarito`'s validator.
        """
        for trecho in gabarito.trechos:
            if trecho.inicio_s <= t_s < trecho.fim_s:
                return trecho
        return None

    def alinhar_batch(
        self,
        gabarito: Gabarito,
        batch_start_s: float,
        batch_duration_s: float,
        user_pitches_hz: list[float],
        user_timestamps_ms: list[int],
        voz_escolhida: VozEscolhida = "n/a",
    ) -> list[AlinhamentoTrecho]:
        """Produce one :class:`AlinhamentoTrecho` per intersecting trecho.

        ``user_pitches_hz`` and ``user_timestamps_ms`` must have the same
        length; timestamps are milliseconds from the start of the batch. The
        intersection between the batch window
        ``[batch_start_s, batch_start_s + batch_duration_s]`` and each trecho
        defines the time range used for both the reference clipping and the
        user resample.
        """
        if len(user_pitches_hz) != len(user_timestamps_ms):
            raise ValueError(
                "Aligner: user_pitches_hz and user_timestamps_ms must have the same length "
                f"(got {len(user_pitches_hz)} and {len(user_timestamps_ms)})"
            )
        if batch_duration_s <= 0.0:
            raise ValueError(f"Aligner: batch_duration_s must be > 0 (got {batch_duration_s})")

        batch_end_s = batch_start_s + batch_duration_s
        user_times_abs = np.asarray(user_timestamps_ms, dtype=np.float64) / 1000.0 + batch_start_s
        user_freqs_arr = np.asarray(user_pitches_hz, dtype=np.float64)

        alinhamentos: list[AlinhamentoTrecho] = []
        for trecho in gabarito.trechos:
            if trecho.fim_s <= batch_start_s or trecho.inicio_s >= batch_end_s:
                continue
            alinhamentos.append(
                self._align_trecho(
                    trecho=trecho,
                    batch_start_s=batch_start_s,
                    batch_end_s=batch_end_s,
                    user_times_abs=user_times_abs,
                    user_freqs=user_freqs_arr,
                    voz_escolhida=voz_escolhida,
                )
            )
        return alinhamentos

    def _align_trecho(
        self,
        trecho: Trecho,
        batch_start_s: float,
        batch_end_s: float,
        user_times_abs: np.ndarray,
        user_freqs: np.ndarray,
        voz_escolhida: VozEscolhida,
    ) -> AlinhamentoTrecho:
        ref_series, voz_usada = self._select_voz(trecho, voz_escolhida)
        intersection_start = max(trecho.inicio_s, batch_start_s)
        intersection_end = min(trecho.fim_s, batch_end_s)

        ref_times_full = np.asarray(ref_series.tempos_s, dtype=np.float64)
        ref_freqs_full = np.asarray(ref_series.pitches_hz, dtype=np.float64)
        ref_mask = (ref_times_full >= intersection_start) & (ref_times_full <= intersection_end)
        ref_times = ref_times_full[ref_mask]
        ref_freqs = ref_freqs_full[ref_mask]

        user_mask = (user_times_abs >= intersection_start) & (user_times_abs <= intersection_end)
        user_segment_times = user_times_abs[user_mask]
        user_segment_freqs = user_freqs[user_mask]

        if ref_times.size == 0:
            empty = np.zeros(0, dtype=np.float64)
            return AlinhamentoTrecho(
                trecho=trecho,
                ref_times=empty,
                ref_freqs=empty,
                user_times=empty,
                user_freqs=empty,
                voz_usada=voz_usada,
            )

        if self._use_dtw and user_segment_times.size >= _MIN_FRAMES_FOR_DTW:
            user_segment_times = self._warp_via_dtw(
                ref_times=ref_times,
                ref_freqs=ref_freqs,
                user_times=user_segment_times,
                user_freqs=user_segment_freqs,
            )

        user_resampled = self._resample_to_ref_grid(
            ref_times=ref_times,
            user_times=user_segment_times,
            user_freqs=user_segment_freqs,
        )

        return AlinhamentoTrecho(
            trecho=trecho,
            ref_times=ref_times,
            ref_freqs=ref_freqs,
            user_times=ref_times.copy(),
            user_freqs=user_resampled,
            voz_usada=voz_usada,
        )

    @staticmethod
    def _select_voz(
        trecho: Trecho,
        voz_escolhida: VozEscolhida,
    ) -> tuple[NotaSeries, VozEscolhida]:
        if isinstance(trecho, TrechoSolo):
            return trecho.voz, "solo"
        if isinstance(trecho, TrechoUnissono):
            return trecho.voz, "solo"
        if isinstance(trecho, TrechoDuo):
            if voz_escolhida == "aguda":
                return trecho.voz_aguda, "aguda"
            if voz_escolhida == "grave":
                return trecho.voz_grave, "grave"
            return trecho.voz_grave, "grave"
        raise TypeError(f"Aligner: unsupported trecho type {type(trecho).__name__}")

    def _resample_to_ref_grid(
        self,
        ref_times: np.ndarray,
        user_times: np.ndarray,
        user_freqs: np.ndarray,
    ) -> np.ndarray:
        if user_times.size == 0:
            return np.zeros(ref_times.shape, dtype=np.float64)

        if self._mir_eval is not None:
            return self._resample_with_mir_eval(ref_times, user_times, user_freqs)

        return self._resample_with_numpy(ref_times, user_times, user_freqs)

    def _resample_with_mir_eval(
        self,
        ref_times: np.ndarray,
        user_times: np.ndarray,
        user_freqs: np.ndarray,
    ) -> np.ndarray:
        mir_eval = self._mir_eval
        assert mir_eval is not None
        voicing = (user_freqs > 0.0).astype(np.float64)
        resampled_freqs, _ = mir_eval.melody.resample_melody_series(  # type: ignore[attr-defined]
            times=user_times,
            frequencies=user_freqs,
            voicing=voicing,
            times_new=ref_times,
            kind="linear",
        )
        return np.asarray(resampled_freqs, dtype=np.float64)

    @staticmethod
    def _resample_with_numpy(
        ref_times: np.ndarray,
        user_times: np.ndarray,
        user_freqs: np.ndarray,
    ) -> np.ndarray:
        voicing = user_freqs > 0.0
        if not bool(np.any(voicing)):
            return np.zeros(ref_times.shape, dtype=np.float64)

        freq_interp = np.interp(
            ref_times,
            user_times,
            user_freqs,
            left=0.0,
            right=0.0,
        )
        voicing_interp = np.interp(
            ref_times,
            user_times,
            voicing.astype(np.float64),
            left=0.0,
            right=0.0,
        )
        freq_interp = np.where(voicing_interp >= 0.5, freq_interp, 0.0)
        return freq_interp.astype(np.float64)

    def _warp_via_dtw(
        self,
        ref_times: np.ndarray,
        ref_freqs: np.ndarray,
        user_times: np.ndarray,
        user_freqs: np.ndarray,
    ) -> np.ndarray:
        ref_cents = _hz_to_cents(ref_freqs)
        user_cents = _hz_to_cents(user_freqs)
        path = _dtw_path(user_cents, ref_cents)
        if path.size == 0:
            return user_times

        warped = np.empty(user_times.shape, dtype=np.float64)
        last_ref_time = ref_times[-1] if ref_times.size > 0 else user_times[-1]
        first_ref_time = ref_times[0] if ref_times.size > 0 else user_times[0]
        ref_for_user = np.full(user_times.shape, np.nan, dtype=np.float64)
        for user_idx, ref_idx in path:
            if (
                0 <= user_idx < user_times.size
                and 0 <= ref_idx < ref_times.size
                and np.isnan(ref_for_user[user_idx])
            ):
                ref_for_user[user_idx] = ref_times[ref_idx]

        previous = first_ref_time
        for index in range(user_times.size):
            value = ref_for_user[index]
            if np.isnan(value):
                warped[index] = previous
            else:
                warped[index] = value
                previous = value
        warped = np.clip(warped, first_ref_time, last_ref_time)
        return warped


def _hz_to_cents(freqs: np.ndarray) -> np.ndarray:
    """Convert a Hz contour to cents relative to ``C0`` (16.3516 Hz).

    Frames at ``0`` Hz (unvoiced) are mapped to ``0`` cents so they line up
    with the rest of the contour without introducing ``-inf``; the DTW cost
    is computed on absolute differences so the unvoiced bias washes out as
    long as both sequences share the convention.
    """
    safe = np.where(freqs > 0.0, freqs, np.nan)
    cents = 1200.0 * np.log2(safe / 16.3516)
    return np.where(np.isnan(cents), 0.0, cents)


def _dtw_path(seq_a: np.ndarray, seq_b: np.ndarray) -> np.ndarray:
    """Compute a DTW alignment path between ``seq_a`` and ``seq_b``.

    Returns an ``(L, 2)`` array of ``(index_in_a, index_in_b)`` pairs. The
    cost is the absolute difference and the recurrence allows the three
    canonical moves (match, insertion, deletion). The implementation is
    intentionally small — ``O(len(a) * len(b))`` time and memory — because
    per-trecho sequences cap out at a few hundred frames in practice.
    """
    n = int(seq_a.size)
    m = int(seq_b.size)
    if n == 0 or m == 0:
        return np.zeros((0, 2), dtype=np.int64)

    cost = np.abs(seq_a[:, None] - seq_b[None, :])
    cumulative = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    cumulative[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cumulative[i, j] = cost[i - 1, j - 1] + min(
                cumulative[i - 1, j - 1],
                cumulative[i - 1, j],
                cumulative[i, j - 1],
            )

    path: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        diag = cumulative[i - 1, j - 1]
        up = cumulative[i - 1, j]
        left = cumulative[i, j - 1]
        best = min(diag, up, left)
        if best == diag:
            i -= 1
            j -= 1
        elif best == up:
            i -= 1
        else:
            j -= 1
    path.reverse()
    return np.asarray(path, dtype=np.int64)


__all__ = [
    "Aligner",
    "AlinhamentoTrecho",
]
