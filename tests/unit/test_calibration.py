"""Unit tests for the four-step microphone calibrator (phase 4)."""

from __future__ import annotations

import numpy as np
import pytest

from auladcanto.domain.analysis.capture import CaptureConfig, FakeCapture
from auladcanto.domain.calibration.microfone import (
    CalibracaoMicrofone,
    CalibradorMicrofone,
    CalibrationConfig,
)

_SAMPLE_RATE = 8_000
_CHUNK_SIZE = 400
_SEC = 1  # keep all four passes 1s long so tests stay quick


def _make_config() -> CalibrationConfig:
    return CalibrationConfig(
        silencio_segundos=_SEC,
        fala_segundos=_SEC,
        escala_segundos=_SEC,
        sample_rate=_SAMPLE_RATE,
    )


def _make_capture(samples: np.ndarray) -> FakeCapture:
    return FakeCapture(
        samples,
        CaptureConfig(sample_rate=_SAMPLE_RATE, chunk_size=_CHUNK_SIZE, channels=1),
    )


def _silence(num_samples: int, amplitude: float = 1e-6) -> np.ndarray:
    return np.full(num_samples, amplitude, dtype=np.float32)


def _tone(num_samples: int, amplitude: float = 0.3, freq_hz: float = 220.0) -> np.ndarray:
    t = np.arange(num_samples, dtype=np.float32) / _SAMPLE_RATE
    return (amplitude * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float32)


def _full_calibration_buffer(
    silence_amplitude: float = 1e-6,
    speech_amplitude: float = 0.3,
    scale_amplitude: float = 0.3,
) -> np.ndarray:
    per_pass = _SEC * _SAMPLE_RATE
    return np.concatenate(
        [
            _silence(per_pass, amplitude=silence_amplitude),
            _tone(per_pass, amplitude=speech_amplitude, freq_hz=180.0),
            _tone(per_pass, amplitude=scale_amplitude, freq_hz=440.0),
        ]
    ).astype(np.float32)


async def test_calibrar_silencio_produz_noise_floor_proximo_de_minus_120() -> None:
    capture = _make_capture(_full_calibration_buffer(silence_amplitude=1e-6))
    calibrador = CalibradorMicrofone(capture, _make_config())

    resultado = await calibrador.calibrar()

    assert resultado.noise_floor_db <= -100.0
    assert resultado.noise_floor_db >= -120.0


async def test_calibrar_fala_produz_range_dinamico_acima_de_50_db() -> None:
    capture = _make_capture(_full_calibration_buffer(speech_amplitude=0.5))
    calibrador = CalibradorMicrofone(capture, _make_config())

    resultado = await calibrador.calibrar()

    assert resultado.range_dinamico_db > 50.0


async def test_calibrar_sem_pitch_detector_retorna_zero_com_detalhe_placeholder() -> None:
    capture = _make_capture(_full_calibration_buffer())
    calibrador = CalibradorMicrofone(capture, _make_config(), pitch_detector=None)

    resultado = await calibrador.calibrar()

    assert resultado.pitch_detection_acuracia_pct == 0.0


async def test_on_progress_callback_invocado_para_cada_passo() -> None:
    capture = _make_capture(_full_calibration_buffer())
    calibrador = CalibradorMicrofone(capture, _make_config())

    eventos: list[tuple[str, int]] = []

    def _on_progress(passo: str, segundos_restantes: int) -> None:
        eventos.append((passo, segundos_restantes))

    await calibrador.calibrar(on_progress=_on_progress)

    passos = [evento[0] for evento in eventos]
    assert "silencio" in passos
    assert "fala" in passos
    assert "escala" in passos
    assert "latencia" in passos
    assert ("silencio", _SEC) in eventos
    assert ("fala", _SEC) in eventos
    assert ("escala", _SEC) in eventos


def test_rms_db_retorna_floor_para_silencio_absoluto() -> None:
    zeros = np.zeros(1024, dtype=np.float32)

    assert CalibradorMicrofone._rms_db(zeros) == pytest.approx(-120.0)
    assert CalibradorMicrofone._rms_db(np.zeros(0, dtype=np.float32)) == pytest.approx(-120.0)


async def test_resultado_valida_via_pydantic_com_campos_no_range_esperado() -> None:
    capture = _make_capture(_full_calibration_buffer())
    calibrador = CalibradorMicrofone(capture, _make_config())

    resultado = await calibrador.calibrar()

    assert isinstance(resultado, CalibracaoMicrofone)
    assert resultado.range_dinamico_db >= 0.0
    assert 0.0 <= resultado.pitch_detection_acuracia_pct <= 100.0
    assert resultado.latencia_aproximada_ms >= 0
    assert resultado.data_calibracao.tzinfo is not None

    revalidated = CalibracaoMicrofone.model_validate(resultado.model_dump())
    assert revalidated == resultado


async def test_pitch_detector_injetado_retorna_acuracia_e_eh_clampeada() -> None:
    capture = _make_capture(_full_calibration_buffer())

    def _fake_detector(samples: np.ndarray, sample_rate: int) -> float:
        del samples, sample_rate
        return 137.5

    calibrador = CalibradorMicrofone(capture, _make_config(), pitch_detector=_fake_detector)
    resultado = await calibrador.calibrar()

    assert resultado.pitch_detection_acuracia_pct == 100.0


async def test_on_progress_assincrono_eh_aguardado() -> None:
    capture = _make_capture(_full_calibration_buffer())
    calibrador = CalibradorMicrofone(capture, _make_config())

    eventos: list[str] = []

    async def _on_progress(passo: str, segundos_restantes: int) -> None:
        del segundos_restantes
        eventos.append(passo)

    await calibrador.calibrar(on_progress=_on_progress)

    assert eventos == ["silencio", "fala", "escala", "latencia"]
