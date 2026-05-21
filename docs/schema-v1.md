# auladcanto-mcp — JSON Schema v1

This document is the canonical reference for the JSON payloads exchanged
between the auladcanto-mcp server and the Claude operating under
`SKILL.md`. It is also the source of truth for the Pydantic models in
`src/auladcanto/domain/`.

Three models are defined:

1. **`Gabarito`** — the reference description of a song (melody, chords, lyrics).
2. **`BatchReport`** — the analysis report emitted every ~30 seconds during a session.
3. **`PerfilAluno`** — the persistent student profile (calibration, vocal range, preferences).

---

## Schema versioning

Every top-level payload carries an integer `schema_version` field. Current
ceiling is **1**.

| Situation                              | Behaviour                                       |
|----------------------------------------|-------------------------------------------------|
| `schema_version == 1`                  | Parsed and used as documented below             |
| `schema_version > 1`                   | Pydantic rejects with `ValidationError`         |
| `schema_version < 1`                   | Pydantic rejects with `ValidationError`         |
| Field missing                          | Defaults to `1` (current)                       |

When the schema must change, bump the constant and update this file in the
same commit; old payloads remain readable only if a migration helper is
provided.

---

## 1. `Gabarito`

The reference description of a song produced by the preparation pipeline
(phase 2A). The structure supports passages with one voice (`solo`), two
voices in harmony (`duo`), or two voices in unison (`unissono`).

### `Gabarito` top-level fields

| Field                 | Type                       | Description                                                       |
|-----------------------|----------------------------|-------------------------------------------------------------------|
| `schema_version`      | `int` (= 1)                | Schema version; must equal 1.                                     |
| `musica`              | `str`                      | Human title of the song.                                          |
| `artista`             | `str`                      | Performer/composer name.                                          |
| `tom_original`        | `str`                      | Original key, e.g. `"G"`, `"D#m"`.                                |
| `bpm`                 | `float` (> 0)              | Reference tempo in beats per minute.                              |
| `qualidade_gabarito`  | `QualidadeGabarito`        | Confidence envelope (see below).                                  |
| `trechos`             | `list[Trecho]`             | Time-ordered, non-overlapping passages.                           |
| `acordes_violao`      | `list[AcordeViolao]`       | Optional. Guitar chord changes.                                   |
| `letra_timestamped`   | `list[LetraLinha]`         | Optional. Lyrics aligned to timestamps.                           |

### `QualidadeGabarito`

| Field       | Type                                  | Description                                                                                |
|-------------|---------------------------------------|--------------------------------------------------------------------------------------------|
| `nivel`     | `"alta" \| "media" \| "baixa"`        | Headline confidence. Lower confidence → Claude hedges feedback accordingly.                |
| `fontes`    | `list[str]`                           | Sources used. Conventional tags: `bitmidi`, `freemidi`, `midiworld`, `cifraclub`, `musixmatch`, `demucs+crepe`, `basic-pitch`. |
| `alertas`   | `list[str]`                           | Human-readable warnings the Claude should surface (e.g. `"duo vocal detectado em 62%"`).  |

### `Trecho` (tagged union, discriminator: `tipo`)

Each trecho carries the time bounds and the pitch series for its voice(s).
`fim_s` must be strictly greater than `inicio_s`. The whole `trechos` array
must be sorted by `inicio_s` and the spans must not overlap.

#### `TrechoSolo`

| Field      | Type           | Description                                              |
|------------|----------------|----------------------------------------------------------|
| `tipo`     | `"solo"`       | Discriminator literal.                                   |
| `inicio_s` | `float` (≥ 0)  | Start time in seconds (inclusive).                       |
| `fim_s`    | `float` (> `inicio_s`) | End time in seconds (exclusive).                  |
| `voz`      | `NotaSeries`   | Pitch curve of the single voice.                         |

#### `TrechoDuo`

