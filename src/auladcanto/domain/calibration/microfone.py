"""Four-step microphone calibration that produces a :class:`CalibracaoMicrofone`.

The calibrator pulls audio from any :class:`AudioCaptureProtocol` implementation
so the same pipeline works in production (``SoundDeviceCapture``) and in tests
(``FakeCapture``). It runs four passes back-to-back over a single capture
session:

* **Latência** — time from commanding ``start()`` to the first chunk arriving.
* **Silêncio** — RMS of the room tone, expressed in dBFS (floor at -120 dB).
* **Fala** — peak RMS during speech, used to derive the dynamic range
  (``range_dinamico_db = peak_db - noise_floor_db``).
* **Escala** — pitch-detection accuracy on a sung scale. Phase 4 ships with a
  placeholder: when no ``pitch_detector`` is injected it reports 0.0 with a
  human-readable note. Phase 3B will wire in a real detector later.

The latency pass intentionally double-counts: the first chunk it reads becomes
the first chunk of the silence collection, so no captured audio is wasted.

An optional ``on_progress`` callback fires once per pass with the step name and
the seconds remaining when the pass starts, giving CLI/MCP frontends enough
information to render a countdown UI.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from auladcanto.domain.analysis.capture import AudioCaptureProtocol
from auladcanto.domain.perfil_aluno import CalibracaoMicrofone

_NOISE_FLOOR_DB_MIN = -120.0
_RMS_EPSILON = 10.0 ** (_NOISE_FLOOR_DB_MIN / 20.0)

PitchDetector = Callable[[np.ndarray, int], float]
ProgressSyncCallback = Callable[[str, int], None]
ProgressAsyncCallback = Callable[[str, int], Awaitable[None]]
ProgressCallback = ProgressSyncCallback | ProgressAsyncCallback


@dataclass(frozen=True)
class CalibrationConfig:
    """Tunable durations and thresholds for the four calibration passes."""

    silencio_segundos: int = 5
    fala_segundos: int = 5
    escala_segundos: int = 5
    sample_rate: int = 44100
    silence_threshold: float = 1e-4


@dataclass(frozen=True)
class PassoResultado:
    """Result of a single calibration pass (intermediate value, not persisted)."""

    passo: str
    valor: float
    detalhes: str = ""


class CalibradorMicrofone:
    """Run the four-step calibration and produce a :class:`CalibracaoMicrofone`."""

    def __init__(
        self,
        capture: AudioCaptureProtocol,
        config: CalibrationConfig | None = None,
        pitch_detector: PitchDetector | None = None,
    ) -> None:
        self._capture = capture
        self._config = config or CalibrationConfig()
        self._pitch_detector = pitch_detector

    async def calibrar(
        self,
        on_progress: ProgressCallback | None = None,
    ) -> CalibracaoMicrofone:
        """Execute all four passes sequentially over one capture session.

        ``on_progress`` is invoked once at the start of every pass with
        ``(passo_name, segundos_restantes)``. The callback may be sync or
        async; both are awaited safely.
        """
        sample_rate = self._capture.sample_rate

        t_start_command = time.monotonic()
        self._capture.start()
        try:
            first_chunk = await self._read_first_chunk()
            t_first_chunk = time.monotonic()
            latencia_ms = max(0, round((t_first_chunk - t_start_command) * 1000.0))

            await self._emit_progress(on_progress, "silencio", self._config.silencio_segundos)
            silencio_samples = await self._coletar_samples(
                self._config.silencio_segundos,
                sample_rate,
                seed_chunk=first_chunk,
            )
            noise_floor_db = self._rms_db(silencio_samples)

            await self._emit_progress(on_progress, "fala", self._config.fala_segundos)
            fala_samples = await self._coletar_samples(
                self._config.fala_segundos,
                sample_rate,
            )
            fala_db = self._rms_db(fala_samples)
            range_dinamico_db = max(0.0, fala_db - noise_floor_db)

            await self._emit_progress(on_progress, "escala", self._config.escala_segundos)
            escala_samples = await self._coletar_samples(
                self._config.escala_segundos,
                sample_rate,
            )
            pitch_passo = self._avaliar_pitch(escala_samples, sample_rate)

            await self._emit_progress(on_progress, "latencia", 0)
        finally:
            self._capture.stop()

        return CalibracaoMicrofone(
            noise_floor_db=noise_floor_db,
            range_dinamico_db=range_dinamico_db,
            pitch_detection_acuracia_pct=pitch_passo.valor,
            latencia_aproximada_ms=latencia_ms,
            data_calibracao=datetime.now(UTC),
        )

    async def _read_first_chunk(self) -> np.ndarray | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._capture.read_chunk, 5.0)

    async def _coletar_samples(
        self,
        duracao_s: int,
        sample_rate: int,
        seed_chunk: np.ndarray | None = None,
    ) -> np.ndarray:
        total_alvo = duracao_s * sample_rate
        partes: list[np.ndarray] = []
        coletados = 0
        if seed_chunk is not None and seed_chunk.size > 0:
            partes.append(seed_chunk.astype(np.float32, copy=False).reshape(-1))
            coletados += int(seed_chunk.size)

        loop = asyncio.get_running_loop()
        while coletados < total_alvo:
            chunk = await loop.run_in_executor(None, self._capture.read_chunk, 5.0)
            if chunk is None:
                break
            view = chunk.astype(np.float32, copy=False).reshape(-1)
            partes.append(view)
            coletados += int(view.size)

        if not partes:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(partes)[:total_alvo]

    def _avaliar_pitch(self, samples: np.ndarray, sample_rate: int) -> PassoResultado:
        if self._pitch_detector is None:
            return PassoResultado(
                passo="escala",
                valor=0.0,
                detalhes="pitch detector not available (placeholder)",
            )
        acuracia = float(self._pitch_detector(samples, sample_rate))
        acuracia = max(0.0, min(100.0, acuracia))
        return PassoResultado(passo="escala", valor=acuracia, detalhes="")

    @staticmethod
    def _rms_db(samples: np.ndarray) -> float:
        if samples.size == 0:
            return _NOISE_FLOOR_DB_MIN
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
        if rms < _RMS_EPSILON:
            return _NOISE_FLOOR_DB_MIN
        return float(20.0 * np.log10(rms))

    @staticmethod
    async def _emit_progress(
        on_progress: ProgressCallback | None,
        passo: str,
        segundos_restantes: int,
    ) -> None:
        if on_progress is None:
            return
        result = on_progress(passo, segundos_restantes)
        if asyncio.iscoroutine(result):
            await result


__all__ = [
    "CalibracaoMicrofone",
    "CalibradorMicrofone",
    "CalibrationConfig",
    "PassoResultado",
    "PitchDetector",
    "ProgressCallback",
]
