"""MCP server entry point.

Bound to the ``auladcanto-mcp`` console script. The real implementation
arrives in phase 5; for now this prints a notice and exits cleanly so the
console-script wiring can be validated end-to-end during bootstrap.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Stub entry point. Returns ``0`` so smoke tests can shell out without failing."""
    sys.stdout.write("auladcanto-mcp server: not implemented yet (phase 5)\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover — module entry point
    raise SystemExit(main())
