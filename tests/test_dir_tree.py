"""Unit tests for gateway.dir_tree scanner and HTML generator."""

from pathlib import Path
import pytest
from gateway.dir_tree import (
    format_size,
    generate_dir_tree_html,
    get_file_icon,
    scan_directory_tree,
)


def test_format_size():
    assert format_size(500) == "500 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(1024 * 1024 * 3) == "3.0 MB"
    assert format_size(1024 * 1024 * 1024 * 2) == "2.00 GB"


def test_get_file_icon():
    assert get_file_icon("main.py") == "🐍"
    assert get_file_icon("README.md") == "📄"
    assert get_file_icon("config.json") == "⚙️"
    assert get_file_icon("index.html") == "🌐"
    assert get_file_icon(".env.local") == "🔒"
    assert get_file_icon("archive.zip") == "📦"
    assert get_file_icon("unknown_file.xyz") == "📄"


def test_scan_directory_tree(tmp_path):
    # Setup test folder structure
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('app')", encoding="utf-8")
    (src / "utils.py").write_text("def helper(): pass", encoding="utf-8")

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide", encoding="utf-8")

    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=123", encoding="utf-8")

    # Ignored directory
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("git config", encoding="utf-8")

    scan_res = scan_directory_tree(tmp_path)
    assert scan_res["exists"] is True
    assert scan_res["total_files"] == 5  # app.py, utils.py, guide.md, README.md, .env (.git ignored)
    assert scan_res["total_dirs"] == 2   # src, docs (.git ignored)
    assert scan_res["total_size"] > 0

    # Non-existent dir
    non_existent = scan_directory_tree(tmp_path / "does_not_exist")
    assert non_existent["exists"] is False
    assert non_existent["total_files"] == 0


def test_generate_dir_tree_html(tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "core.py").write_text("code", encoding="utf-8")
    (tmp_path / "index.js").write_text("console.log('hi');", encoding="utf-8")

    html_out = generate_dir_tree_html(tmp_path, project_name="my_test_proj")
    assert "<!DOCTYPE html>" in html_out
    assert "my_test_proj" in html_out
    assert "core.py" in html_out
    assert "index.js" in html_out
    assert "/file pkg/core.py" in html_out
    assert "/file index.js" in html_out
    assert "copyFileCmd" in html_out
    assert "filterTree" in html_out
    assert "expandAll" in html_out
    assert "collapseAll" in html_out