| Field                | Type              | Description                                                       |
|----------------------|-------------------|-------------------------------------------------------------------|
| `tipo`               | `"duo"`           | Discriminator literal.                                            |
| `inicio_s`           | `float`           | Start time in seconds.                                            |
| `fim_s`              | `float`           | End time in seconds.                                              |
| `voz_aguda`          | `NotaSeries`      | Pitch curve of the higher voice.                                  |
| `voz_grave`          | `NotaSeries`      | Pitch curve of the lower voice.                                   |
| `intervalo_semitons` | `int`             | Average harmonic interval between voices, in semitones.           |

#### `TrechoUnissono`

| Field      | Type           | Description                                              |
|------------|----------------|----------------------------------------------------------|
| `tipo`     | `"unissono"`   | Discriminator literal.                                   |
| `inicio_s` | `float`        | Start time in seconds.                                   |
| `fim_s`    | `float`        | End time in seconds.                                     |
| `voz`      | `NotaSeries`   | Pitch curve shared by both voices.                       |

### `NotaSeries`

Parallel arrays. `pitches_hz[i]` is the fundamental observed at `tempos_s[i]`.

| Field        | Type           | Description                                                                                          |
|--------------|----------------|------------------------------------------------------------------------------------------------------|
| `pitches_hz` | `list[float]`  | Fundamental frequencies in Hz. `0.0` marks an unvoiced/silent frame (mir_eval convention).            |
| `tempos_s`   | `list[float]`  | Frame timestamps in seconds. Must have the same length as `pitches_hz`.                              |

### `AcordeViolao`

| Field     | Type            | Description                                                                       |
|-----------|-----------------|-----------------------------------------------------------------------------------|
| `tempo_s` | `float` (≥ 0)   | When the chord starts.                                                            |
| `acorde`  | `str`           | Chord token: root + optional quality + optional extension + optional `/bass` (e.g. `G`, `Em7`, `C#m7b5`, `D/F#`). |

### `LetraLinha`

| Field     | Type           | Description                                |
|-----------|----------------|--------------------------------------------|
| `tempo_s` | `float` (≥ 0)  | When the lyric line begins.                |
| `texto`   | `str`          | Lyric text.                                |

### Sample `Gabarito`

```json
{
  "schema_version": 1,
  "musica": "Faz Parte",
  "artista": "Bruno e Marrone",
  "tom_original": "G",
  "bpm": 96.0,
  "qualidade_gabarito": {
    "nivel": "media",
    "fontes": ["demucs+crepe", "cifraclub"],
    "alertas": ["duo vocal detectado em 62% da musica"]
  },
  "trechos": [
    {
      "tipo": "solo",
      "inicio_s": 0.0,
      "fim_s": 7.2,
      "voz": {
        "pitches_hz": [392.0, 440.0, 493.88, 440.0],
        "tempos_s": [0.0, 0.5, 1.0, 1.5]
      }
    },
    {
      "tipo": "duo",
      "inicio_s": 7.2,
      "fim_s": 32.4,
      "voz_aguda": {
        "pitches_hz": [659.25, 698.46, 659.25],
        "tempos_s": [7.2, 7.7, 8.2]
      },
      "voz_grave": {
        "pitches_hz": [523.25, 554.37, 523.25],
        "tempos_s": [7.2, 7.7, 8.2]
      },
      "intervalo_semitons": 4
    }
  ],
  "acordes_violao": [
    {"tempo_s": 0.0, "acorde": "G"},
    {"tempo_s": 2.0, "acorde": "Em7"},
    {"tempo_s": 4.0, "acorde": "C"},
    {"tempo_s": 6.0, "acorde": "D"}
  ],
  "letra_timestamped": [
    {"tempo_s": 0.5, "texto": "Eu sei que voce..."}
  ]
}
```

---

## 2. `BatchReport`

The analysis report emitted by the analyzers (phase 3B) every 30 seconds.
This is the payload the Claude reads to formulate one feedback message per
batch.

### `BatchReport` top-level fields

