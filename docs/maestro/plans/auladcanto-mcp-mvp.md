# Plano Estruturado — `auladcanto-mcp` (Tutor Musical via Claude Code)

> **Origem:** Conversa de design conduzida no Claude Web, transformada em plano estruturado via Maestro (depth = Deep). Diretório do projeto: `/home/gabwolff/auladcanto/`.

---

## 1. Contexto

### 1.1 Problema
O usuário quer um tutor musical pessoal que combine **análise técnica de canto e violão** com **feedback didático no estilo professor humano**, sem depender de servidor remoto ou app dedicado. Hoje, soluções existentes ou são apps fechados (Yousician, Moises) ou exigem mensalidade. A hipótese central é que um **MCP local + persona via SKILL.md/CLAUDE.md** pode entregar 80% do valor de um professor para prática deliberada, ao custo de centavos por sessão (só os tokens do Claude lendo JSON estruturado).

### 1.2 Resultado desejado
- Usuário instala via `pip install auladcanto-mcp` + `claude mcp add ...`, registra no Claude Code uma única vez.
- Diz "quero aprender Wish You Were Here" → Claude prepara o gabarito (com transparência sobre o tempo) → usuário canta/toca → Claude dá feedback contextualizado por batches de 30s.
- Histórico de evolução por música persistido localmente, sem servidor.
- Áudio nunca sai da máquina; só metadados JSON sobem para a API do Claude.

### 1.3 Decisões de escopo já tomadas
- **Depth de design:** Deep (7 seções, sondagem, alternativas por decisão).
- **MVP médio:** voz + violão + calibração de microfone + histórico por música. Sem modos de estudo alternativos, sem gamificação no MVP.
- **Plataforma alvo MVP:** Linux apenas (ambiente do dev é Linux 7.0.9-zen). Multi-plataforma vira pós-MVP.
- **Risco do gabarito:** fallback gracioso (MIDI database → cifra → pipeline de áudio), com `qualidade_gabarito` exposto no JSON.
- **Duos vocais:** pipeline híbrido completo (solo + uníssono + duo), usuário escolhe a voz.
- **Validação MVP:** bateria de testes automatizados + uso manual real.
- **Distribuição:** pip install + claude mcp add (nome do pacote: `auladcanto-mcp`).

---

## 2. Pressupostos e Restrições

### 2.1 Pressupostos técnicos (a sondar)
| # | Pressuposto | Risco se falso |
|---|------------|----------------|
| P1 | `aubio` mantém detecção de pitch com confiabilidade ≥70% para voz com vibrato leve | Score de pitch fica enviesado; CLAUDE.md precisa avisar sobre incerteza |
| P2 | Bases públicas (BitMIDI, FreeMidi, MidiWorld) cobrem ≥70% do repertório pop/MPB/rock comum | Cai mais frequentemente no pipeline lento (Demucs); UX precisa comunicar tempo |
| P3 | `mir_eval.melody` funciona bem aplicado em janelas de 30s (uso fora do design original offline) | Pode precisar de wrapper próprio para alinhamento temporal; DTW como fallback |
| P4 | `htdemucs_6s` entrega trilha de guitarra utilizável o suficiente para feedback de violão | Feedback de violão fica restrito a "acordes via Cifra Club", sem análise de dedilhado |
| P5 | Latência de captura via `sounddevice` (PortAudio/ALSA) é estável em Linux desktop | Jitter de batch atrapalha análise; precisa de pré-buffer maior |
| P6 | Usuário tem `ffmpeg` instalável e CPU "moderna" (≥4 cores) | Pipeline de áudio inviável em hardware fraco; precisa banner de aviso |

