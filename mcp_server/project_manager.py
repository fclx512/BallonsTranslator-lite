"""MCP Server project lifecycle management.

Maintains the active ProjImgTrans project in memory for the duration of
an MCP session. Tool handlers acquire the project via get_active_project().
"""

import logging
from typing import Optional

from utils.proj_imgtrans import ProjImgTrans

logger = logging.getLogger("mcp_server.project")

_active_project: Optional[ProjImgTrans] = None
_active_path: Optional[str] = None


def open_project(directory: str) -> dict:
    """Load a BallonsTranslator project from disk into memory.

    Args:
        directory: Path to the project directory (contains images + project JSON).

    Returns:
        Project index summary — lightweight page overview (same format as list_pages).
    """
    global _active_project, _active_path

    # Close any previously open project first
    close_project()

    proj = ProjImgTrans(directory)  # __init__ calls load()
    _active_project = proj
    _active_path = directory

    logger.info("Opened project: %s (%d pages)", directory, len(proj.pages))

    from utils.proj_compact import build_index

    return build_index(proj)


def get_active_project() -> ProjImgTrans:
    """Get the currently loaded project.

    Raises:
        RuntimeError: If no project has been opened yet.
    """
    if _active_project is None:
        raise RuntimeError(
            "No project is currently open. Call open_project first."
        )
    return _active_project


def save_project() -> dict:
    """Write the current project state to disk.

    Returns:
        Dict with status and saved path.
    """
    proj = get_active_project()
    proj.save()
    logger.info("Saved project to: %s", _active_path)
    return {"status": "saved", "path": _active_path}


def close_project() -> dict:
    """Unload the current project and release memory.

    Returns:
        Dict with status and previously active path.
    """
    global _active_project, _active_path
    path = _active_path
    _active_project = None
    _active_path = None
    if path:
        logger.info("Closed project: %s", path)
    return {"status": "closed", "path": path}


def get_state() -> dict:
    """Return the current server/active-project state.

    Returns:
        Dict indicating whether a project is open, its path, page count.
    """
    if _active_project is None:
        return {"project_open": False}
    return {
        "project_open": True,
        "path": _active_path,
        "page_count": len(_active_project.pages),
    }
