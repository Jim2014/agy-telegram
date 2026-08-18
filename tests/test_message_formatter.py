"""Unit tests for gateway.message_formatter."""

from gateway.message_formatter import (
    AgyFileBlock,
    balance_telegram_html,
    detect_content_mode,
    escape_html,
    extract_agy_file_blocks,
    format_final_result,
    format_markdown_tables,
    format_streaming_message,
    format_tool_summary,
    generate_standalone_html,
    markdown_to_telegram_html,
    parse_agy_file_block,
    render_agy_file,
    sanitize_filename,
    should_export_as_file,
    split_text_chunks,
    wrap_raw_html_for_mobile,
)


def test_escape_html():
    assert escape_html("Hello <world> & 'friends'") == "Hello &lt;world&gt; &amp; 'friends'"
    assert escape_html("") == ""


def test_format_tool_summary():
    # Active run_command
    t1 = format_tool_summary("run_command", "ACTIVE", {"parameters": {"CommandLine": "git status"}})
    assert "run_command" in t1
    assert "git status" in t1
    assert "⚙️" in t1

    # Done view_file
    t2 = format_tool_summary("view_file", "DONE", {"parameters": {"AbsolutePath": "/a/b/c.py"}})
    assert "c.py" in t2
    assert "✅" in t2

    # Error tool
    t3 = format_tool_summary("list_dir", "ERROR", {"error": {"message": "Permission denied"}})
    assert "list_dir failed" in t3
    assert "Permission denied" in t3
    assert "⚠️" in t3


def test_balance_telegram_html():
    unclosed = "<b>bold <i>italic and <code>code"
    balanced = balance_telegram_html(unclosed)
    assert balanced == "<b>bold <i>italic and <code>code</code></i></b>"

    already_balanced = "<pre><code>x = 1;</code></pre>"
    assert balance_telegram_html(already_balanced) == already_balanced


def test_markdown_to_telegram_html():
    # 1. Bold & Italic & Strikethrough
    md1 = "This is **bold** and *italic* and ~~deleted~~."
    html1 = markdown_to_telegram_html(md1)
    assert "<b>bold</b>" in html1
    assert "<i>italic</i>" in html1
    assert "<s>deleted</s>" in html1

    # 2. Code blocks with language and inline code
    md2 = "Use `x < 5`:\n```python\nif a < b:\n    return '<b>tag</b>'\n```"
    html2 = markdown_to_telegram_html(md2)
    assert "<code>x &lt; 5</code>" in html2
    assert '<pre><code class="language-python">' in html2
    assert "if a &lt; b:" in html2
    assert "&lt;b&gt;tag&lt;/b&gt;" in html2

    # 3. Headings, Links, Quotes, Lists
    md3 = "### Header\n> Quote line\n- Item 1\n[Google](https://google.com)"
    html3 = markdown_to_telegram_html(md3)
    assert "<b>Header</b>" in html3
    assert "<blockquote>Quote line</blockquote>" in html3
    assert "• Item 1" in html3
    assert '<a href="https://google.com">Google</a>' in html3


def test_format_markdown_tables():
    tbl = "| Col 1 | Col 2 |\n|---|---|\n| A | B |"
    res = format_markdown_tables(tbl)
    assert "```" in res
    assert "Col 1" in res
    assert "Col 2" in res


def test_format_streaming_message():
    tools = ["⚙️ tool1...", "✅ tool2"]
    text = "Generating **bold** response..."
    res = format_streaming_message(tools, text, cursor=True)
    assert "[Progress]" in res
    assert "tool1" in res
    assert "tool2" in res
    assert "<b>bold</b>" in res
    assert "▌" in res


def test_format_streaming_message_truncation():
    tools = ["tool1", "tool2"]
    huge_text = "x" * 5000
    res = format_streaming_message(tools, huge_text, cursor=True, max_len=1000)
    assert len(res) <= 1200
    assert "[...earlier text omitted...]" in res


def test_format_final_result():
    res = format_final_result(
        response_text="All done with **success**!",
        duration_seconds=3.456,
        total_tokens=15000,
        thinking_tokens=300,
        conversation_id="conv-1234-5678-90ab",
    )
    assert "<b>success</b>" in res
    assert "3.5s" in res
    assert "15.0k (thinking: 300) tokens" in res
    assert "conv-123..." in res


