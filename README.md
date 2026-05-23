# auladcanto-mcp

Tutor musical local (canto + violão) integrado ao Claude Code via MCP.

## O que é

Você diz "quero aprender Wish You Were Here" dentro do Claude Code. O
`auladcanto-mcp` busca a música no YouTube, confirma a versão correta com
você, prepara um gabarito (melodia, acordes e letra alinhada) e abre uma
sessão de prática. A cada 30 segundos o servidor lê o que o microfone
captou, mede afinação, ritmo, respiração e volume, e devolve um JSON para
o Claude. O Claude lê esse JSON e te dá feedback como um professor
experiente — uma instrução física por vez, sempre conectando o número ao
corpo. O áudio nunca sai da sua máquina; só metadados estruturados sobem
para a API.

## Instalação

1. Instale o pacote (de preferência num virtualenv dedicado):

   ```bash
   pip install -e ".[mcp,audio]"
   # ou, quando publicado no PyPI:
   pip install "auladcanto-mcp[mcp,audio]"
   ```

2. Garanta as dependências de sistema:

   ```bash
   sudo apt install ffmpeg          # Debian/Ubuntu
   sudo dnf install ffmpeg          # Fedora
   sudo pacman -S ffmpeg            # Arch
   brew install ffmpeg              # macOS
   choco install ffmpeg             # Windows
   ```

3. Provisione o diretório de estado e os templates do Claude:

   ```bash
   auladcanto init
   ```

   O comando cria `~/.auladcanto/` com subpastas (`cache/`, `sessoes/`,
   `historico/`), copia `SKILL.md` para `~/.auladcanto/SKILL.md` e pergunta
   se quer copiar `CLAUDE.md` para o diretório atual.

4. Calibre o microfone (obrigatório no primeiro uso):

   ```bash
   auladcanto calibrar
   ```

5. Registre o servidor MCP no Claude Code:

   ```bash
   claude mcp add auladcanto -- auladcanto-mcp
   ```

6. Abra o Claude Code e fale naturalmente — por exemplo: "bora estudar
   `Wish You Were Here`". O Claude detecta a intenção pela skill, chama
   `buscar_musica`, e o fluxo segue daí.

## Uso típico

```
você  : quero aprender Wish You Were Here
claude: achei três versões — estúdio (Pink Floyd, 5:34),
        ao vivo Pulse (6:32) e acústica do David Gilmour (5:48).
        Qual delas?
você  : a primeira
claude: ok, preparando o gabarito... pronto.
        Tenho melodia, acordes e letra alinhada — gabarito completo.
        Você quer praticar voz, violão ou os dois?
você  : voz
claude: microfone captando. Pode começar quando quiser.
        A cada meio minuto eu olho o que aconteceu e te falo uma coisa.

   (...30 segundos depois...)

claude: a afinação no primeiro verso ficou estável, mas você acelerou
        seis BPM perto do refrão. Costuma acontecer quando o ar começa
        a faltar e o corpo apressa para terminar.
        No próximo, marca a batida com o pé — só o pé, sem pensar.
```

## Comandos CLI

| Comando                    | O que faz                                                                       |
|----------------------------|---------------------------------------------------------------------------------|
| `auladcanto init`          | Cria `~/.auladcanto/`, copia `SKILL.md` e (opcional) `CLAUDE.md` no CWD.        |
| `auladcanto calibrar`      | Roda a calibração de 4 passos do microfone e persiste no `perfil.json`.         |
| `auladcanto mcp-server`    | Sobe o servidor MCP no foreground (alternativa ao console script).              |
| `auladcanto verificar-deps`| Checa `ffmpeg`, `yt-dlp` e os módulos opcionais (`sounddevice`, `mcp`, etc).    |
| `auladcanto limpar-cache`  | Apaga `cache/` e `sessoes/` (preserva `perfil.json`, `historico/`, templates).  |

Veja `auladcanto --help` para a lista completa.

## Estado e privacidade

- Áudio bruto fica em `~/.auladcanto/cache/` durante a preparação e é
  descartado depois — nunca trafega pela internet.
- Apenas metadados JSON (afinação em cents, BPM medido, contagem de
  respiros, etc.) sobem para a API do Claude. Centavos de dólar por sessão.
- Estado persistido localmente em `~/.auladcanto/`:
  - `perfil.json` — calibração, faixa vocal, preferências.
  - `cache/` — gabaritos prontos, indexados por `musica_id`.
  - `sessoes/` — sessões em andamento e recém-encerradas.
  - `historico/` — agregados de progresso por música.
- `AULADCANTO_HOME` permite mudar a raiz (útil para testes / múltiplos
  perfis na mesma máquina).

## Limitações conhecidas

- **Linux apenas no MVP.** macOS e Windows estão no roadmap pós-MVP — o
  pipeline depende de `sounddevice`/PortAudio + `ffmpeg`, que rodam nos
  três sistemas, mas o ciclo de teste do MVP cobre só Linux.
- **Música em duo** (Bruno e Marrone, Chitãozinho e Xororó, etc.): a
  afinação medida é aproximada porque o `aubio`/`crepe` rastreia uma única
  voz por vez. A skill pergunta qual voz você está cantando antes de
  avaliar.
- **Pipeline de áudio é lento** quando não existem MIDI/cifra
  pré-processados: a extração com `demucs` + `basic-pitch` leva entre
  **15 e 40 minutos** em CPU típica. A skill avisa antes de começar.
- **`aubio` ainda não compila em Python 3.13+**: o pacote continua
  funcionando, mas algumas features de pitch caem para um fallback em
  numpy puro (menos preciso). Em Python 3.11 / 3.12 a stack completa
  funciona.
- O microfone precisa estar calibrado antes da primeira sessão. Sem isso,
  `volume.media_normalizada` e a detecção de respiração ficam enviesadas
  e o feedback perde a calibração com seu setup.

## Arquitetura

- Plano completo de design: [`docs/maestro/plans/auladcanto-mcp-mvp.md`](docs/maestro/plans/auladcanto-mcp-mvp.md).
- Schema v1 dos artefatos JSON (gabarito, BatchReport, perfil): [`docs/schema-v1.md`](docs/schema-v1.md).
- Visão geral da arquitetura: [`docs/architecture.md`](docs/architecture.md).

## Custos

A única conta que você paga é a da API do Claude. Em Sonnet, uma sessão
de prática de 3 minutos consome em torno de **US$ 0,04** em tokens (a maior
parte é o gabarito + os BatchReports enviados a cada 30s). Estudando
1 hora por dia, fica em ~**US$ 24/mês**. Não existe servidor próprio para
hospedar — tudo roda na sua máquina.

## Licença

MIT — veja [LICENSE](LICENSE).
