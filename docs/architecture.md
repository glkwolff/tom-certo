# Architecture Overview

> **Status:** stub. The authoritative design document is
> [`docs/maestro/plans/auladcanto-mcp-mvp.md`](maestro/plans/auladcanto-mcp-mvp.md).
> This file will be expanded as the implementation crystallizes.

## High-level layout

`auladcanto-mcp` is a single Python process exposing MCP tools to Claude Code
over stdio. The codebase follows a three-layer separation:

```
src/auladcanto/
  cli.py        # Typer CLI (`auladcanto` console script)
  mcp/
    server.py   # MCP server (`auladcanto-mcp` console script)
    tools/      # one module per MCP tool (phase 5+)
  domain/       # business logic (gabarito, analysis, comparator) — phase 1+
  storage/      # JSON persistence under ~/.auladcanto/
```

Tests mirror that structure under `tests/unit`, `tests/integration`, and
`tests/golden`.

## Key invariants

- **Local-first.** Audio never leaves the user's machine; only JSON metadata
  travels to the Claude API.
- **No network in tests.** Integration tests touch the filesystem only.
- **JSON on disk.** Persistence is plain JSON files under `~/.auladcanto/`;
  no SQLite, no external database.
- **Python ≥ 3.11.** Modern type hints (`X | Y`), `asyncio.TaskGroup`, etc.

## Where to look next

- Roadmap and per-phase deliverables: see the source-of-truth plan linked
  above (§5–§7 cover the 12-phase MVP).
- Public CLI surface: [`src/auladcanto/cli.py`](../src/auladcanto/cli.py).
- Path conventions: [`src/auladcanto/storage/paths.py`](../src/auladcanto/storage/paths.py).
