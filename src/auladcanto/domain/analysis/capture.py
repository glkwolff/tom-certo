"""Audio capture front-end backed by :mod:`sounddevice` (PortAudio).

The capture layer is intentionally tiny: it owns one ``sounddevice.InputStream``
and forwards every captured chunk into a :class:`queue.Queue` so a separate
worker can pull from it without ever touching PortAudio's realtime callback
thread.

Plan section 3.5 / risk R7 fix the single non-negotiable constraint: the
PortAudio callback must do no Python work beyond ``queue.put_nowait(copy)``.
Anything heavier (formatting, logging, pitch detection) risks holding the GIL
long enough for the PortAudio thread to drop frames. The closure built in
:meth:`SoundDeviceCapture.start` therefore touches exactly one method
(``Queue.put_nowait``) on exactly one object (``indata.copy()``) — no logging,
no exception handling, no shape checks.

``sounddevice`` itself lives in the ``[audio]`` extra because PortAudio
bindings frequently fail to build on minimal Linux containers. The module
imports it lazily at :meth:`SoundDeviceCapture.start` time and raises
:class:`MissingAudioDependencyError` with a helpful install hint when it is
missing — the rest of the package keeps working without it (e.g. tests use
:class:`FakeCapture` instead).
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


class CaptureError(Exception):
    """Base error for the capture layer."""


class MissingAudioDependencyError(CaptureError):
    """Raised when ``sounddevice`` is needed but the ``[audio]`` extra is not installed."""


@dataclass(frozen=True)
class CaptureConfig:
    """Static configuration for an :class:`AudioCaptureProtocol` implementation.

    The defaults match the values stored in
    :class:`auladcanto.domain.perfil_aluno.PreferenciasAluno` so calibration
    results round-trip cleanly into a live capture session.
    """

    sample_rate: int = 44100
    chunk_size: int = 512
    channels: int = 1
    device: int | str | None = None


class AudioCaptureProtocol(Protocol):
    """The narrow contract the rest of the analysis pipeline depends on.

    Implementations may use a real microphone (``SoundDeviceCapture``) or a
    fixture buffer (``FakeCapture``); the buffer/analyzer code must not care
    which one it is talking to.
    """

    @property
    def sample_rate(self) -> int: ...

    @property
    def chunk_size(self) -> int: ...

    @property
    def is_running(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None: ...


def _load_sounddevice() -> ModuleType:
    """Import ``sounddevice`` lazily, raising a friendly error if it is missing."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise MissingAudioDependencyError(
            "sounddevice is required for live audio capture. "
            'Install the audio extra: `pip install -e ".[audio]"`.'
        ) from exc
    return cast("ModuleType", sd)


class SoundDeviceCapture:
    """Live microphone capture via ``sounddevice.InputStream``.

    The instance is single-use in the sense that :meth:`start` and
    :meth:`stop` may each be called at most once per object. The PortAudio
    callback runs on a dedicated realtime thread; pulling chunks from
    :meth:`read_chunk` happens on whatever thread owns the consumer (typically
    an asyncio executor running :class:`BatchBuffer`).
    """

    def __init__(self, config: CaptureConfig) -> None:
        self._config = config
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Any | None = None
        self._running = False

    @property
    def sample_rate(self) -> int:
        return self._config.sample_rate

    @property
    def chunk_size(self) -> int:
        return self._config.chunk_size

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Open the PortAudio stream and begin pushing chunks into the queue."""
        if self._running:
            raise CaptureError("SoundDeviceCapture.start() called while already running")
        sd = _load_sounddevice()

        put_chunk: Callable[[np.ndarray], None] = self._queue.put_nowait

        def _callback(
            indata: np.ndarray,
            _frames: int,
            _time_info: object,
            _status: object,
        ) -> None:
            put_chunk(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self._config.sample_rate,
            blocksize=self._config.chunk_size,
            channels=self._config.channels,
            device=self._config.device,
            dtype="float32",
            callback=_callback,
        )
        self._stream.start()
        self._running = True

    def stop(self) -> None:
        """Stop and close the PortAudio stream. Safe to call multiple times."""
        if not self._running:
            return
        self._running = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.stop()
            stream.close()

    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None:
        """Return the next captured chunk or ``None`` after a clean shutdown.

        If ``timeout`` elapses while the stream is still running the call
        re-raises ``queue.Empty`` as a timeout signal; callers wishing to keep
        polling typically pass a small ``timeout`` and treat ``None`` as
        "stream finished".
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            if not self._running:
                return None
            raise


class FakeCapture:
    """In-memory capture stub used by tests.

    Slices a pre-recorded numpy array into ``chunk_size``-sample pieces and
    yields them on every :meth:`read_chunk` call. Returns ``None`` once the
    buffer is exhausted so consumers can detect end-of-stream the same way
    they would with a closed microphone.
    """

    def __init__(self, samples: np.ndarray, config: CaptureConfig) -> None:
        if samples.ndim != 1:
            raise ValueError(f"FakeCapture: samples must be 1-D (got shape {samples.shape!r})")
        self._samples = samples.astype(np.float32, copy=False)
        self._config = config
        self._cursor = 0
        self._running = False

    @property
    def sample_rate(self) -> int:
        return self._config.sample_rate

    @property
    def chunk_size(self) -> int:
        return self._config.chunk_size

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def read_chunk(self, timeout: float | None = None) -> np.ndarray | None:
        del timeout
        if self._cursor >= self._samples.size:
            self._running = False
            return None
        end = min(self._cursor + self._config.chunk_size, self._samples.size)
        chunk = self._samples[self._cursor : end].copy()
        self._cursor = end
        return chunk


__all__ = [
    "AudioCaptureProtocol",
    "CaptureConfig",
    "CaptureError",
    "FakeCapture",
    "MissingAudioDependencyError",
    "SoundDeviceCapture",
]