def test_split_text_chunks():
    short = "<b>Hello</b> world"
    assert split_text_chunks(short, 100) == ["<b>Hello</b> world"]

    long_text = "\n\n".join([f"Paragraph {i}: " + "word " * 20 for i in range(20)])
    chunks = split_text_chunks(long_text, chunk_size=200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 300


def test_should_export_as_file():
    # Explicit request
    assert should_export_as_file("请把diff发我文件", "short response") is True
    assert should_export_as_file("export file please", "short response") is True
    assert should_export_as_file("生成测试报告", "short") is True

    # Large text threshold
    assert should_export_as_file("normal prompt", "x" * 2500) is True

    # Code diff block
    diff_text = "```diff\ndiff --git a/a.py b/a.py\n+ new line\n- old line\n" + "extra " * 100 + "```"
    assert should_export_as_file("show diff", diff_text) is True

    # Short normal response should NOT export
    assert should_export_as_file("Hello", "Hi there, how can I help?") is False


def test_generate_standalone_html():
    sample = """# Report Title
This is **bold** and `inline_code`.

```diff
diff --git a/main.py b/main.py
@@ -1,3 +1,4 @@
+print("added")
-print("removed")
```
"""
    html_out = generate_standalone_html(
        title="Test Report",
        markdown_content=sample,
        metadata={"time": "2.5s", "tokens": "12.5k", "model": "gemini-2.5-pro"},
    )
    assert "<!DOCTYPE html>" in html_out
    assert "Test Report" in html_out
    assert "diff-add" in html_out
    assert "diff-sub" in html_out
    assert "gemini-2.5-pro" in html_out
    assert "<strong>bold</strong>" in html_out


def test_sanitize_filename():
    assert sanitize_filename("diff.html") == "diff.html"
    assert sanitize_filename("../../../etc/passwd.html") == "passwd.html"
    assert sanitize_filename("my report") == "my report.html"
    assert sanitize_filename("test?.html") == "test_.html"
    assert sanitize_filename("") == "report.html"
    assert sanitize_filename("file.htm") == "file.htm"


def test_detect_content_mode():
    # Explicit hints
    assert detect_content_mode("foo", mode_hint="html") == "html"
    assert detect_content_mode("foo", mode_hint="path") == "path"
    assert detect_content_mode("foo", mode_hint="md") == "markdown"

    # File path detection
    assert detect_content_mode("runtime/artifacts/diff.html") == "path"
    assert detect_content_mode("./out/report.html") == "path"
    assert detect_content_mode("/var/log/test.log") == "path"
    assert detect_content_mode("report.md") == "path"

    # Raw HTML detection
    assert detect_content_mode("<!DOCTYPE html><html><body>Test</body></html>") == "html"
    assert detect_content_mode("<html><head></head><body><h1>Hi</h1></body></html>") == "html"
    assert detect_content_mode("<div class='custom-report'><table><tr><td>Data</td></tr></table></div>") == "html"

    # Markdown detection
    assert detect_content_mode("# Heading\n- Item 1\n- Item 2\n```python\nprint(1)\n```") == "markdown"
    assert detect_content_mode("Just some standard multi-line\nresponse commentary.") == "markdown"


def test_parse_agy_file_block():
    # 1. Block with headers and separator
    block1 = """filename: diff_report.html
title: Code Diff Report
layout: mobile
---
```diff
+ added
- removed
```"""
    b1 = parse_agy_file_block(block1)
    assert b1.filename == "diff_report.html"
    assert b1.title == "Code Diff Report"
    assert b1.layout == "mobile"
    assert b1.mode == "markdown"
    assert "```diff" in b1.content

    # 2. Block with headers without separator
    block2 = """filename: test.html
title: Test Title
# Header
- Content bullet
"""
    b2 = parse_agy_file_block(block2)
    assert b2.filename == "test.html"
    assert b2.title == "Test Title"
    assert b2.mode == "markdown"

    # 3. Block with file path
    block3 = """filename: artifact.html
---
./runtime/artifacts/export.html"""
    b3 = parse_agy_file_block(block3)
    assert b3.filename == "artifact.html"
    assert b3.mode == "path"
    assert b3.content.strip() == "./runtime/artifacts/export.html"

    # 4. Block with raw HTML
    block4 = """title: Raw HTML Page
---
<!DOCTYPE html><html><body><h1>Hello</h1></body></html>"""
    b4 = parse_agy_file_block(block4)
    assert b4.filename == "report.html"
    assert b4.title == "Raw HTML Page"
    assert b4.mode == "html"


def test_extract_agy_file_blocks():
    text = """Here is the preview analysis.

[AGY_FILE]
filename: diff1.html
title: Diff 1
---
# Diff 1 content
[END_AGY_FILE]

Some intermediate text.

[AGY_FILE]
filename: diff2.html
title: Diff 2
---
# Diff 2 content
[END_AGY_FILE]

Final conclusion."""
    cleaned, blocks = extract_agy_file_blocks(text)
    assert len(blocks) == 2
    assert blocks[0].filename == "diff1.html"
    assert blocks[0].title == "Diff 1"
    assert blocks[1].filename == "diff2.html"
    assert blocks[1].title == "Diff 2"

    assert "[AGY_FILE]" not in cleaned
    assert "[END_AGY_FILE]" not in cleaned
    assert "Here is the preview analysis." in cleaned
    assert "Some intermediate text." in cleaned
    assert "Final conclusion." in cleaned


def test_render_agy_file_markdown():
    block = AgyFileBlock(
        filename="report.html",
        title="Markdown Report",
        content="# Hello World\nThis is **bold** text.",
        mode="markdown",
    )
    html_out, err = render_agy_file(block)
    assert err is None
    assert "<!DOCTYPE html>" in html_out
    assert "Markdown Report" in html_out
    assert "<strong>bold</strong>" in html_out
    assert '<meta name="viewport"' in html_out


def test_render_agy_file_raw_html():
    # Fragment HTML
    block_frag = AgyFileBlock(
        filename="frag.html",
        title="Fragment",
        content="<div class='custom'><h3>Fragment Title</h3><p>Content</p></div>",
        mode="html",
    )
    html_out, err = render_agy_file(block_frag)
    assert err is None
    assert "<!DOCTYPE html>" in html_out
    assert "<div class='custom'><h3>Fragment Title</h3>" in html_out
    assert '<meta name="viewport"' in html_out

    # Full HTML without viewport
    block_full = AgyFileBlock(
        filename="full.html",
        title="Full",
        content="<!DOCTYPE html><html><head><title>Full Doc</title></head><body><h1>Full Body</h1></body></html>",
        mode="html",
    )
    html_out2, err2 = render_agy_file(block_full)
    assert err2 is None
    assert '<meta name="viewport"' in html_out2
    assert "Full Body" in html_out2
    assert "overflow-x" in html_out2


def test_render_agy_file_filepath(tmp_path):
    # Existing file
    test_file = tmp_path / "test_artifact.html"
    test_file.write_text("<!DOCTYPE html><html><body><h1>Artifact Content</h1></body></html>")

    block = AgyFileBlock(
        filename="test_artifact.html",
        title="Artifact",
        content=str(test_file),
        mode="path",
    )
    html_out, err = render_agy_file(block, base_dir=str(tmp_path))
    assert err is None
    assert "Artifact Content" in html_out

    # Non-existent file
    block_missing = AgyFileBlock(
        filename="missing.html",
        title="Missing",
        content="non_existent_dir/missing.html",
        mode="path",
    )
    html_out_m, err_m = render_agy_file(block_missing, base_dir=str(tmp_path))
    assert html_out_m is None
    assert "File not found" in err_m


def test_mobile_html_responsive_elements():
    html_out = generate_standalone_html(
        title="Mobile Responsive Test",
        markdown_content="| Col 1 | Col 2 |\n|---|---|\n| Val 1 | Val 2 |\n\n```python\nlong_code_line = 12345\n```",
    )
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in html_out
    assert "overflow-x: auto" in html_out
    assert "-webkit-overflow-scrolling: touch" in html_out
    assert "max-width: 860px" in html_out
    assert "@media (max-width: 480px)" in html_out
    assert "@media (prefers-color-scheme: light)" in html_out

