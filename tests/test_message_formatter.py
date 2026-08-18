"""Unit tests for gateway.message_formatter."""

from gateway.message_formatter import (
    balance_telegram_html,
    escape_html,
    format_final_result,
    format_markdown_tables,
    format_streaming_message,
    format_tool_summary,
    markdown_to_telegram_html,
    split_text_chunks,
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
