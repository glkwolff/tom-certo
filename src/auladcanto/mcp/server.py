"""MCP server entry point — wires the 11 tools shipped in phase 5.

The ``mcp`` SDK is an optional dependency (see ``[project.optional-dependencies].mcp``
in ``pyproject.toml``); importing this module without the SDK installed must
still succeed so the rest of the package (CLI, tests) keeps working. The SDK
imports therefore live inside :func:`_load_mcp` and surface a friendly error
through :func:`main` when missing.

The console-script ``auladcanto-mcp`` (declared in ``[project.scripts]``)
targets :func:`main` directly — keep the signature ``def main() -> int`` so the
wiring stays stable.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any

from auladcanto.mcp.tools import musica as musica_tools
from auladcanto.mcp.tools import perfil as perfil_tools
from auladcanto.mcp.tools import sessao as sessao_tools


@dataclass(frozen=True)
class _ToolSpec:
    """Static metadata for one tool exposed over MCP."""

    name: str
    description: str
    input_schema: dict[str, Any]


TOOL_SPECS: list[_ToolSpec] = [
    _ToolSpec(
        name="buscar_musica",
        description="Search YouTube candidates for a free-text query (returns up to 3).",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
    ),
    _ToolSpec(
        name="confirmar_download",
        description="Confirm a candidate and prepare its gabarito (cached on success).",
        input_schema={
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "titulo": {"type": "string"},
                "artista": {"type": "string"},
            },
            "required": ["video_id"],
        },
    ),
    _ToolSpec(
        name="verificar_cache",
        description="Check whether a musica_id has a ready gabarito in the cache.",
        input_schema={
            "type": "object",
            "properties": {"musica_id": {"type": "string"}},
            "required": ["musica_id"],
        },
    ),
    _ToolSpec(
        name="preparar_gabarito",
        description="Search + confirm in one call when (titulo, artista) are known.",
        input_schema={
            "type": "object",
            "properties": {
                "titulo": {"type": "string"},
                "artista": {"type": "string"},
            },
            "required": ["titulo", "artista"],
        },
    ),
    _ToolSpec(
        name="iniciar_sessao",
        description="Start a live practice session for a cached musica_id.",
        input_schema={
            "type": "object",
            "properties": {
                "musica_id": {"type": "string"},
                "modo": {"type": "string", "enum": ["voz", "violao", "ambos"]},
                "voz_escolhida": {
                    "type": "string",
                    "enum": ["aguda", "grave", "solo", "n/a"],
                },
            },
            "required": ["musica_id", "modo"],
        },
    ),
    _ToolSpec(
        name="pausar_sessao",
        description="Stop the current session and persist its state under sessoes_dir.",
        input_schema={"type": "object", "properties": {}},
    ),
    _ToolSpec(
        name="get_batch_atual",
        description="Return the most recent closed batch report for the active session.",
        input_schema={"type": "object", "properties": {}},
    ),
    _ToolSpec(
        name="get_contexto_sessao",
        description="Return every batch accumulated so far in the active session.",
        input_schema={"type": "object", "properties": {}},
    ),
    _ToolSpec(
        name="get_perfil_aluno",
        description="Return the persisted student profile (creates a default if missing).",
        input_schema={"type": "object", "properties": {}},
    ),
    _ToolSpec(
        name="get_historico",
        description="Return aggregate progress data for a given musica_id.",
        input_schema={
            "type": "object",
            "properties": {"musica_id": {"type": "string"}},
            "required": ["musica_id"],
        },
    ),
    _ToolSpec(
        name="calibrar_microfone",
        description="Run the four-step microphone calibration and update the profile.",
        input_schema={"type": "object", "properties": {}},
    ),
]


async def _dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route a tool invocation to the underlying tool function."""
    if name == "buscar_musica":
        return await musica_tools.buscar_musica(
            query=str(arguments.get("query", "")),
            limit=int(arguments.get("limit", 3)),
        )
    if name == "confirmar_download":
        return await musica_tools.confirmar_download(
            video_id=str(arguments.get("video_id", "")),
            titulo=arguments.get("titulo"),
            artista=arguments.get("artista"),
        )
    if name == "verificar_cache":
        return musica_tools.verificar_cache(str(arguments.get("musica_id", "")))
    if name == "preparar_gabarito":
        return await musica_tools.preparar_gabarito(
            titulo=str(arguments.get("titulo", "")),
            artista=str(arguments.get("artista", "")),
        )
    if name == "iniciar_sessao":
        return await sessao_tools.iniciar_sessao(
            musica_id=str(arguments.get("musica_id", "")),
            modo=arguments.get("modo", "voz"),
            voz_escolhida=arguments.get("voz_escolhida", "n/a"),
        )
    if name == "pausar_sessao":
        return await sessao_tools.pausar_sessao()
    if name == "get_batch_atual":
        return sessao_tools.get_batch_atual()
    if name == "get_contexto_sessao":
        return sessao_tools.get_contexto_sessao()
    if name == "get_perfil_aluno":
        return perfil_tools.get_perfil_aluno()
    if name == "get_historico":
        return perfil_tools.get_historico(str(arguments.get("musica_id", "")))
    if name == "calibrar_microfone":
        return await perfil_tools.calibrar_microfone()
    raise ValueError(f"unknown MCP tool: '{name}'")


def main() -> int:
    """Console-script entrypoint. Returns ``0`` on clean exit, ``1`` on missing SDK."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError:
        print(
            "auladcanto-mcp requires the 'mcp' extra. Install with: pip install -e '.[mcp]'",
            file=sys.stderr,
        )
        return 1

    server = Server("auladcanto-mcp")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_schema,
            )
            for spec in TOOL_SPECS
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await _dispatch(name, arguments)
        except Exception as exc:  # surface errors to the client instead of crashing
            result = {"status": "error", "erro": str(exc)}
        payload = json.dumps(result, ensure_ascii=False, default=str)
        return [TextContent(type="text", text=payload)]

    async def _run_server() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_run_server())
    return 0


if __name__ == "__main__":  # pragma: no cover — module entry point
    raise SystemExit(main())
