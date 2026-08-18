"""Unit tests for gateway.message_formatter."""

from gateway.message_formatter import (
    escape_html,
    format_final_result,
    format_streaming_message,
    format_tool_summary,
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


def test_format_streaming_message():
    tools = ["⚙️ tool1...", "✅ tool2"]
    text = "Generating response..."
    res = format_streaming_message(tools, text, cursor=True)
    assert "[Progress]" in res
    assert "tool1" in res
    assert "tool2" in res
    assert "Generating response... ▌" in res


def test_format_streaming_message_truncation():
    tools = ["tool1", "tool2"]
    huge_text = "x" * 5000
    res = format_streaming_message(tools, huge_text, cursor=True, max_len=1000)
    assert len(res) <= 1000
    assert "[...earlier text omitted...]" in res


def test_format_final_result():
    res = format_final_result(
        response_text="All done!",
        duration_seconds=3.456,
        total_tokens=15000,
        thinking_tokens=300,
        conversation_id="conv-1234-5678-90ab",
    )
    assert "All done!" in res
    assert "3.5s" in res
    assert "15.0k (thinking: 300) tokens" in res
    assert "conv-123..." in res


def test_split_text_chunks():
    short = "Hello world"
    assert split_text_chunks(short, 100) == ["Hello world"]

    long_text = "\n\n".join([f"Paragraph {i}: " + "word " * 20 for i in range(20)])
    chunks = split_text_chunks(long_text, chunk_size=200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 200