### 2.2 Restrições
- **Local-first absoluto:** áudio jamais trafega para serviço externo. Só metadados JSON.
- **Sem servidor próprio:** zero dependência de infra mantida pelo dev.
- **Custo por sessão alvo:** ≤ $0.05 (entrada ~10K tokens, saída ~2K). Ver §6.4.
- **Python ≥ 3.11** (suporte completo a `asyncio.TaskGroup`, type hints modernos).
- **Compatibilidade legal:** `yt-dlp` para uso pessoal aceitável; documentação explícita do trade-off legal no README.
- **Privacidade:** estado persistido em `~/.auladcanto/`, isolado do projeto.

---

## 3. Arquitetura

### 3.1 Visão de alto nível

```
┌──────────────────────────────────────────────────────────────┐
│  Claude Code (UI conversacional)                             │
│  ↳ lê: ~/.auladcanto/SKILL.md  (persona do professor)        │
│  ↳ chama: ferramentas MCP via stdio                          │
└─────────────────────┬────────────────────────────────────────┘
                      │ MCP / stdio
┌─────────────────────▼────────────────────────────────────────┐
│  auladcanto-mcp  (processo Python local)                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Camada 1 — Ferramentas MCP (interface)                 │  │
│  │  buscar_musica, confirmar_download, verificar_cache,   │  │
│  │  preparar_gabarito, iniciar_sessao, get_batch_atual,   │  │
│  │  get_contexto_sessao, pausar_sessao, get_historico,    │  │
│  │  calibrar_microfone, get_perfil_aluno                  │  │
│  └─────────────┬──────────────────────────────────────────┘  │
│  ┌─────────────▼──────────────────────────────────────────┐  │
│  │ Camada 2 — Domínio                                     │  │
│  │  • preparação (busca→gabarito) com fallback gracioso   │  │
│  │  • análise em tempo real (callback aubio + buffer 30s) │  │
│  │  • comparador (mir_eval + DTW)                         │  │
│  │  • analisadores ricos (vibrato, respiração, ataque…)   │  │
│  │  • detecção de polifonia vocal                         │  │
│  │  • calibração de microfone                             │  │
│  └─────────────┬──────────────────────────────────────────┘  │
│  ┌─────────────▼──────────────────────────────────────────┐  │
│  │ Camada 3 — Persistência                                │  │
│  │  ~/.auladcanto/  (perfil, cache, sessões, histórico)   │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
        │ subprocess / async file watching
        ▼
┌──────────────────────────────────────────────────────────────┐
│  Tools externos:                                             │
│   yt-dlp (download), ffmpeg (transcode), demucs (separação), │
│   basic-pitch / crepe (pitch→MIDI offline)                   │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Camadas e responsabilidades

**Camada 1 — Ferramentas MCP (`auladcanto/mcp/`):** apenas adaptação. Cada ferramenta serializa/deserializa JSON e delega ao domínio. Sem lógica de negócio.

**Camada 2 — Domínio (`auladcanto/domain/`):**
- `preparation/` — pipeline de busca de gabarito (search MIDI → cifra → áudio).
- `analysis/` — callback de áudio, buffer de 30s, analisadores ricos.
- `gabarito/` — modelo de gabarito (suporta solo/duo/uníssono via tipo discriminado).
- `comparator/` — comparação batch vs. gabarito usando `mir_eval`.
- `calibration/` — calibração inicial de microfone.

**Camada 3 — Persistência (`auladcanto/storage/`):**
```
~/.auladcanto/
  perfil.json                  # range vocal, calibração, preferências
  SKILL.md                     # gerado/atualizado pelo pacote
  CLAUDE.md                    # gerado/atualizado pelo pacote
  cache/
    {hash_audio}/              # 1 música = 1 hash
      metadata.json
      gabarito.json            # estrutura híbrida (§3.4)
      vocals.wav  vocals.midi
      guitar.wav  guitar.midi
      qualidade.json           # confiança do gabarito
  sessoes/
    {musica_hash}/
      ativa.json               # batch atual + buffer
      {timestamp}.json         # sessões fechadas
  historico/
    {musica_hash}/
      progresso.json           # evolução
      padroes.json             # erros recorrentes