| Field                       | Type                              | Description                                                                                       |
|-----------------------------|-----------------------------------|---------------------------------------------------------------------------------------------------|
| `schema_version`            | `int` (= 1)                       | Must equal 1.                                                                                     |
| `batch_numero`              | `int` (≥ 0)                       | Zero-indexed batch number within the session.                                                     |
| `timestamp`                 | `datetime` (ISO 8601)             | When the batch closed.                                                                            |
| `musica_id`                 | `str`                             | Hash identifying the song (matches `~/.auladcanto/cache/{hash}/`).                                |
| `duracao_segundos`          | `int` (> 0)                       | Window length. Defaults to 30.                                                                    |
| `posicao_musica`            | `str`                             | Human-readable position, e.g. `"primeiro refrão"`, `"ponte"`.                                     |
| `voz_escolhida`             | `"aguda" \| "grave" \| "solo" \| "n/a"` | Which voice the student picked for duo passages.                                          |
| `timing`                    | `TimingMetrics`                   | Tempo metrics for the window.                                                                     |
| `pitch`                     | `PitchMetrics`                    | Pitch accuracy metrics.                                                                           |
| `vibrato`                   | `VibratoMetrics`                  | Vibrato presence and quality.                                                                     |
| `respiracao`                | `RespiracaoMetrics`               | Breath detection metrics.                                                                         |
| `volume`                    | `VolumeMetrics`                   | Loudness/projection metrics.                                                                      |
| `transposicao_detectada`    | `TransposicaoDetectada \| null`   | Optional. Detection that user is singing in a different key.                                      |
| `comparacao_batch_anterior` | `ComparacaoBatchAnterior \| null` | Optional. Trend versus the previous batch.                                                        |

### `TimingMetrics`

| Field                      | Type            | Range / values | Interpretation                                                                |
|----------------------------|-----------------|----------------|-------------------------------------------------------------------------------|
| `bpm_usuario`              | `float`         | ≥ 0            | BPM measured from user audio in this window.                                  |
| `bpm_gabarito`             | `float`         | ≥ 0            | BPM expected from the gabarito at this position.                              |
| `desvio_bpm`               | `float`         | any            | `bpm_usuario − bpm_gabarito`. Positive = user faster.                         |
| `acelerando_no_batch`      | `bool`          |                | True if BPM trended upward within the window.                                 |
| `irregularidade_ritmica`   | `float`         | 0.0 – 1.0      | 0 = metronomic; 1 = wildly unsteady.                                          |

### `PitchMetrics`

| Field                  | Type                | Range / values                                                  | Interpretation                                                       |
|------------------------|---------------------|-----------------------------------------------------------------|----------------------------------------------------------------------|
| `notas_corretas_pct`   | `float`             | 0 – 100                                                         | % of frames within ±50¢ of the target.                               |
| `precisao_oitava_pct`  | `float`             | 0 – 100                                                         | % of frames with the correct pitch class (ignoring octave).          |
| `desvio_padrao_cents`  | `float`             | ≥ 0                                                             | Std-dev of cents error. > 30 = unstable intonation.                  |
| `ataque_predominante`  | enum                | `direto`, `under_shoot`, `over_shoot`, `instavel`, `indeterminado` | Dominant attack pattern post-onset.                              |
| `momentos_criticos`    | `list[MomentoCritico]` |                                                              | Notable single-note errors worth surfacing.                          |

### `MomentoCritico`

| Field          | Type    | Range / values | Interpretation                                                |
|----------------|---------|----------------|---------------------------------------------------------------|
| `timestamp_ms` | `int`   | ≥ 0            | Time offset (ms) within the batch.                            |
| `nota_alvo`    | `str`   |                | Target note in scientific pitch notation, e.g. `"G4"`.        |
| `erro_cents`   | `int`   | any            | Signed cents error; negative = flat, positive = sharp.        |

### `VibratoMetrics`

| Field           | Type                                              | Interpretation                                                          |
|-----------------|---------------------------------------------------|-------------------------------------------------------------------------|
| `detectado`     | `bool`                                            | Whether vibrato was detected in the window.                             |
| `frequencia_hz` | `float \| null`                                   | Vibrato rate when present. Typical natural range 5–7 Hz.                |
| `naturalidade`  | `"natural" \| "lento_tremulo" \| "rapido_tenso" \| null` | Qualitative classification. `null` when `detectado=false`.        |

