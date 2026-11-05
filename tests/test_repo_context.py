"""Tests for tools/repo_context.py cross-platform Merkle script."""

from __future__ import annotations

import json
from pathlib import Path

from tools.repo_context import compute_merkle, text_sha256


def test_text_sha256() -> None:
    # SHA-256 of empty string
    assert text_sha256("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert text_sha256("test") == "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


def test_compute_merkle_structure(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    result = compute_merkle(repo_root)

    assert result["schema_version"] == 1
    assert "root_sha256" in result
    assert result["file_count"] > 0
    assert "AGENTS.md" in result["files"]
    assert "." in result["directories"]
    assert len(result["root_sha256"]) == 64