```

### 3.3 Pipeline de preparação (com fallback gracioso)

```
[entrada: "Wish You Were Here"]
        │
        ▼
[1] Busca em MIDI databases (BitMIDI, FreeMidi, MidiWorld)
        │ found?
   SIM ─┘─► [valida estrutura: BPM razoável, faixa de notas, polifonia]
        │              │
        │              └─► gabarito {confiança: alta, fontes: [bitmidi]}
        │ NÃO
        ▼
[2] Busca em Cifra Club + Musixmatch (acordes + letra timestamped)
        │ found?
   SIM ─┘─► gabarito parcial {confiança: média, fontes: [cifraclub, musixmatch]}
        │   (sem melodia — só acordes e letra)
        │ NÃO
        ▼
[3] Pipeline de áudio (fallback)
    yt-dlp ytsearch3 → usuário confirma versão
    → ffmpeg normaliza
    → htdemucs_6s separa (vocals + guitar)
    → CREPE em vocals → basic-pitch em guitar
    → detecção de polifonia vocal (§3.4)
    → gabarito {confiança: baixa-média, fontes: [demucs+crepe]}
```

Cada saída expõe `qualidade_gabarito` para o Claude alertar o usuário.

### 3.4 Modelo de gabarito (suporta duos)

```json
{
  "musica": "Faz Parte",
  "artista": "Bruno e Marrone",
  "tom_original": "G",
  "bpm": 96,
  "qualidade_gabarito": {
    "nivel": "media",
    "fontes": ["demucs+crepe", "cifraclub"],
    "alertas": ["duo vocal detectado em 62% da musica"]
  },
  "trechos": [
    {
      "tipo": "solo",
      "inicio_s": 0.0, "fim_s": 7.2,
      "voz": {"pitches": [...], "tempos": [...]}
    },
    {
      "tipo": "duo",
      "inicio_s": 7.2, "fim_s": 32.4,
      "voz_aguda": {"pitches": [...], "tempos": [...]},
      "voz_grave": {"pitches": [...], "tempos": [...]},
      "intervalo_semitons": 4
    },
    {
      "tipo": "unissono",
      "inicio_s": 32.4, "fim_s": 36.0,
      "voz": {"pitches": [...], "tempos": [...]}
    }
  ],
  "acordes_violao": [
    {"tempo_s": 0.0, "acorde": "G"},
    {"tempo_s": 2.0, "acorde": "Em7"}
  ],
  "letra_timestamped": [
    {"tempo_s": 0.5, "texto": "Eu sei..."}
  ]
}
```

### 3.5 Motor de análise (batch 30s)

```
sounddevice.InputStream (callback minimo, copia chunk → queue.Queue)
        ↓
thread separada
        ↓
aubio.pitch (YIN) + aubio.onset + aubio.tempo
        ↓
buffer de 30s acumulado em memoria
        ↓ a cada 30s
        ▼
[analise rica em paralelo via asyncio.TaskGroup]
  • comparador: mir_eval.melody contra trecho do gabarito alinhado
  • vibrato: FFT sobre serie de pitch (5-7Hz)
  • respiracao: detecao de silencios 40-500ms
  • ataque: pitch_window pos-onset vs nota-alvo (under/over/direto)
  • timing: BPM atual vs gabarito, aceleração na janela
  • detecao_transposicao: erro consistente de N semitons
        ↓
JSON de batch (schema v1) com versionamento
        ↓
salva em ~/.auladcanto/sessoes/{musica}/ativa.json
        ↓