### `RespiracaoMetrics`

| Field                  | Type            | Interpretation                                                          |
|------------------------|-----------------|-------------------------------------------------------------------------|
| `respiros_detectados`  | `int` (≥ 0)     | Count of breaths detected in the window.                                |
| `respiros`             | `list[Respiro]` | Per-breath details.                                                     |
| `alerta_sem_respiro`   | `bool`          | True if a phrase >10s was sung without an audible breath.               |

### `Respiro`

| Field          | Type                                                          | Interpretation                                  |
|----------------|---------------------------------------------------------------|-------------------------------------------------|
| `timestamp_ms` | `int` (≥ 0)                                                   | Breath start, ms offset in the batch.           |
| `duracao_ms`   | `int` (≥ 0)                                                   | Breath duration.                                |
| `tipo`         | `"rapido_insuficiente" \| "normal" \| "preparatorio_longo"`   | Classification of the breath's musical role.    |

### `VolumeMetrics`

| Field                | Type                                | Interpretation                                                  |
|----------------------|-------------------------------------|-----------------------------------------------------------------|
| `media_normalizada`  | `float` (0.0 – 1.0)                 | Average RMS in `[0, 1]` after calibration normalization.        |
| `quedas_abruptas`    | `int` (≥ 0)                         | Count of sudden volume drops (>12 dB in < 200ms).               |
| `projecao_geral`     | `"fraca" \| "boa" \| "forte"`       | Qualitative projection assessment.                              |

### `TransposicaoDetectada`

| Field        | Type           | Interpretation                                                                 |
|--------------|----------------|--------------------------------------------------------------------------------|
| `detectada`  | `bool`         | Whether a consistent N-semitone offset was inferred.                           |
| `semitons`   | `int`          | Inferred offset; negative = user lower than reference.                         |
| `confianca`  | `float` (0–1)  | Confidence of the detection. Suggest transposing only when ≥ 0.7.              |

### `ComparacaoBatchAnterior`

Populated from batch 2 onwards. Each axis carries one of:
`"melhorando"`, `"estavel"`, `"piorando"`, `"acelerando"`, `"desacelerando"`,
`"n/a"`.

| Field                 | Type        | Interpretation                                |
|-----------------------|-------------|-----------------------------------------------|
| `bpm_tendencia`       | `Tendencia` | Tempo trend relative to previous batch.       |
| `pitch_tendencia`     | `Tendencia` | Pitch accuracy trend.                         |
| `respiro_tendencia`   | `Tendencia` | Breath behaviour trend.                       |

### Sample `BatchReport`

```json
{
  "schema_version": 1,
  "batch_numero": 2,
  "timestamp": "2026-05-21T12:00:00Z",
  "musica_id": "a1b2c3d4",
  "duracao_segundos": 30,
  "posicao_musica": "segundo refrão",
  "voz_escolhida": "aguda",
  "timing": {
    "bpm_usuario": 98.0,
    "bpm_gabarito": 96.0,
    "desvio_bpm": 2.0,
    "acelerando_no_batch": true,
    "irregularidade_ritmica": 0.12
  },
  "pitch": {
    "notas_corretas_pct": 82.5,
    "precisao_oitava_pct": 97.0,
    "desvio_padrao_cents": 18.4,
    "ataque_predominante": "under_shoot",
    "momentos_criticos": [
      {"timestamp_ms": 4300, "nota_alvo": "G4", "erro_cents": -35}
    ]
  },
  "vibrato": {
    "detectado": true,
    "frequencia_hz": 6.1,
    "naturalidade": "natural"
  },
  "respiracao": {
    "respiros_detectados": 3,
    "respiros": [
      {"timestamp_ms": 8000, "duracao_ms": 420, "tipo": "normal"}
    ],
    "alerta_sem_respiro": false
  },
  "volume": {
    "media_normalizada": 0.68,
    "quedas_abruptas": 1,
    "projecao_geral": "boa"
  },
  "transposicao_detectada": {
    "detectada": true,
    "semitons": -2,
    "confianca": 0.81
  },
  "comparacao_batch_anterior": {
    "bpm_tendencia": "acelerando",
    "pitch_tendencia": "melhorando",
    "respiro_tendencia": "estavel"
  }
}
```

