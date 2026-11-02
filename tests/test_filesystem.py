"""Tests for workspace boundary enforcement."""

import pytest
from pathlib import Path
from rea.security.filesystem import WorkspaceBoundaryError, enforce_workspace, safe_write


def test_safe_path_inside_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    result = enforce_workspace(ws / "output.html", ws)
    assert result == ws / "output.html"


def test_blocks_path_traversal(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(WorkspaceBoundaryError, match="outside"):
        enforce_workspace(ws / ".." / ".." / "etc" / "passwd", ws)


def test_blocks_absolute_escape(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(WorkspaceBoundaryError):
        enforce_workspace(Path("/etc/passwd"), ws)


def test_safe_write_creates_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    path = safe_write(ws / "subdir" / "test.html", "<html></html>", ws)
    assert path.exists()
    assert path.read_text() == "<html></html>"


def test_safe_write_blocks_escape(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(WorkspaceBoundaryError):
        safe_write(tmp_path / "escaped.txt", "bad", ws)


def test_nested_path_inside_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    nested = ws / "a" / "b" / "c" / "file.js"
    result = enforce_workspace(nested, ws)
    assert result == nested.resolve()
