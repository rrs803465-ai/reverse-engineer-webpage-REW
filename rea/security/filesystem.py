"""Filesystem safety: keep generated files inside approved workspaces."""

from __future__ import annotations

import os
from pathlib import Path


class WorkspaceBoundaryError(PermissionError):
    """Raised when a path escapes the approved workspace."""


def enforce_workspace(path: Path | str, workspace: Path | str) -> Path:
    """Resolve *path* and verify it is inside *workspace*.

    Returns the resolved Path if safe; raises WorkspaceBoundaryError otherwise.
    """
    resolved = Path(path).resolve()
    ws = Path(workspace).resolve()

    try:
        resolved.relative_to(ws)
    except ValueError:
        raise WorkspaceBoundaryError(
            f"Path '{resolved}' is outside the approved workspace '{ws}'."
        )
    return resolved


def safe_write(path: Path | str, content: str | bytes, workspace: Path | str) -> Path:
    """Write *content* to *path* after verifying it is inside *workspace*."""
    safe_path = enforce_workspace(path, workspace)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"
    with open(safe_path, mode, encoding=encoding) as fh:
        fh.write(content)
    return safe_path


def safe_read(path: Path | str, workspace: Path | str) -> str:
    """Read a text file after verifying it is inside *workspace*."""
    safe_path = enforce_workspace(path, workspace)
    return safe_path.read_text(encoding="utf-8")


def restrict_permissions(path: Path | str) -> None:
    """Set file to owner-read/write only (600) where supported."""
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass
