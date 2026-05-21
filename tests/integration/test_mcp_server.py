"""Integration test for the MCP server entrypoint.

Verifies two complementary behaviours of :func:`auladcanto.mcp.server.main`:

* When the ``mcp`` extra is installed, ``main`` registers all 11 tool specs
  via :data:`TOOL_SPECS` without hanging — we patch the ``mcp.server`` boundary
  so we never actually open stdio.
* When the ``mcp`` extra is *not* installed, ``main`` returns ``1`` and prints
  a remediation hint pointing at the optional dependency.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import patch

import pytest

from auladcanto.mcp import server as server_module


@pytest.mark.integration
def test_main_returns_1_and_mentions_extra_when_mcp_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If ``mcp`` is not importable, ``main()`` exits with code 1 and a hint."""
    real_mcp_server = sys.modules.get("mcp.server")

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("mcp"):
            raise ImportError(f"forced missing: {name}")
        return importlib.__import__(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fake_import):
        exit_code = server_module.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[mcp]" in captured.err

    # Sanity: restore the real module reference for downstream tests.
    if real_mcp_server is not None:
        sys.modules["mcp.server"] = real_mcp_server


@pytest.mark.integration
def test_main_completes_when_mcp_present_with_stubbed_stdio() -> None:
    """When the SDK is installed, ``main`` reaches the stdio loop without hanging."""
    pytest.importorskip("mcp.server")

    with (
        patch.object(server_module.asyncio, "run", return_value=None) as run_mock,
    ):
        exit_code = server_module.main()

    assert exit_code == 0
    run_mock.assert_called_once()

    assert len(server_module.TOOL_SPECS) == 11
    names = {spec.name for spec in server_module.TOOL_SPECS}
    assert "buscar_musica" in names
    assert "calibrar_microfone" in names