---

## 3. `PerfilAluno`

Persistent student profile stored at `~/.auladcanto/perfil.json`.

### `PerfilAluno` top-level fields

| Field            | Type                       | Description                                                         |
|------------------|----------------------------|---------------------------------------------------------------------|
| `schema_version` | `int` (= 1)                | Must equal 1.                                                       |
| `nome`           | `str \| null`              | Optional display name.                                              |
| `criado`         | `datetime` (ISO 8601)      | When the profile was first created.                                 |
| `faixa_vocal`    | `FaixaVocal \| null`       | Vocal range (filled during calibration / range probe).              |
| `calibracao`     | `CalibracaoMicrofone \| null` | Microphone calibration results.                                  |
| `preferencias`   | `PreferenciasAluno`        | User preferences (defaulted when absent).                           |

### `NotaMidi`

Used throughout the profile to represent specific notes. The three fields
must be mutually consistent — use `NotaMidi.from_nota("A4")` to build one
without copy-paste errors.

| Field         | Type        | Range / values | Interpretation                                          |
|---------------|-------------|----------------|---------------------------------------------------------|
| `nota`        | `str`       |                | Scientific pitch notation, e.g. `"C4"`, `"C#5"`, `"Eb3"`. |
| `midi_number` | `int`       | 0 – 127        | MIDI note number; 69 = A4.                              |
| `hz`          | `float`     | > 0            | Equal-tempered frequency for `midi_number`.             |

### `FaixaVocal`

| Field             | Type                | Description                                                      |
|-------------------|---------------------|------------------------------------------------------------------|
| `minima`          | `NotaMidi`          | Lowest reliably-produced note.                                   |
| `maxima`          | `NotaMidi`          | Highest reliably-produced note (≥ `minima`).                     |
| `confortavel_min` | `NotaMidi \| null`  | Lower bound of the comfortable subrange (≥ `minima`).            |
| `confortavel_max` | `NotaMidi \| null`  | Upper bound of the comfortable subrange (≤ `maxima`).            |

### `CalibracaoMicrofone`

| Field                          | Type                  | Interpretation                                              |
|--------------------------------|-----------------------|-------------------------------------------------------------|
| `noise_floor_db`               | `float`               | Resting noise floor in dBFS (typically negative).           |
| `range_dinamico_db`            | `float` (≥ 0)         | Useful dynamic range above the noise floor.                 |
| `pitch_detection_acuracia_pct` | `float` (0–100)       | Empirical accuracy on the calibration scale exercise.       |
| `latencia_aproximada_ms`       | `int` (≥ 0)           | Estimated capture latency.                                  |
| `data_calibracao`              | `datetime`            | When this calibration was performed.                        |

### `PreferenciasAluno`

| Field                      | Type                                     | Default     | Interpretation                                    |
|----------------------------|------------------------------------------|-------------|---------------------------------------------------|
| `idioma`                   | `str`                                    | `"pt-BR"`   | Locale used by the Claude persona.                |
| `transposicao_automatica`  | `bool`                                   | `true`      | Whether to auto-suggest key transpositions.       |
| `modo_pratica_default`     | `"voz" \| "violao" \| "ambos"`           | `"voz"`     | Default mode when starting a session.             |
| `sample_rate`              | `int` (> 0)                              | `44100`     | Capture sample rate.                              |
| `chunk_size`               | `int` (> 0)                              | `512`       | Audio callback chunk size in frames.              |

### Sample `PerfilAluno`

