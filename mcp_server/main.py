#!/usr/bin/env python3
"""BallonsTranslator-lite MCP Server.

Exposes project manipulation tools via the Model Context Protocol (MCP).
Designed to be used as a subprocess by MCP clients such as Claude Code.

Usage:
    python -m mcp_server
    # or directly:
    python mcp_server/main.py
"""

import argparse
import logging
import sys
import os

# Ensure project root is on sys.path so utils.* imports resolve
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def main():
    parser = argparse.ArgumentParser(
        description="BallonsTranslator MCP Server — expose project tools via MCP"
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format=LOG_FORMAT)
    logging.getLogger("mcp_server").setLevel(getattr(logging, args.log_level))

    # Initialize pcfg singleton — needed by get_config tool which reads
    # pcfg.global_fontformat, pcfg.module.lang_source, lang_target, etc.
    import utils.config as program_config

    program_config.load_config()
    logger = logging.getLogger("mcp_server")
    logger.info("pcfg initialized")

    # Build and run the MCP server
    from mcp.server.fastmcp import FastMCP
    from mcp_server.tools import register_all_tools

    mcp = FastMCP("ballonstranslator-mcp")

    register_all_tools(mcp)

    logger.info("Starting MCP server (stdio transport)...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
