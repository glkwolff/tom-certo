# auladcanto-mcp

> **Status:** in development (phase 0 of 12 — project bootstrap).

A local MCP (Model Context Protocol) server that turns Claude Code into a
personal music tutor for voice and guitar. Audio is captured and analyzed on
your machine; only structured JSON metadata is sent to the Claude API.

## Installation

```bash
# Editable install with dev tooling (lint, type-check, tests):
pip install -e ".[dev]"

# Optional: audio analysis stack (heavier dependencies):
pip install -e ".[audio]"

# Optional: MCP protocol SDK:
pip install -e ".[mcp]"

# Register the MCP server with Claude Code:
claude mcp add auladcanto -- auladcanto-mcp
```

## Quickstart

```bash
auladcanto --help              # list subcommands
auladcanto --version           # print version
auladcanto init                # provision ~/.auladcanto/  (phase 7)
auladcanto calibrar            # microphone calibration   (phase 7)
auladcanto mcp-server          # run MCP server           (phase 5)
auladcanto verificar-deps      # check ffmpeg, demucs, …  (phase 7)
auladcanto limpar-cache        # prune cached gabaritos   (phase 7)
```

## Project layout

```
src/auladcanto/
  cli.py            # Typer CLI (entry point: `auladcanto`)
  mcp/server.py     # MCP server stub (entry point: `auladcanto-mcp`)
  domain/           # gabarito, analysis, comparator (future phases)
  storage/paths.py  # ~/.auladcanto/ path helpers
tests/
  unit/             # fast, isolated tests
  integration/      # touch filesystem, no network
  golden/           # regression fixtures
```

## Documentation

- Architecture overview: [`docs/architecture.md`](docs/architecture.md)
- Source-of-truth design plan: [`docs/maestro/plans/auladcanto-mcp-mvp.md`](docs/maestro/plans/auladcanto-mcp-mvp.md)

## License

MIT — see [LICENSE](LICENSE).
# tom-certo
