"""Unit tests for the audio capture + 30s batch buffer (phase 3A)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import numpy as np
import pytest

from auladcanto.domain.analysis.buffer import (
    BatchBuffer,
    CaptureMismatchError,
    ClosedBatch,
    SessionTimeoutError,
)
from auladcanto.domain.analysis.capture import (
    CaptureConfig,
    FakeCapture,
    MissingAudioDependencyError,
    SoundDeviceCapture,
)

_SAMPLE_RATE = 8_000
_CHUNK_SIZE = 400
_BATCH_DURATION_S = 1
_BATCH_CAPACITY = _SAMPLE_RATE * _BATCH_DURATION_S


def _make_tone(num_samples: int, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(num_samples, dtype=np.float32) / _SAMPLE_RATE
    return (amplitude * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)


def _make_config(chunk_size: int = _CHUNK_SIZE) -> CaptureConfig:
    return CaptureConfig(sample_rate=_SAMPLE_RATE, chunk_size=chunk_size, channels=1)


async def _drain_queue(queue: asyncio.Queue[ClosedBatch]) -> list[ClosedBatch]:
    batches: list[ClosedBatch] = []
    while not queue.empty():
        batches.append(queue.get_nowait())
    return batches


def test_fake_capture_slices_into_chunks_of_expected_size() -> None:
    samples = _make_tone(_CHUNK_SIZE * 3 + 17)
    capture = FakeCapture(samples, _make_config())
    capture.start()

    chunk1 = capture.read_chunk()
    chunk2 = capture.read_chunk()
    chunk3 = capture.read_chunk()
    chunk4 = capture.read_chunk()
    chunk5 = capture.read_chunk()

    assert chunk1 is not None and chunk1.size == _CHUNK_SIZE
    assert chunk2 is not None and chunk2.size == _CHUNK_SIZE
    assert chunk3 is not None and chunk3.size == _CHUNK_SIZE
    assert chunk4 is not None and chunk4.size == 17  # remainder
    assert chunk5 is None  # exhausted
    assert capture.is_running is False


def test_fake_capture_rejects_non_1d_input() -> None:
    with pytest.raises(ValueError, match="must be 1-D"):
        FakeCapture(np.zeros((2, 100), dtype=np.float32), _make_config())


async def test_batch_buffer_emits_first_batch_at_exact_capacity() -> None:
    samples = _make_tone(_BATCH_CAPACITY)
    capture = FakeCapture(samples, _make_config())
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=3600,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    await buffer.run(queue)

    batches = await _drain_queue(queue)
    assert len(batches) == 1
    assert batches[0].total_samples == _BATCH_CAPACITY
    assert batches[0].sample_rate == _SAMPLE_RATE
    assert batches[0].batch_numero == 0
    np.testing.assert_array_equal(batches[0].samples, samples)


async def test_batch_buffer_emits_three_full_batches_for_three_durations() -> None:
    samples = _make_tone(_BATCH_CAPACITY * 3)
    capture = FakeCapture(samples, _make_config())
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=3600,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    await buffer.run(queue)

    batches = await _drain_queue(queue)
    assert [b.batch_numero for b in batches] == [0, 1, 2]
    assert all(b.total_samples == _BATCH_CAPACITY for b in batches)
    np.testing.assert_array_equal(
        np.concatenate([b.samples for b in batches]),
        samples,
    )


async def test_batch_buffer_inactivity_timeout_raises_after_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silent_samples = np.full(_CHUNK_SIZE * 10, 1e-6, dtype=np.float32)
    capture = FakeCapture(silent_samples, _make_config())
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=2,
        silence_threshold=1e-3,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    clock_state = {"t": 0.0}

    def _fake_monotonic() -> float:
        t = clock_state["t"]
        clock_state["t"] = t + 0.5
        return t

    monkeypatch.setattr(
        "auladcanto.domain.analysis.buffer.time.monotonic",
        _fake_monotonic,
    )

    with pytest.raises(SessionTimeoutError, match="silence_threshold"):
        await buffer.run(queue)


async def test_batch_buffer_stop_mid_batch_emits_partial_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = _make_tone(_CHUNK_SIZE * 3)  # smaller than _BATCH_CAPACITY
    capture = FakeCapture(samples, _make_config())
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=3600,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    fixed_dt = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "auladcanto.domain.analysis.buffer._now_utc",
        lambda: fixed_dt,
    )

    await buffer.run(queue)

    batches = await _drain_queue(queue)
    assert len(batches) == 1
    partial = batches[0]
    assert partial.total_samples == _CHUNK_SIZE * 3
    assert partial.started_at == fixed_dt
    assert partial.ended_at == fixed_dt
    np.testing.assert_array_equal(partial.samples, samples)


async def test_batch_buffer_handles_partial_chunk_at_end_of_input() -> None:
    odd_remainder = 137
    samples = _make_tone(_BATCH_CAPACITY + odd_remainder)
    capture = FakeCapture(samples, _make_config())
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=3600,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    await buffer.run(queue)

    batches = await _drain_queue(queue)
    assert [b.total_samples for b in batches] == [_BATCH_CAPACITY, odd_remainder]
    np.testing.assert_array_equal(
        np.concatenate([b.samples for b in batches]),
        samples,
    )


async def test_batch_buffer_rejects_capture_sample_rate_mismatch() -> None:
    capture = FakeCapture(_make_tone(100), _make_config())
    buffer = BatchBuffer.__new__(BatchBuffer)
    BatchBuffer.__init__(buffer, capture, batch_duration_seconds=1)
    buffer._sample_rate = 22_050  # type: ignore[attr-defined]
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    with pytest.raises(CaptureMismatchError, match="sample_rate"):
        await buffer.run(queue)


def test_batch_buffer_rejects_non_positive_durations() -> None:
    capture = FakeCapture(_make_tone(100), _make_config())
    with pytest.raises(ValueError, match="batch_duration_seconds"):
        BatchBuffer(capture, batch_duration_seconds=0)
    with pytest.raises(ValueError, match="inactivity_timeout_seconds"):
        BatchBuffer(capture, inactivity_timeout_seconds=0)
    with pytest.raises(ValueError, match="silence_threshold"):
        BatchBuffer(capture, silence_threshold=-0.1)


async def test_batch_buffer_external_stop_drains_pending_samples() -> None:
    samples = _make_tone(_CHUNK_SIZE * 5)
    capture = FakeCapture(samples, _make_config())
    buffer = BatchBuffer(
        capture,
        batch_duration_seconds=_BATCH_DURATION_S,
        inactivity_timeout_seconds=3600,
    )
    queue: asyncio.Queue[ClosedBatch] = asyncio.Queue()

    original_read = capture.read_chunk
    chunks_seen = {"count": 0}

    def _instrumented_read(timeout: float | None = None) -> np.ndarray | None:
        chunks_seen["count"] += 1
        result = original_read(timeout)
        if chunks_seen["count"] >= 2:
            buffer.stop()
        return result

    capture.read_chunk = _instrumented_read  # type: ignore[method-assign]

    await buffer.run(queue)

    batches = await _drain_queue(queue)
    assert len(batches) == 1
    assert batches[0].total_samples > 0
    assert batches[0].total_samples <= _CHUNK_SIZE * 2


def test_sound_device_capture_raises_friendly_error_when_module_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def _raise_for_sounddevice(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "sounddevice":
            raise ImportError("simulated missing sounddevice")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raise_for_sounddevice)

    capture = SoundDeviceCapture(_make_config())
    with pytest.raises(MissingAudioDependencyError, match="sounddevice is required"):
        capture.start()


@pytest.fixture
def fake_sounddevice_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Inject a stand-in ``sounddevice`` module so we can exercise the start/stop wiring."""
    import sys
    import types

    class _FakeStream:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.closed = False
            self.callback = kwargs.get("callback")

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    module = types.ModuleType("sounddevice")
    streams: list[_FakeStream] = []

    def _input_stream(**kwargs: object) -> _FakeStream:
        s = _FakeStream(**kwargs)
        streams.append(s)
        return s

    module.InputStream = _input_stream  # type: ignore[attr-defined]
    module._created_streams = streams  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "sounddevice", module)
    yield module


