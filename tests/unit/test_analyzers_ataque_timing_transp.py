"""Unit tests for the ataque / timing / transposicao analyzers (phase 3B/b)."""

from __future__ import annotations

import numpy as np
import pytest

from auladcanto.domain.analysis.ataque import AtaqueClassifier, AtaqueEvent
from auladcanto.domain.analysis.timing import TimingAnalyzer
from auladcanto.domain.analysis.transposicao import TransposicaoDetector
from auladcanto.domain.batch import TimingMetrics, TransposicaoDetectada

# ---------------------------------------------------------------------------
# AtaqueClassifier
# ---------------------------------------------------------------------------


def test_ataque_classifier_direto_quando_pitch_estavel_no_alvo() -> None:
    classifier = AtaqueClassifier()
    window = [440.0] * 15

    assert classifier(window, nota_alvo_hz=440.0) == "direto"


def test_ataque_classifier_under_shoot_quando_pitch_sobe_de_baixo_do_alvo() -> None:
    classifier = AtaqueClassifier()
    window = list(np.linspace(420.0, 440.0, num=15, dtype=np.float64))

    assert classifier(window, nota_alvo_hz=440.0) == "under_shoot"


def test_ataque_classifier_over_shoot_quando_pitch_desce_de_cima_do_alvo() -> None:
    classifier = AtaqueClassifier()
    window = list(np.linspace(460.0, 440.0, num=15, dtype=np.float64))

    assert classifier(window, nota_alvo_hz=440.0) == "over_shoot"


def test_ataque_classifier_instavel_quando_pitch_nunca_assenta() -> None:
    classifier = AtaqueClassifier()
    window = [420.0, 460.0, 410.0, 470.0, 415.0, 465.0, 412.0, 468.0, 418.0, 462.0]

    assert classifier(window, nota_alvo_hz=440.0) == "instavel"


def test_ataque_classifier_indeterminado_para_janela_curta() -> None:
    classifier = AtaqueClassifier()

    assert classifier([440.0, 440.0], nota_alvo_hz=440.0) == "indeterminado"


def test_ataque_classifier_indeterminado_quando_nota_alvo_invalida() -> None:
    classifier = AtaqueClassifier()
    window = [440.0] * 15

    assert classifier(window, nota_alvo_hz=0.0) == "indeterminado"


def test_ataque_classifier_predominant_retorna_classe_majoritaria() -> None:
    classifier = AtaqueClassifier()
    events = [
        AtaqueEvent(0, 440.0, 0.0, 0.0, "direto"),
        AtaqueEvent(500, 440.0, 0.0, 0.0, "direto"),
        AtaqueEvent(1000, 440.0, 0.0, 0.0, "direto"),
        AtaqueEvent(1500, 440.0, -80.0, 0.0, "under_shoot"),
        AtaqueEvent(2000, 440.0, 50.0, 0.0, "over_shoot"),
    ]

    assert classifier.predominant(events) == "direto"


def test_ataque_classifier_predominant_ignora_eventos_indeterminados() -> None:
    classifier = AtaqueClassifier()
    events = [
        AtaqueEvent(0, 440.0, 0.0, 0.0, "indeterminado"),
        AtaqueEvent(500, 440.0, 0.0, 0.0, "indeterminado"),
        AtaqueEvent(1000, 440.0, -80.0, 0.0, "under_shoot"),
    ]

    assert classifier.predominant(events) == "under_shoot"


def test_ataque_classifier_predominant_retorna_indeterminado_quando_lista_vazia() -> None:
    classifier = AtaqueClassifier()

    assert classifier.predominant([]) == "indeterminado"


def test_ataque_classifier_classify_events_inclui_cents_calculados() -> None:
    classifier = AtaqueClassifier()
    window = list(np.linspace(420.0, 440.0, num=15, dtype=np.float64))
    events = classifier.classify_events([(123, window, 440.0)])

    assert len(events) == 1
    assert events[0].timestamp_ms == 123
    assert events[0].nota_alvo_hz == 440.0
    assert events[0].cents_inicial < -20.0
    assert abs(events[0].cents_final) < 20.0
    assert events[0].classificacao == "under_shoot"


def test_ataque_classifier_rejeita_tolerancia_negativa() -> None:
    with pytest.raises(ValueError, match="tolerance_cents"):
        AtaqueClassifier(tolerance_cents=-1.0)


# ---------------------------------------------------------------------------
# TimingAnalyzer
# ---------------------------------------------------------------------------


def test_timing_compute_metrics_onsets_regulares_produzem_bpm_120() -> None:
    analyzer = TimingAnalyzer()
    onsets = [i * 0.5 for i in range(20)]

    metrics = analyzer.compute_metrics(onsets, bpm_gabarito=120.0)

    assert isinstance(metrics, TimingMetrics)
    assert metrics.bpm_usuario == pytest.approx(120.0, abs=1e-6)
    assert metrics.desvio_bpm == pytest.approx(0.0, abs=1e-6)
    assert metrics.irregularidade_ritmica == pytest.approx(0.0, abs=1e-6)
    assert metrics.acelerando_no_batch is False


def test_timing_compute_metrics_detecta_aceleracao_no_segundo_metade() -> None:
    analyzer = TimingAnalyzer()
    first_half = [i * 0.6 for i in range(20)]
    last_first = first_half[-1]
    second_half = [last_first + (i + 1) * 0.3 for i in range(30)]
    onsets = first_half + second_half

    metrics = analyzer.compute_metrics(onsets, bpm_gabarito=100.0, batch_duration_s=30.0)

    assert metrics.acelerando_no_batch is True


def test_timing_compute_metrics_onsets_irregulares_aumentam_irregularidade() -> None:
    analyzer = TimingAnalyzer()
    onsets = [0.0, 0.2, 1.0, 1.1, 2.0, 2.05, 3.0, 3.5, 4.0, 4.9, 6.0]

    metrics = analyzer.compute_metrics(onsets, bpm_gabarito=120.0)

    assert metrics.irregularidade_ritmica > 0.3


def test_timing_compute_metrics_desvio_bpm_calculado_corretamente() -> None:
    analyzer = TimingAnalyzer()
    onsets = [i * 0.5 for i in range(20)]

    metrics = analyzer.compute_metrics(onsets, bpm_gabarito=100.0)

    assert metrics.bpm_usuario == pytest.approx(120.0, abs=1e-6)
    assert metrics.bpm_gabarito == pytest.approx(100.0, abs=1e-6)
    assert metrics.desvio_bpm == pytest.approx(20.0, abs=1e-6)


def test_timing_compute_metrics_zero_onsets_produz_bpm_zero() -> None:
    analyzer = TimingAnalyzer()

    metrics = analyzer.compute_metrics([], bpm_gabarito=120.0)

    assert metrics.bpm_usuario == 0.0
    assert metrics.acelerando_no_batch is False
    assert metrics.irregularidade_ritmica == 0.0


def test_timing_compute_metrics_irregularidade_clampeada_em_um() -> None:
    analyzer = TimingAnalyzer()
    onsets = [0.0, 0.01, 5.0, 5.02, 12.0]

    metrics = analyzer.compute_metrics(onsets, bpm_gabarito=120.0)

    assert 0.0 <= metrics.irregularidade_ritmica <= 1.0


def test_timing_detect_onsets_fallback_detecta_pulsos_energeticos() -> None:
    analyzer = TimingAnalyzer(sample_rate=8000, energy_threshold=0.05, refractory_ms=80.0)
    sample_rate = analyzer.sample_rate
    duration_s = 2.0
    total = int(sample_rate * duration_s)
    samples = np.zeros(total, dtype=np.float32)
    pulse_len = int(sample_rate * 0.02)
    for pulse_start_s in (0.1, 0.6, 1.1, 1.6):
        start_idx = int(pulse_start_s * sample_rate)
        samples[start_idx : start_idx + pulse_len] = 0.5

    onsets = analyzer.detect_onsets(samples)

    assert len(onsets) >= 3
    assert all(onsets[i] < onsets[i + 1] for i in range(len(onsets) - 1))


def test_timing_detect_onsets_em_silencio_retorna_vazio() -> None:
    analyzer = TimingAnalyzer(sample_rate=8000)
    samples = np.zeros(8000, dtype=np.float32)

    assert analyzer.detect_onsets(samples) == []


def test_timing_analyzer_rejeita_sample_rate_invalido() -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        TimingAnalyzer(sample_rate=0)


# ---------------------------------------------------------------------------
# TransposicaoDetector
# ---------------------------------------------------------------------------


def test_transposicao_detector_identifica_offset_consistente_de_dois_semitons() -> None:
    detector = TransposicaoDetector()
    reference = [220.0, 246.94, 261.63, 293.66, 329.63, 349.23, 392.0, 440.0]
    factor = 2.0 ** (2.0 / 12.0)
    user = [hz * factor for hz in reference]

    resultado = detector.detect(user, reference)

    assert isinstance(resultado, TransposicaoDetectada)
    assert resultado.detectada is True
    assert resultado.semitons == 2
    assert resultado.confianca >= 0.7


def test_transposicao_detector_offsets_aleatorios_nao_disparam() -> None:
    detector = TransposicaoDetector()
    rng = np.random.default_rng(seed=42)
    reference = [220.0] * 40
    user = [
        220.0 * float(2.0 ** (semitones / 12.0))
        for semitones in rng.integers(low=-6, high=7, size=40)
    ]

    resultado = detector.detect(user, reference)

    assert resultado.detectada is False


def test_transposicao_detector_pitches_identicos_nao_detectam_transposicao() -> None:
    detector = TransposicaoDetector()
    reference = [261.63, 293.66, 329.63, 349.23, 392.0, 440.0]

    resultado = detector.detect(reference, reference)

    assert resultado.detectada is False
    assert resultado.semitons == 0


def test_transposicao_detector_pula_frames_com_pitch_invalido() -> None:
    detector = TransposicaoDetector()
    reference = [220.0, 0.0, 220.0, 220.0, 220.0]
    factor = 2.0 ** (5.0 / 12.0)
    user = [220.0 * factor, 220.0 * factor, 0.0, 220.0 * factor, 220.0 * factor]

    resultado = detector.detect(user, reference)

    assert resultado.detectada is True
    assert resultado.semitons == 5


def test_transposicao_detector_listas_vazias_abstem() -> None:
    detector = TransposicaoDetector()

    resultado = detector.detect([], [])

    assert resultado.detectada is False
    assert resultado.semitons == 0
    assert resultado.confianca == 0.0


def test_transposicao_detector_rejeita_min_confianca_invalido() -> None:
    with pytest.raises(ValueError, match="min_confianca"):
        TransposicaoDetector(min_confianca=0.0)
    with pytest.raises(ValueError, match="min_confianca"):
        TransposicaoDetector(min_confianca=1.5)
