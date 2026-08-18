"""Unit tests for gateway.file_renderers."""

from pathlib import Path
import pytest
from gateway.file_renderers import (
    format_file_size,
    highlight_code_syntax,
    is_binary_file,
    render_code_template,
    render_csv_template,
    render_diff_template,
    render_file_to_html,
    render_markdown_template,
)


def test_format_file_size():
    assert format_file_size(200) == "200 B"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(1024 * 1024 * 5) == "5.0 MB"


def test_is_binary_file(tmp_path):
    # Text file
    txt_file = tmp_path / "hello.txt"
    txt_file.write_text("hello world", encoding="utf-8")
    assert is_binary_file(txt_file) is False

    # Binary file by extension
    png_file = tmp_path / "image.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert is_binary_file(png_file) is True

    # Binary file by null bytes content
    bin_file = tmp_path / "data.dat"
    bin_file.write_bytes(b"some\x00data\x00here")
    assert is_binary_file(bin_file) is True


def test_render_code_file_to_html(tmp_path):
    py_file = tmp_path / "test_app.py"
    py_file.write_text(
        "import os\n\ndef main():\n    # say hello\n    msg = 'Hello World'\n    return 42\n",
        encoding="utf-8",
    )

    is_conv, html_out, out_name = render_file_to_html(py_file, rel_path="test_app.py")
    assert is_conv is True
    assert out_name == "test_app.py.html"
    assert "<!DOCTYPE html>" in html_out
    assert "test_app.py" in html_out
    assert "hl-kw" in html_out  # Keyword highlighting
    assert "line-num" in html_out
    assert "Copy Code" in html_out


def test_render_markdown_to_html(tmp_path):
    md_file = tmp_path / "README.md"
    md_file.write_text(
        "# My Project\n\nThis is a **bold** description.\n\n```python\nprint('hello')\n```\n",
        encoding="utf-8",
    )

    is_conv, html_out, out_name = render_file_to_html(md_file, rel_path="README.md")
    assert is_conv is True
    assert out_name == "README.md.html"
    assert "<!DOCTYPE html>" in html_out
    assert "My Project" in html_out
    assert "<strong>bold</strong>" in html_out
    assert "print('hello')" in html_out


def test_render_diff_to_html(tmp_path):
    diff_file = tmp_path / "patch.diff"
    diff_file.write_text(
        "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@\n-old line\n+new line\n",
        encoding="utf-8",
    )

    is_conv, html_out, out_name = render_file_to_html(diff_file, rel_path="patch.diff")
    assert is_conv is True
    assert out_name == "patch.diff.html"
    assert "diff-add" in html_out
    assert "diff-sub" in html_out


def test_render_csv_to_html(tmp_path):
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("id,name,score\n1,Alice,95\n2,Bob,88\n", encoding="utf-8")

    is_conv, html_out, out_name = render_file_to_html(csv_file, rel_path="data.csv")
    assert is_conv is True
    assert out_name == "data.csv.html"
    assert "Alice" in html_out
    assert "Bob" in html_out
    assert "<thead>" in html_out


def test_render_binary_file_returns_false(tmp_path):
    bin_file = tmp_path / "logo.png"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    is_conv, html_out, out_name = render_file_to_html(bin_file)
    assert is_conv is False
    assert html_out == ""
    assert out_name == "logo.png"
