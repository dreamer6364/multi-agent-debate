"""MCP (Model Context Protocol) server frontend.

Exposes elenchus as tools any MCP client (Claude Desktop, Claude Code, IDEs) can call:
  - analyze(text, check, sensitivity)
  - list_checks()

Run:  elenchus mcp     (stdio transport)
"""
from __future__ import annotations


def build_server(engine=None, config_path: str | None = None):
    """Construct and return the FastMCP server (kept separate from run() for testing)."""
    from mcp.server.fastmcp import FastMCP

    from ..core.checks import CHECKS
    from ..core.engine import Engine
    from ..core.errors import friendly_error

    if engine is None:
        from ..core.config import load_config

        engine = Engine(load_config(config_path))

    mcp = FastMCP("elenchus")

    @mcp.tool()
    def analyze(text: str, check: str = "fallacy", sensitivity: str | None = None) -> dict:
        """Cross-examine text for logical fallacies or manipulation.

        Args:
            text: The message or argument to analyze.
            check: Which check to run — "fallacy" (logical fallacies) or "scam"
                (scam / manipulation red flags).
            sensitivity: Optional override — "lenient", "balanced", or "strict".

        Returns a dict with `overall` (one-sentence summary) and `findings`
        (each: name, quote, explanation, confidence).
        """
        try:
            return engine.analyze(text, check, sensitivity)
        except Exception as exc:  # noqa: BLE001
            return {"overall": friendly_error(exc), "findings": [], "error": True}

    @mcp.tool()
    def list_checks() -> dict:
        """List available checks and the techniques each one looks for."""
        return {
            cid: {
                "title": c.title,
                "techniques": [{"id": t.id, "name": t.name, "definition": t.definition} for t in c.techniques],
            }
            for cid, c in CHECKS.items()
        }

    return mcp


def main(config_path: str | None = None) -> None:
    try:
        server = build_server(config_path=config_path)
    except ImportError:
        raise SystemExit("MCP support needs the extra:  pip install 'elenchus[mcp]'")
    server.run()  # stdio transport
