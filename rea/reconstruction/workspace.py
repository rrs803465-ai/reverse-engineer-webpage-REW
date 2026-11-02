"""Project workspace: manage generated file output within approved boundaries."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

from rea.security.filesystem import enforce_workspace, safe_write

log = logging.getLogger(__name__)


class Workspace:
    """Manages a single project's generated-file output directory."""

    def __init__(self, root: Path, project_id: str) -> None:
        self.root = root / project_id
        self.root.mkdir(parents=True, exist_ok=True)
        log.debug("Workspace at %s", self.root)

    def write(self, relative_path: str, content: str | bytes) -> Path:
        """Write *content* to *relative_path* inside the workspace."""
        target = self.root / relative_path
        return safe_write(target, content, self.root)

    def read(self, relative_path: str) -> str:
        """Read a text file from the workspace."""
        target = enforce_workspace(self.root / relative_path, self.root)
        return target.read_text(encoding="utf-8")

    def exists(self, relative_path: str) -> bool:
        return (self.root / relative_path).exists()

    def list_files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*") if p.is_file())

    def content_hash(self, relative_path: str) -> str:
        target = enforce_workspace(self.root / relative_path, self.root)
        return hashlib.sha256(target.read_bytes()).hexdigest()[:20]

    def backup_if_exists(self, relative_path: str) -> Path | None:
        """Copy an existing file to a .bak before overwriting."""
        target = self.root / relative_path
        if target.exists():
            bak = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, bak)
            log.debug("Backed up %s → %s", target.name, bak.name)
            return bak
        return None

    def clean(self) -> None:
        """Remove all files in the workspace (use with explicit confirmation)."""
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative_path: str = "") -> Path:
        return self.root / relative_path

    def relative(self, abs_path: Path) -> str:
        return str(abs_path.relative_to(self.root))
