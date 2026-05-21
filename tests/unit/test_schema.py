"""Golden tests for the phase-1 domain schema (gabarito, batch, perfil)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from auladcanto.domain.batch import (
    CURRENT_SCHEMA_VERSION,
    BatchReport,
    ComparacaoBatchAnterior,
    MomentoCritico,
    PitchMetrics,
    RespiracaoMetrics,
    Respiro,
    TimingMetrics,
    TransposicaoDetectada,
    VibratoMetrics,
    VolumeMetrics,
)
from auladcanto.domain.gabarito import (
    AcordeViolao,
    Gabarito,
    GabaritoBuilder,
    LetraLinha,
    NotaSeries,
    QualidadeGabarito,
    TrechoDuo,
    TrechoSolo,
)
from auladcanto.domain.perfil_aluno import (
    CalibracaoMicrofone,
    FaixaVocal,
    NotaMidi,
    PerfilAluno,
    PreferenciasAluno,
)

# ---------------------------------------------------------------------------
# Gabarito
# ---------------------------------------------------------------------------


def _make_series(n: int) -> NotaSeries:
    return NotaSeries(
        pitches_hz=[440.0 + i for i in range(n)],
        tempos_s=[float(i) * 0.1 for i in range(n)],
    )


def test_gabarito_roundtrip_solo_and_duo() -> None:
    """A gabarito with one solo and one duo trecho survives a JSON roundtrip."""
    gabarito = (
        GabaritoBuilder(
            musica="Faz Parte",
            artista="Bruno e Marrone",
            tom_original="G",
            bpm=96.0,
            qualidade=QualidadeGabarito(
                nivel="media",
                fontes=["demucs+crepe", "cifraclub"],
                alertas=["duo vocal detectado em 62% da musica"],
            ),
        )
        .add_solo(inicio_s=0.0, fim_s=7.2, voz=_make_series(5))
        .add_duo(
            inicio_s=7.2,
            fim_s=32.4,
            voz_aguda=_make_series(8),
            voz_grave=_make_series(8),
            intervalo_semitons=4,
        )
        .add_acorde(tempo_s=0.0, acorde="G")
        .add_acorde(tempo_s=2.0, acorde="Em7")
        .add_letra(tempo_s=0.5, texto="Eu sei...")
        .build()
    )

    encoded = gabarito.model_dump_json()
    parsed = Gabarito.model_validate_json(encoded)

    assert parsed == gabarito
    assert isinstance(parsed.trechos[0], TrechoSolo)
    assert isinstance(parsed.trechos[1], TrechoDuo)
    assert parsed.trechos[1].intervalo_semitons == 4


def test_gabarito_discriminator_picks_correct_subclass() -> None:
    """The ``tipo`` field acts as the discriminator across solo/duo/unissono."""
    payload = {
        "musica": "X",
        "artista": "Y",
        "tom_original": "C",
        "bpm": 120.0,
        "qualidade_gabarito": {"nivel": "alta", "fontes": ["bitmidi"], "alertas": []},
        "trechos": [
            {
                "tipo": "solo",
                "inicio_s": 0.0,
                "fim_s": 2.0,
                "voz": {"pitches_hz": [440.0], "tempos_s": [0.0]},
            },
            {
                "tipo": "unissono",
                "inicio_s": 2.0,
                "fim_s": 4.0,
                "voz": {"pitches_hz": [440.0], "tempos_s": [0.0]},
            },
        ],
    }
    gabarito = Gabarito.model_validate(payload)
    assert gabarito.trechos[0].tipo == "solo"
    assert gabarito.trechos[1].tipo == "unissono"


def test_gabarito_rejects_overlapping_trechos() -> None:
    """Two trechos with overlapping time spans are rejected at validation."""
    with pytest.raises(ValidationError, match="overlap"):
        (
            GabaritoBuilder(
                musica="X",
                artista="Y",
                tom_original="C",
                bpm=120.0,
                qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
            )
            .add_solo(inicio_s=0.0, fim_s=5.0, voz=_make_series(3))
            .add_solo(inicio_s=4.0, fim_s=8.0, voz=_make_series(3))
            .build()
        )


def test_gabarito_rejects_unsorted_trechos() -> None:
    """Trechos must arrive sorted by ``inicio_s``."""
    with pytest.raises(ValidationError, match="sorted"):
        (
            GabaritoBuilder(
                musica="X",
                artista="Y",
                tom_original="C",
                bpm=120.0,
                qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
            )
            .add_solo(inicio_s=5.0, fim_s=10.0, voz=_make_series(3))
            .add_solo(inicio_s=0.0, fim_s=4.0, voz=_make_series(3))
            .build()
        )


def test_nota_series_length_mismatch_is_rejected() -> None:
    """``pitches_hz`` and ``tempos_s`` must have the same length."""
    with pytest.raises(ValidationError, match="same length"):
        NotaSeries(pitches_hz=[440.0, 442.0], tempos_s=[0.0])


def test_nota_series_len_dunder() -> None:
    """``len(NotaSeries)`` returns the number of frames."""
    assert len(_make_series(7)) == 7


@pytest.mark.parametrize("nivel", ["alta", "media", "baixa"])
def test_qualidade_gabarito_accepts_known_levels(nivel: str) -> None:
    qg = QualidadeGabarito(nivel=nivel, fontes=["x"])  # type: ignore[arg-type]
    assert qg.nivel == nivel


def test_qualidade_gabarito_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        QualidadeGabarito(nivel="ótima", fontes=["x"])  # type: ignore[arg-type]


@pytest.mark.parametrize("chord", ["G", "Em7", "C#m7b5", "D/F#", "Fmaj7", "Bb", "Asus4"])
def test_acorde_violao_accepts_common_chords(chord: str) -> None:
    a = AcordeViolao(tempo_s=0.0, acorde=chord)
    assert a.acorde == chord


def test_acorde_violao_rejects_garbage() -> None:
    with pytest.raises(ValidationError, match="chord token"):
        AcordeViolao(tempo_s=0.0, acorde="???")


def test_letra_linha_roundtrip() -> None:
    line = LetraLinha(tempo_s=1.25, texto="Hello")
    assert LetraLinha.model_validate_json(line.model_dump_json()) == line


# ---------------------------------------------------------------------------
# BatchReport
# ---------------------------------------------------------------------------


def _make_batch_report(
    *,
    with_optional: bool,
    schema_version: int = CURRENT_SCHEMA_VERSION,
) -> BatchReport:
    timing = TimingMetrics(
        bpm_usuario=98.0,
        bpm_gabarito=96.0,
        desvio_bpm=2.0,
        acelerando_no_batch=True,
        irregularidade_ritmica=0.12,
    )
    pitch = PitchMetrics(
        notas_corretas_pct=82.5,
        precisao_oitava_pct=97.0,
        desvio_padrao_cents=18.4,
        ataque_predominante="under_shoot",
        momentos_criticos=[
            MomentoCritico(timestamp_ms=4_300, nota_alvo="G4", erro_cents=-35),
        ],
    )
    vibrato = VibratoMetrics(detectado=True, frequencia_hz=6.1, naturalidade="natural")
    respiracao = RespiracaoMetrics(
        respiros_detectados=3,
        respiros=[Respiro(timestamp_ms=8_000, duracao_ms=420, tipo="normal")],
        alerta_sem_respiro=False,
    )
    volume = VolumeMetrics(media_normalizada=0.68, quedas_abruptas=1, projecao_geral="boa")

    kwargs: dict[str, object] = {
        "schema_version": schema_version,
        "batch_numero": 2,
        "timestamp": datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
        "musica_id": "abc123",
        "duracao_segundos": 30,
        "posicao_musica": "segundo refrão",
        "voz_escolhida": "aguda",
        "timing": timing,
        "pitch": pitch,
        "vibrato": vibrato,
        "respiracao": respiracao,
        "volume": volume,
    }
    if with_optional:
        kwargs["transposicao_detectada"] = TransposicaoDetectada(
            detectada=True, semitons=-2, confianca=0.81
        )
        kwargs["comparacao_batch_anterior"] = ComparacaoBatchAnterior(
            bpm_tendencia="acelerando",
            pitch_tendencia="melhorando",
            respiro_tendencia="estavel",
        )
    return BatchReport(**kwargs)  # type: ignore[arg-type]


def test_batch_report_roundtrip_with_all_optionals() -> None:
    report = _make_batch_report(with_optional=True)
    encoded = report.to_json()
    parsed = BatchReport.from_json(encoded)
    assert parsed == report
    assert parsed.transposicao_detectada is not None
    assert parsed.comparacao_batch_anterior is not None


def test_batch_report_roundtrip_without_optionals() -> None:
    report = _make_batch_report(with_optional=False)
    parsed = BatchReport.from_json(report.to_json())
    assert parsed == report
    assert parsed.transposicao_detectada is None
    assert parsed.comparacao_batch_anterior is None


def test_batch_report_default_schema_version_is_one() -> None:
    report = _make_batch_report(with_optional=False)
    assert report.schema_version == 1


def test_batch_report_rejects_future_schema_version() -> None:
    with pytest.raises(ValidationError, match="newer than this build"):
        _make_batch_report(with_optional=False, schema_version=99)


def test_batch_report_rejects_invalid_schema_version_zero() -> None:
    with pytest.raises(ValidationError, match="schema_version must be >= 1"):
        _make_batch_report(with_optional=False, schema_version=0)


@pytest.mark.parametrize(
    "value", ["direto", "under_shoot", "over_shoot", "instavel", "indeterminado"]
)
def test_pitch_metrics_accepts_known_ataque_predominante(value: str) -> None:
    pm = PitchMetrics(
        notas_corretas_pct=50.0,
        precisao_oitava_pct=50.0,
        desvio_padrao_cents=10.0,
        ataque_predominante=value,  # type: ignore[arg-type]
    )
    assert pm.ataque_predominante == value


def test_pitch_metrics_rejects_unknown_ataque_predominante() -> None:
    with pytest.raises(ValidationError):
        PitchMetrics(
            notas_corretas_pct=50.0,
            precisao_oitava_pct=50.0,
            desvio_padrao_cents=10.0,
            ataque_predominante="aleatorio",  # type: ignore[arg-type]
        )


def test_pitch_metrics_notas_corretas_pct_out_of_range() -> None:
    with pytest.raises(ValidationError):
        PitchMetrics(
            notas_corretas_pct=120.0,
            precisao_oitava_pct=80.0,
            desvio_padrao_cents=10.0,
            ataque_predominante="direto",
        )


def test_volume_metrics_media_normalizada_out_of_range() -> None:
    with pytest.raises(ValidationError):
        VolumeMetrics(media_normalizada=1.5, quedas_abruptas=0, projecao_geral="boa")


# ---------------------------------------------------------------------------
# PerfilAluno
# ---------------------------------------------------------------------------


def test_nota_midi_from_nota_a4_is_69_at_440hz() -> None:
    nm = NotaMidi.from_nota("A4")
    assert nm.midi_number == 69
    assert nm.hz == pytest.approx(440.0, abs=1e-6)


@pytest.mark.parametrize(
    ("nota", "expected_midi"),
    [("C4", 60), ("C#5", 73), ("Eb3", 51), ("G2", 43)],
)
def test_nota_midi_from_nota_known_values(nota: str, expected_midi: int) -> None:
    assert NotaMidi.from_nota(nota).midi_number == expected_midi


def test_nota_midi_rejects_inconsistent_fields() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        NotaMidi(nota="A4", midi_number=70, hz=440.0)


def test_perfil_aluno_save_load_roundtrip(tmp_path: Path) -> None:
    profile = PerfilAluno(
        nome="Gabriel",
        criado=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
        faixa_vocal=FaixaVocal(
            minima=NotaMidi.from_nota("E2"),
            maxima=NotaMidi.from_nota("A4"),
            confortavel_min=NotaMidi.from_nota("G2"),
            confortavel_max=NotaMidi.from_nota("E4"),
        ),
        calibracao=CalibracaoMicrofone(
            noise_floor_db=-58.0,
            range_dinamico_db=42.5,
            pitch_detection_acuracia_pct=87.0,
            latencia_aproximada_ms=18,
            data_calibracao=datetime(2026, 5, 21, 10, 5, 0, tzinfo=UTC),
        ),
    )

    target = tmp_path / "perfil.json"
    profile.save(target)

    assert target.exists()
    loaded = PerfilAluno.load(target)
    assert loaded == profile


def test_perfil_aluno_defaults() -> None:
    profile = PerfilAluno(criado=datetime(2026, 1, 1, tzinfo=UTC))
    assert profile.schema_version == 1
    assert profile.nome is None
    assert profile.faixa_vocal is None
    assert profile.calibracao is None
    assert profile.preferencias == PreferenciasAluno()
    assert profile.preferencias.idioma == "pt-BR"
    assert profile.preferencias.transposicao_automatica is True
    assert profile.preferencias.modo_pratica_default == "voz"
    assert profile.preferencias.sample_rate == 44100
    assert profile.preferencias.chunk_size == 512


def test_faixa_vocal_rejects_inverted_bounds() -> None:
    with pytest.raises(ValidationError, match="must be >= minima"):
        FaixaVocal(
            minima=NotaMidi.from_nota("A4"),
            maxima=NotaMidi.from_nota("E2"),
        )


def test_perfil_aluno_save_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "perfil.json"
    profile = PerfilAluno(criado=datetime(2026, 1, 1, tzinfo=UTC))
    profile.save(target)
    assert target.exists()