def test_sound_device_capture_callback_only_copies_into_queue(
    fake_sounddevice_module: object,
) -> None:
    capture = SoundDeviceCapture(_make_config())
    capture.start()
    assert capture.is_running is True

    stream = fake_sounddevice_module._created_streams[-1]  # type: ignore[attr-defined]
    chunk = np.linspace(0.0, 1.0, _CHUNK_SIZE, dtype=np.float32)
    stream.callback(chunk, _CHUNK_SIZE, None, None)
    stream.callback(chunk * 2, _CHUNK_SIZE, None, None)

    received = capture.read_chunk(timeout=0.5)
    assert received is not None
    np.testing.assert_array_equal(received, chunk)
    assert received is not chunk  # callback called .copy()

    received2 = capture.read_chunk(timeout=0.5)
    assert received2 is not None
    np.testing.assert_array_equal(received2, chunk * 2)

    capture.stop()
    assert stream.stopped is True
    assert stream.closed is True
    assert capture.is_running is False


def test_sound_device_capture_read_chunk_returns_none_after_stop(
    fake_sounddevice_module: object,
) -> None:
    del fake_sounddevice_module
    import queue as queue_module

    capture = SoundDeviceCapture(_make_config())
    capture.start()
    capture.stop()
    assert capture.read_chunk(timeout=0.01) is None

    capture2 = SoundDeviceCapture(_make_config())
    capture2.start()
    with pytest.raises(queue_module.Empty):
        capture2.read_chunk(timeout=0.01)
    capture2.stop()