Claude le via MCP get_batch_atual()
```

### 3.6 Persona e prompt (SKILL.md / CLAUDE.md)

O pacote escreve dois arquivos no setup:
- **`SKILL.md`** — discoverable pelo Claude Code; descreve quando ativar e fluxos.
- **`CLAUDE.md`** opcional, na raiz do projeto do usuário, descreve a persona "professor experiente" com regras de feedback (3-5 parágrafos, sem jargão, uma instrução física concreta por batch, etc.).

Ambos são **gerados a partir de templates versionados** no pacote, atualizados em upgrades sem perder customizações do usuário.

---

## 4. Decisões-chave (matriz)

| # | Decisão | Alternativas consideradas | Escolha | Justificativa |
|---|--------|---------------------------|---------|---------------|
| D1 | Linguagem | Go+Flutter; Python; C/C++ engine; Python+wrapper Rust | **Python puro** | Todo o ecossistema de áudio/ML vive em Python; libs C-native cobrem a parte crítica via binding (aubio, librosa); engine C++ é otimização prematura |
| D2 | Pitch tempo real | CREPE; librosa.pyin; aubio (YIN) | **aubio YIN** | Latência ~5-15ms em C nativo; suficiente para batch 30s; CREPE fica reservado para pipeline offline de gabarito |
| D3 | Separação de stems | Demucs htdemucs; htdemucs_6s; mdx_extra; Moises.ai | **htdemucs_6s (default), mdx_extra (modo rápido)** | 6s isola guitar/piano, essencial para violão; usuário pode optar por mdx_extra em hardware fraco |
| D4 | Pitch→MIDI offline | Basic Pitch; CREPE+pós-proc; Omnizart | **CREPE para voz, Basic Pitch para violão** | CREPE lida melhor com vibrato/melisma vocal; Basic Pitch funciona bem em material instrumental limpo |
| D5 | Distribuição | pip + mcp add; pipx; uv tool; git clone+script | **pip + mcp add** | Mais reconhecível; setup em 2 comandos; PyPI handles upgrades |
| D6 | Persistência | SQLite; JSON em disco; LMDB | **JSON em disco** | Estado pequeno (~MB), human-readable, fácil debug, zero dependência adicional |
| D7 | Versionamento de schema JSON | Sem versão; campo `schema_version`; semver em path | **`schema_version` no payload** | Permite evolução sem quebrar prompt do Claude silenciosamente |
| D8 | Tratamento de duos | Bloquear; usar média; separar e escolher; híbrido completo | **híbrido completo** | Caso de uso real do dev; média é matematicamente errado; separação parcial é o melhor possível |
| D9 | Calibração no MVP | Adiada; obrigatória 1ª vez; recomendada | **obrigatória 1ª vez** | Sem isso, feedback fica sistematicamente errado (microfones variam absurdamente) |
| D10 | Transposição vocal | Adiada; manual; automática por range | **automática mas opcional** | Calibração de range no perfil; sistema sugere transposição quando música excede ±2 semitons do range confortável; usuário decide |
| D11 | Histórico de longo prazo | Adiar; só sessão atual; histórico por música | **histórico por música** | Sem isso, Claude não dá feedback de evolução ("você melhorou X esta semana"); diferencial central |
| D12 | Cache de gabarito | Sem cache; cache por nome; cache por hash de áudio | **hash de áudio** | Resistente a renomeações; não reprocessa quando mesmo arquivo cai sob outro título |
| D13 | Plataforma MVP | só Linux; Linux+macOS; cross | **só Linux** | Ambiente do dev; reduz superfície de bugs; multi-plataforma vira fase pós-MVP |
| D14 | Estratégia para risco do gabarito | Spike antes do MVP; fallback gracioso; conjunto curado | **fallback gracioso** | Sistema sempre tenta melhor fonte primeiro e expõe confiança ao usuário |
| D15 | Gamificação no MVP | Tudo; belt+recordes; sem gamificação | **sem gamificação** | Pós-MVP. Foco do MVP é validar análise técnica + persona didática |

---

## 5. Fases de Implementação

> **Convenção:** cada fase é independente em arquivos quando possível, permitindo execução em paralelo via Maestro. Fases marcadas com 🔗 têm dependência forte da anterior.

### Fase 0 — Bootstrap do projeto (1 dia)
**Owner agent sugerido:** `devops-engineer`
- `pyproject.toml` (poetry/hatch), Python ≥3.11
- Layout `src/auladcanto/{mcp,domain,storage,cli}/`
- Pytest + tox/nox; ruff + mypy
- `Makefile` ou `just` para tarefas comuns
- README inicial com instruções de instalação
- LICENÇA (recomendado MIT — confirmar com dev)
- Estrutura `~/.auladcanto/` criada no primeiro run via `auladcanto init`

**Entregável:** `pip install -e .` funciona; pytest roda; `auladcanto --help` mostra subcomandos.

---

### Fase 1 — Modelo de domínio + esquema JSON v1 (2 dias)
**Owner:** `architect` para revisar; `coder` para implementar.
- `domain/gabarito.py` — dataclasses para gabarito híbrido (solo/duo/uníssono).
- `domain/batch.py` — dataclasses para JSON de batch v1, com `schema_version`.
- `domain/perfil_aluno.py` — range vocal, calibração, preferências.
- Testes de serialização/desserialização (golden tests).
- Documento `docs/schema-v1.md` descrevendo todos os campos para o Claude interpretar.

🔗 **Bloqueia:** todas as fases seguintes (todas dependem dos tipos).

---

### Fase 2A — Pipeline de busca de gabarito (3-4 dias)
**Owner:** `coder` + `data-engineer`
- `domain/preparation/midi_search.py` — adaptadores BitMIDI, FreeMidi, MidiWorld (com cache HTTP).
- `domain/preparation/cifra_search.py` — adaptador Cifra Club + Musixmatch.
- `domain/preparation/audio_pipeline.py` — yt-dlp + ffmpeg + demucs + crepe/basic-pitch.
- `domain/preparation/orchestrator.py` — implementa fallback gracioso (1→2→3).
- `domain/preparation/quality.py` — avaliação de qualidade do gabarito (`alta/media/baixa`).
- Testes: mock de cada fonte + integração com 5 músicas de teste.

---

### Fase 2B — Detecção de polifonia vocal e modelo de duos (2 dias)
**Owner:** `coder`
- `domain/gabarito/polifonia.py` — `detectar_polifonia_temporal(audio)` retorna janelas com 1 ou 2 vozes simultâneas.
- `domain/gabarito/separacao.py` — separação por altura (filtro por pitch).
- Testes em 2-3 músicas de duo do repertório alvo do dev.

🔗 Depende de Fase 2A para ter `vocals.wav`.

---

### Fase 3A — Captura de áudio em batches (2 dias)
**Owner:** `coder`
- `domain/analysis/capture.py` — `sounddevice.InputStream` com callback mínimo e `queue.Queue`.
- `domain/analysis/buffer.py` — buffer de 30s em memória, com fechamento async.
- Tratamento de interrupção (SIGINT) e cleanup.
- Tratamento de timeout de sessão inativa (>10 min sem áudio → encerra sessão).

---

### Fase 3B — Analisadores ricos (3-4 dias)
**Owner:** `coder` + `performance-engineer` para verificar uso de CPU
- `domain/analysis/pitch.py` — aubio YIN + tracking de pitch.
- `domain/analysis/vibrato.py` — FFT sobre série de pitch, classificação natural/trêmulo/tenso.
- `domain/analysis/respiracao.py` — detecção de silêncios 40-500ms, alerta sem respiro.
- `domain/analysis/ataque.py` — classificação direto/under_shoot/over_shoot por onset.
- `domain/analysis/timing.py` — BPM atual, desvio, aceleração na janela, irregularidade rítmica.
- `domain/analysis/transposicao.py` — detecção de erro consistente de N semitons.
- Cada analisador retorna um sub-objeto do JSON de batch v1.

🔗 Depende de Fase 1 (schema) e Fase 3A (buffer).

---

### Fase 3C — Comparador com gabarito (2-3 dias)
**Owner:** `coder`
- `domain/comparator/aligner.py` — alinhamento temporal usando mir_eval.melody.resample_melody_series + DTW como fallback.
- `domain/comparator/score.py` — calcula `precisao_pitch_pct`, `precisao_oitava_pct`, `notas_cantadas_pct`, etc.
- Quando trecho é duo: o usuário escolhe voz aguda/grave; comparador usa apenas a escolhida e expõe a outra como contexto.
- Testes contra gabaritos sintéticos para verificar scores.

🔗 Depende de Fase 2 (gabarito) e Fase 3B (séries de pitch).

---

### Fase 4 — Calibração de microfone (2 dias)
**Owner:** `coder` + `ux-designer` para fluxo conversacional.
- `domain/calibration/microfone.py`:
  1. 5s de silêncio → noise floor.
  2. 5s de fala normal → range dinâmico.
  3. 5s de notas em escala (instruídas pelo Claude) → valida pitch detection.
  4. Mede latência aproximada de captura.
- Resultado salvo em `~/.auladcanto/perfil.json`.
- Reexecução opcional (`auladcanto calibrar`).

---

### Fase 5 — Servidor MCP e ferramentas (2-3 dias)
**Owner:** `api-designer` (contratos) + `coder` (implementação).
Ferramentas a expor:
- `buscar_musica(query)` — retorna candidatos para confirmação.
- `confirmar_download(video_id_ou_url)` — dispara pipeline.
- `verificar_cache(musica_id)` — status do gabarito.
- `preparar_gabarito(musica_id)` — pipeline em background, retorna status.
- `iniciar_sessao(musica_id, modo, voz_escolhida=None)` — modo: voz | violao | ambos.
- `pausar_sessao()` / `retomar_sessao()`.
- `get_batch_atual()` — JSON do último batch fechado.
- `get_contexto_sessao()` — todos os batches da sessão atual.
- `get_historico(musica_id)` — evolução cross-session.
- `get_perfil_aluno()` — calibração + range.
- `calibrar_microfone()` — dispara fluxo de calibração.

Cada ferramenta tem teste de contrato (input/output match com schema).

🔗 Depende de Fases 2, 3, 4.

---

### Fase 6 — Persona (SKILL.md / CLAUDE.md) (2 dias)
**Owner:** `copywriter` + `technical-writer` para schema; revisão do dev.
- `templates/SKILL.md` — gatilhos, fluxo obrigatório, interpretação do JSON v1, regras de feedback (incluindo "uma observação por batch", "instrução física concreta", etc.).
- `templates/CLAUDE.md` — persona "professor experiente de canto e violão", tom, exemplos calibrados.
- `auladcanto init` escreve esses arquivos em `~/.auladcanto/` e oferece copiar para CWD.
- Testes manuais: rodar 3 cenários conversacionais e revisar tom.

---

### Fase 7 — CLI e empacotamento para distribuição (1-2 dias)
**Owner:** `devops-engineer`
- `auladcanto` CLI: `init`, `calibrar`, `mcp-server`, `verificar-deps`, `limpar-cache`.
- `pyproject.toml` com `[project.scripts]` mapeando `auladcanto-mcp` → entry point do servidor MCP.
- Detecção de `ffmpeg` ausente com mensagem clara.
- README final com instruções: `pip install auladcanto-mcp` + `claude mcp add auladcanto -- auladcanto-mcp`.
- Eventual publicação em TestPyPI antes de PyPI público.

---

### Fase 8 — Bateria de testes integrados + uso manual (3-5 dias)
**Owner:** `tester` + dev em uso real.
- Golden tests do JSON de batch para 3 cenários simulados (pitch perfeito, vibrato tenso, frase sem respiro).
- Testes de integração end-to-end com música real curada.
- Testes de regressão de qualidade de gabarito para conjunto fixo.
- Uso pessoal real do dev por 5-10 sessões em 5-10 músicas, registrando bugs/UX.

🔗 Fase final do MVP.

---

### Roadmap pós-MVP (não detalhado neste plano)
- Multi-plataforma (macOS, Windows).
- Modos de estudo (frase a frase, loop, 0.5x, shadow).
- Gamificação (belt system, recordes, replays de glória).
- Validação de gabarito via correlação com áudio original.
- Detecção/sugestão de aquecimento vocal.
- Modo "audio de 5-10s para API que aceita áudio" (deixado como ganho explícito de arquitetura).

---

## 6. Riscos e Mitigações

| # | Risco | Probabilidade | Impacto | Mitigação |
|---|-------|---------------|---------|-----------|
| R1 | Pipeline Demucs lento demais em CPU típica → UX ruim | Alta | Alto | Fallback gracioso (D14); cache agressivo por hash; transparência via JSON `tempo_estimado_segundos` |
| R2 | Pitch detection erra muito em técnicas vocais brasileiras (sertanejo, etc.) | Alta | Alto | Aceitar limitação documentada no SKILL.md; permitir usuário marcar "ignorar pitch nesta música" |
| R3 | yt-dlp quebra com mudança da API do YouTube | Média | Médio | Validar versão no setup; bumpar dependência regularmente; aceitar como custo de manutenção |
| R4 | Bibliotecas C-native (aubio, demucs) com problemas de install em distros menos comuns | Média | Médio | Documentar tested distros; CI com matriz de distros Linux populares |
| R5 | JSON schema evolui e quebra CLAUDE.md/SKILL.md silenciosamente | Baixa | Alto | `schema_version` no payload (D7); guard rails no SKILL.md ("se versão > X, peça atualização"); testes que validam compatibilidade |
| R6 | Persona desbalanceada (muito fria ou muito "Duolingo") mata adesão | Alta | Alto | Iteração intensiva na Fase 6 com `copywriter`; revisão manual; ajuste pós-uso real |
| R7 | Captura de áudio com jitter por GIL bloqueando callback | Média | Alto | Callback mínimo (só `queue.put`); processing em thread separada; teste de stress com música longa |
| R8 | Custo de tokens cresce além do estimado em sessões longas | Baixa | Médio | Limitar histórico em prompt (últimos N batches); transparência no README |
| R9 | Cifra Club / Musixmatch mudam APIs/HTML quebrando parsers | Alta | Médio | Cada adaptador é isolado e falha gracefulmente; pipeline cai para próximo passo |
| R10 | Detecção de polifonia gera muitos falsos positivos/negativos | Média | Médio | Usuário pode forçar tipo do trecho via MCP; calibração de threshold por gênero |

---

## 7. Critérios de Validação

### 7.1 Critérios de aceitação do MVP
1. **Setup:** dev consegue rodar `pip install -e .` + `claude mcp add ...` em máquina limpa em ≤5 min.
2. **Fluxo conversacional:** Claude reconhece pedido de música, dispara busca, pede confirmação, prepara gabarito.
3. **Calibração:** primeiro uso pede calibração; perfil persiste em `~/.auladcanto/perfil.json`.
4. **Análise rica:** JSON de batch contém todos os campos do schema v1 com valores plausíveis.
5. **Persistência de histórico:** sessões persistidas; `get_historico` retorna evolução.
6. **Duo:** sistema detecta duo, oferece escolha de voz, e ajusta comparação.
7. **Qualidade do gabarito exposta:** `qualidade_gabarito.nivel` afeta forma como Claude apresenta o feedback.

### 7.2 Plano de testes
- **Unitários:** cada analisador, cada adaptador de busca, comparador (≥80% cobertura).
- **Integração:** pipeline end-to-end com fixture de áudio (música solo, música em duo).
- **Golden tests:** JSON de batch para 3 cenários sintéticos.
- **Contrato MCP:** input/output de cada ferramenta validados contra schema.
- **Manual:** 5-10 sessões reais em 5-10 músicas (mix de pop, MPB, sertanejo).

### 7.3 Métricas de saúde durante uso manual
- Tempo médio de preparação por música (objetivo: <10s para MIDI/cifra, <15min para áudio).
- Taxa de feedback do Claude percebido como útil (subjetivo, log no diário do dev).
- Crashes/erros não tratados (objetivo: 0 em sessão de 30 min).
- Custo médio de tokens por sessão (objetivo: ≤$0.05).

---

## 8. Arquivos a serem criados (visão de pacote)

```
auladcanto/
├── pyproject.toml
├── README.md
├── LICENSE
├── Makefile
├── src/auladcanto/
│   ├── __init__.py
│   ├── cli.py                          # CLI auladcanto / auladcanto-mcp
│   ├── mcp/
│   │   ├── server.py                   # entrypoint MCP via stdio
│   │   └── tools/                      # uma ferramenta por arquivo
│   ├── domain/
│   │   ├── gabarito.py
│   │   ├── batch.py                    # schema v1
│   │   ├── perfil_aluno.py
│   │   ├── preparation/
│   │   │   ├── orchestrator.py         # fallback gracioso
│   │   │   ├── midi_search.py
│   │   │   ├── cifra_search.py
│   │   │   ├── audio_pipeline.py
│   │   │   └── quality.py
│   │   ├── analysis/
│   │   │   ├── capture.py
│   │   │   ├── buffer.py
│   │   │   ├── pitch.py
│   │   │   ├── vibrato.py
│   │   │   ├── respiracao.py
│   │   │   ├── ataque.py
│   │   │   ├── timing.py
│   │   │   └── transposicao.py
│   │   ├── comparator/
│   │   │   ├── aligner.py
│   │   │   └── score.py
│   │   ├── calibration/microfone.py
│   │   └── polifonia.py
│   ├── storage/
│   │   ├── paths.py                    # resolve ~/.auladcanto/
│   │   ├── cache.py                    # cache por hash de áudio
│   │   ├── historico.py
│   │   └── perfil.py
│   └── templates/
│       ├── SKILL.md
│       └── CLAUDE.md
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── golden/
│   └── fixtures/musicas/               # 5-10 músicas pequenas de teste
└── docs/
    ├── schema-v1.md
    ├── arquitetura.md
    └── decisoes.md                     # ADRs equivalentes à matriz D1..D15