```json
{
  "schema_version": 1,
  "nome": "Gabriel",
  "criado": "2026-05-21T10:00:00Z",
  "faixa_vocal": {
    "minima": {"nota": "E2", "midi_number": 40, "hz": 82.4069},
    "maxima": {"nota": "A4", "midi_number": 69, "hz": 440.0},
    "confortavel_min": {"nota": "G2", "midi_number": 43, "hz": 97.9989},
    "confortavel_max": {"nota": "E4", "midi_number": 64, "hz": 329.6276}
  },
  "calibracao": {
    "noise_floor_db": -58.0,
    "range_dinamico_db": 42.5,
    "pitch_detection_acuracia_pct": 87.0,
    "latencia_aproximada_ms": 18,
    "data_calibracao": "2026-05-21T10:05:00Z"
  },
  "preferencias": {
    "idioma": "pt-BR",
    "transposicao_automatica": true,
    "modo_pratica_default": "voz",
    "sample_rate": 44100,
    "chunk_size": 512
  }
}
```

---

## Interpretation Guide (for the Claude in SKILL.md)

These are reusable heuristics the SKILL.md prompt can lean on when reading a
`BatchReport`. They are guidance, not gospel — always combine with the
`qualidade_gabarito` of the underlying reference.

### Pitch

- `pitch.notas_corretas_pct < 70` and the position is a refrão → the voice
  may not be warmed up yet; suggest a gentle warm-up before a retake.
- `pitch.precisao_oitava_pct > 95` but `notas_corretas_pct < 70` → the
  student has the right shape but is consistently flat/sharp; check
  `transposicao_detectada` and consider suggesting a key change.
- `pitch.desvio_padrao_cents > 30` → unstable intonation; a single physical
  cue (e.g. "apoie a respiração no diafragma") usually helps more than
  multiple corrections.
- `pitch.ataque_predominante == "under_shoot"` → student approaches notes
  from below; suggest mentally aiming a half-step above the target.
- `pitch.ataque_predominante == "over_shoot"` → student attacks too high
  and slides down; suggest a calmer, slower attack.

### Vibrato

- `vibrato.detectado == false` over multiple consecutive batches in
  sustained passages → suggest a deliberate vibrato exercise.
- `vibrato.naturalidade == "rapido_tenso"` → typical sign of throat
  tension; suggest jaw release / open vowel cues.
- `vibrato.naturalidade == "lento_tremulo"` → suggests under-supported
  vibrato; cue diaphragm engagement.

### Respiração

- `respiracao.alerta_sem_respiro == true` → mention it explicitly; the
  student is probably running out of air. Suggest a marked breath point.
- `respiros_detectados == 0` in a 30s window → either the mic is far or
  the student is holding tension. Cross-reference `volume.media_normalizada`.

### Timing

- `abs(timing.desvio_bpm) < 2.0` → student is locked in; congratulate
  briefly, no correction needed.
- `timing.acelerando_no_batch == true` and `desvio_bpm > 5` → student is
  rushing; suggest a subdivision count or a metronome for the next try.
- `timing.irregularidade_ritmica > 0.4` → focus the feedback on rhythm
  rather than pitch; the underlying issue is likely tempo, not pitch.

### Volume / projeção

- `volume.media_normalizada < 0.25` → either the student is shy or the
  mic gain is wrong; rule out gain first.
- `volume.quedas_abruptas > 3` → student is dropping the ends of phrases;
  classic posture/breath-support issue.

### Transposição

- `transposicao_detectada.detectada == true` and `confianca >= 0.7` and
  the offset is < 0 → song is too high; offer to transpose down by
  `abs(semitons)` semitones. Wait for the student's go-ahead unless
  `PerfilAluno.preferencias.transposicao_automatica == true`.

### Quality of gabarito

- `gabarito.qualidade_gabarito.nivel == "baixa"` → hedge every pitch-based
  comment ("a referência aqui é aproximada"); never assert the student is
  wrong without a caveat.
- Alerts in `qualidade_gabarito.alertas` should be acknowledged once at
  the start of the session, not repeated every batch.
