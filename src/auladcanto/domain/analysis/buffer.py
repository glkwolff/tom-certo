"""30-second batch buffer fed by an :class:`AudioCaptureProtocol`.

The buffer is the boundary between the realtime capture layer (phase 3A) and
the rich analyzers (phase 3B): it accumulates ``np.ndarray`` chunks until 30
seconds of audio are available, then emits a :class:`ClosedBatch` on an
``asyncio.Queue`` for downstream consumers to pick up.

Two design choices keep the hot path cheap:

* The internal sample store is a single ``np.empty`` array allocated once at
  ``run()`` startup. Each chunk lands in a slice of that buffer, so the loop
  never calls ``np.concatenate`` (which would copy O(N) bytes every chunk).
* Pulling from the capture is dispatched to the default thread executor with
  :func:`asyncio.get_running_loop().run_in_executor` so the asyncio event loop
  stays responsive while the capture's ``read_chunk`` blocks on its
  ``queue.get``.

If the microphone goes silent for ``inactivity_timeout_seconds`` the run is
aborted with :class:`SessionTimeoutError`; any buffered samples are still
emitted as a final partial batch before the error propagates so callers can
flush whatever they have. Silence is measured by counting contiguous chunks
whose absolute maximum is below ``silence_threshold``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from auladcanto.domain.analysis.capture import AudioCaptureProtocol

_READ_TIMEOUT_S = 1.0


class BatchBufferError(Exception):
    """Base error for the batch buffer."""


class SessionTimeoutError(BatchBufferError):
    """Raised when no non-silent audio arrives for ``inactivity_timeout_seconds``."""


class CaptureMismatchError(BatchBufferError):
    """Raised when the capture's sample rate disagrees with the buffer's configuration."""


@dataclass(frozen=True)
class ClosedBatch:
    """A 30-second window (or shorter, if the session ended early) of captured audio.

    ``samples`` is the contiguous float32 array consumed by phase 3B analyzers.
    ``total_samples`` is redundant with ``len(samples)`` but kept explicit so
    callers do not have to second-guess numpy shape conventions.
    """

    batch_numero: int
    started_at: datetime
    ended_at: datetime
    samples: np.ndarray
    sample_rate: int
    total_samples: int


def _now_utc() -> datetime:
    return datetime.now(UTC)


class BatchBuffer:
    """Accumulate audio chunks into closed 30s batches.

    The buffer owns the capture's lifecycle (it calls ``start()`` and
    ``stop()``) so callers only need to ``await buffer.run(queue)`` and react
    to the ``ClosedBatch`` instances appearing on ``queue``.
    """

    def __init__(
        self,
        capture: AudioCaptureProtocol,
        batch_duration_seconds: int = 30,
        inactivity_timeout_seconds: int = 600,
        silence_threshold: float = 1e-4,
    ) -> None:
        if batch_duration_seconds <= 0:
            raise ValueError(
                f"BatchBuffer: batch_duration_seconds must be > 0 (got {batch_duration_seconds})"
            )
        if inactivity_timeout_seconds <= 0:
            raise ValueError(
                "BatchBuffer: inactivity_timeout_seconds must be > 0 "
                f"(got {inactivity_timeout_seconds})"
            )
        if silence_threshold < 0:
            raise ValueError(
                f"BatchBuffer: silence_threshold must be >= 0 (got {silence_threshold})"
            )
        self._capture = capture
        self._batch_duration_seconds = batch_duration_seconds
        self._inactivity_timeout_seconds = inactivity_timeout_seconds
        self._silence_threshold = silence_threshold
        self._stop_requested = False
        self._sample_rate = capture.sample_rate
        self._batch_capacity = self._sample_rate * batch_duration_seconds
        self._buffer = np.empty(self._batch_capacity, dtype=np.float32)
        self._cursor = 0
        self._batch_numero = 0
        self._batch_started_at: datetime | None = None
        self._last_active_monotonic: float | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def batch_duration_seconds(self) -> int:
        return self._batch_duration_seconds

    def stop(self) -> None:
        """Request a graceful shutdown after the current iteration."""
        self._stop_requested = True

    async def run(self, output_queue: asyncio.Queue[ClosedBatch]) -> None:
        """Pump chunks from the capture into ``output_queue`` as 30s batches.

        The coroutine returns cleanly when :meth:`stop` is called or when the
        capture reports end-of-stream; it raises :class:`SessionTimeoutError`
        when the microphone has been silent for too long.
        """
        if self._capture.sample_rate != self._sample_rate:
            raise CaptureMismatchError(
                "BatchBuffer: capture sample_rate "
                f"({self._capture.sample_rate}) does not match buffer sample_rate "
                f"({self._sample_rate})"
            )

        loop = asyncio.get_running_loop()
        self._capture.start()
        self._batch_started_at = _now_utc()
        self._last_active_monotonic = time.monotonic()
        try:
            while not self._stop_requested:
                chunk = await loop.run_in_executor(None, self._capture.read_chunk, _READ_TIMEOUT_S)
                if chunk is None:
                    break
                self._update_activity(chunk)
                self._check_inactivity_timeout()
                await self._absorb_chunk(chunk, output_queue)
        finally:
            await self._flush_partial(output_queue)
            self._capture.stop()

    def _update_activity(self, chunk: np.ndarray) -> None:
        if chunk.size == 0:
            return
        peak = float(np.max(np.abs(chunk)))
        if peak >= self._silence_threshold:
            self._last_active_monotonic = time.monotonic()

    def _check_inactivity_timeout(self) -> None:
        if self._last_active_monotonic is None:
            return
        elapsed = time.monotonic() - self._last_active_monotonic
        if elapsed >= self._inactivity_timeout_seconds:
            raise SessionTimeoutError(
                "BatchBuffer: no audio above silence_threshold for "
                f"{elapsed:.1f}s (limit {self._inactivity_timeout_seconds}s)"
            )

    async def _absorb_chunk(
        self,
        chunk: np.ndarray,
        output_queue: asyncio.Queue[ClosedBatch],
    ) -> None:
        view = chunk.reshape(-1) if chunk.ndim > 1 else chunk
        offset = 0
        while offset < view.size:
            remaining_in_batch = self._batch_capacity - self._cursor
            take = min(remaining_in_batch, view.size - offset)
            self._buffer[self._cursor : self._cursor + take] = view[offset : offset + take]
            self._cursor += take
            offset += take
            if self._cursor >= self._batch_capacity:
                await self._close_batch(output_queue)

    async def _close_batch(self, output_queue: asyncio.Queue[ClosedBatch]) -> None:
        assert self._batch_started_at is not None
        samples = self._buffer[: self._cursor].copy()
        batch = ClosedBatch(
            batch_numero=self._batch_numero,
            started_at=self._batch_started_at,
            ended_at=_now_utc(),
            samples=samples,
            sample_rate=self._sample_rate,
            total_samples=int(samples.size),
        )
        await output_queue.put(batch)
        self._batch_numero += 1
        self._cursor = 0
        self._batch_started_at = _now_utc()

    async def _flush_partial(self, output_queue: asyncio.Queue[ClosedBatch]) -> None:
        if self._cursor == 0 or self._batch_started_at is None:
            return
        samples = self._buffer[: self._cursor].copy()
        batch = ClosedBatch(
            batch_numero=self._batch_numero,
            started_at=self._batch_started_at,
            ended_at=_now_utc(),
            samples=samples,
            sample_rate=self._sample_rate,
            total_samples=int(samples.size),
        )
        await output_queue.put(batch)
        self._batch_numero += 1
        self._cursor = 0
        self._batch_started_at = None


__all__ = [
    "BatchBuffer",
    "BatchBufferError",
    "CaptureMismatchError",
    "ClosedBatch",
    "SessionTimeoutError",
]