```

---

## 9. Como verificar/executar quando implementado

```bash
# Setup
git clone <repo> auladcanto
cd auladcanto
pip install -e ".[dev]"
auladcanto init                         # cria ~/.auladcanto/ e templates

# Registro no Claude Code
claude mcp add auladcanto -- auladcanto-mcp

# Verificar deps de sistema
auladcanto verificar-deps               # confirma ffmpeg, sounddevice

# Calibração inicial
auladcanto calibrar

# Uso normal — abrir Claude Code em qualquer diretório com SKILL.md na sessão:
# "quero aprender Garota de Ipanema"
# Claude → buscar_musica → confirmar_download → preparar_gabarito
# "vamos praticar, voz"
# Claude → iniciar_sessao(modo="voz")
# [usuário canta]
# Claude → get_batch_atual → dá feedback contextualizado

# Testes
make test                               # unit + integration
make test-golden                        # JSON snapshots
make test-e2e                           # com fixtures de áudio
make lint                               # ruff + mypy
```

---

## 10. Próximos passos imediatos (se este plano for aprovado)

1. Aprovar este plano via Maestro (ExitPlanMode → resposta do usuário).
2. Sair de Plan Mode; Maestro cria sessão e dispara Fase 0 via `devops-engineer`.
3. Após Fase 0, Maestro decide execução paralela das Fases 1, 2A independentes (e sequencial das demais conforme dependências).
4. Code review obrigatório ao final de cada fase via `code-reviewer` (gate Critical/Major).
