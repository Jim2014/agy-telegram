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

    # Done view_file with duration and line range
    t2 = format_tool_summary(
        "view_file",
        "DONE",
        {"parameters": {"AbsolutePath": "/a/b/c.py", "StartLine": 10, "EndLine": 25}},
        duration_seconds=1.23,
    )
    assert "c.py#L10-L25" in t2
    assert "✅" in t2
    assert "1.2s" in t2

    # Error tool
    t3 = format_tool_summary("list_dir", "ERROR", {"error": {"message": "Permission denied"}})
    assert "list_dir failed" in t3
    assert "Permission denied" in t3
    assert "⚠️" in t3

    # Subagents
    t_sub = format_tool_summary(
        "invoke_subagent",
        "ACTIVE",
        {"parameters": {"Subagents": [{"Role": "Code Reviewer"}, {"Role": "Security Analyst"}]}},
    )
    assert "🤖" in t_sub
    assert "Code Reviewer" in t_sub
    assert "Security Analyst" in t_sub

    # Search & Web
    t_web = format_tool_summary("read_url_content", "ACTIVE", {"parameters": {"Url": "https://antigravity.dev/docs"}})
    assert "🌐" in t_web
    assert "https://antigravity.dev/docs" in t_web

    # Image generation
    t_img_act = format_tool_summary("generate_image", "ACTIVE", {"parameters": {"ImageName": "app_mockup"}})
    assert "🎨" in t_img_act
    assert "app_mockup" in t_img_act
    t_img = format_tool_summary("generate_image", "DONE", {"parameters": {"ImageName": "app_mockup"}}, duration_seconds=3.5)
    assert "✅" in t_img
    assert "app_mockup" in t_img
    assert "3.5s" in t_img

    # Schedule / task
    t_sched = format_tool_summary("schedule", "ACTIVE", {"parameters": {"DurationSeconds": 60}})
    assert "⏰" in t_sched
    assert "60s" in t_sched


def test_format_tokens_and_duration():
    from gateway.message_formatter import format_duration, format_tokens

    # Token formatting
    assert format_tokens(3973596) == "3.97M (3,973,596)"
    assert format_tokens(3973596, thinking=250000) == "3.97M (3,973,596) [thinking: 250.0k]"
    assert format_tokens(45210) == "45.2k (45,210)"
    assert format_tokens(350) == "350"
    assert format_tokens(0) == "0"

    # Duration formatting
    assert format_duration(7208.2) == "2h 0m 8s"
    assert format_duration(75.4) == "1m 15s"
    assert format_duration(4.2) == "4.2s"
    assert format_duration(0.0) == "0.0s"


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
    assert "• **A**: B" in res

    html_out = markdown_to_telegram_html(tbl)
    assert "• <b>A</b>: B" in html_out


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
    # With project_name
    res_proj = format_final_result(
        response_text="All done with **success**!",
        duration_seconds=3.456,
        total_tokens=15000,
        thinking_tokens=300,
        conversation_id="conv-1234-5678-90ab",
        project_name="myapp",
    )
    assert "<b>success</b>" in res_proj
    assert "3.5s" in res_proj
    assert "15.0k (thinking: 300) tokens" in res_proj
    assert "myapp" in res_proj

    # Fallback to conversation_id when project_name is None
    res_conv = format_final_result(
        response_text="All done",
        duration_seconds=75.4,
        total_tokens=9_935_900,
        thinking_tokens=166_751,
        conversation_id="conv-1234-5678-90ab",
    )
    assert "conv-123..." in res_conv
    assert "1m 15s" in res_conv
    assert "9.94M (thinking: 166.8k) tokens" in res_conv


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

    # Short normal response should NOT export (including words like 'different'/'difference')
    assert should_export_as_file("Hello", "Hi there, how can I help?") is False
    assert should_export_as_file("What is the difference between A and B?", "Short explanation.") is False


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

    # File path detection
    assert detect_content_mode("runtime/artifacts/diff.html") == "path"
    assert detect_content_mode("./out/report.html") == "path"
    assert detect_content_mode("/var/log/test.log") == "path"

    # Direct HTML detection
    assert detect_content_mode("<!DOCTYPE html><html><body>Test</body></html>") == "html"
    assert detect_content_mode("<html><head></head><body><h1>Hi</h1></body></html>") == "html"
    assert detect_content_mode("<div class='custom-report'><table><tr><td>Data</td></tr></table></div>") == "html"
    assert detect_content_mode("<h1>Heading</h1><p>Direct HTML content</p>") == "html"


def test_parse_agy_file_block():
    # 1. Block with headers and separator containing HTML
    block1 = """filename: diff_report.html
title: Code Diff Report
layout: mobile
---
<div class="diff-report">
  <pre class="diff">+ added line</pre>
</div>"""
    b1 = parse_agy_file_block(block1)
    assert b1.filename == "diff_report.html"
    assert b1.title == "Code Diff Report"
    assert b1.layout == "mobile"
    assert b1.mode == "html"
    assert "diff-report" in b1.content

    # 2. Block with headers without separator
    block2 = """filename: test.html
title: Test Title
<div class="content"><h1>Header</h1></div>
"""
    b2 = parse_agy_file_block(block2)
    assert b2.filename == "test.html"
    assert b2.title == "Test Title"
    assert b2.mode == "html"

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


def test_render_agy_file_direct_html():
    block = AgyFileBlock(
        filename="report.html",
        title="Direct HTML Report",
        content="<!DOCTYPE html><html><head><title>Direct Report</title></head><body><h1>Hello World</h1><p>This is direct HTML.</p></body></html>",
        mode="html",
    )
    html_out, err = render_agy_file(block)
    assert err is None
    assert "<!DOCTYPE html>" in html_out
    assert "Hello World" in html_out
    assert "This is direct HTML." in html_out
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


def test_extract_agy_action_blocks():
    from gateway.message_formatter import extract_agy_action_blocks

    text = """
Here is the implementation of the strategy.

[AGY_ACTIONS]
- 🧪 Run Tests: pytest -v
- 📝 View Diff: /diff
- 🚀 Commit & Push
[END_AGY_ACTIONS]
"""
    cleaned, actions = extract_agy_action_blocks(text)
    assert "[AGY_ACTIONS]" not in cleaned
    assert "Here is the implementation" in cleaned
    assert len(actions) == 3
    assert actions[0].label == "🧪 Run Tests"
    assert actions[0].action == "pytest -v"
    assert actions[1].label == "📝 View Diff"
    assert actions[1].action == "/diff"
    assert actions[2].label == "🚀 Commit & Push"
    assert actions[2].action == "🚀 Commit & Push"


def test_blockquotes_in_streaming():
    from gateway.message_formatter import format_streaming_message

    tools = ["⚙️ run_command: pytest...", "✅ run_command (1.2s)"]
    msg = format_streaming_message(tool_events=tools, current_text="Building...")
    assert "<blockquote>" in msg
    assert "</blockquote>" in msg
    assert "<b>[Progress]</b>" in msg


def test_format_pinned_banner():
    from gateway.message_formatter import format_pinned_banner
    from gateway.session_manager import ChatSession

    session = ChatSession(
        chat_id=12345,
        active_project="alpha-strategy",
        projects={
            "alpha-strategy": {
                "name": "alpha-strategy",
                "path": "/workspace/projects/alpha",
                "conversation_id": "conv-12345678",
                "total_tokens": 1500000,
                "thinking_tokens": 120000,
                "turns": 15,
                "total_duration_seconds": 95.5,
            }
        },
        model="gemini-3.8-flash",
        effort="high",
    )

    # 1. Idle state
    banner_idle = format_pinned_banner(session, is_busy=False)
    assert "📌 <b>agy Workspace Hub</b>" in banner_idle
    assert "<code>alpha-strategy</code>" in banner_idle
    assert "🟢 <b>Ready</b>" in banner_idle
    assert "/workspace/projects/alpha" in banner_idle
    assert "gemini-3.8-flash" in banner_idle
    assert "1.50M" in banner_idle
    assert "15 turns" in banner_idle

    # 2. Busy state
    banner_busy = format_pinned_banner(session, is_busy=True)
    assert "⚡ <b>Running...</b>" in banner_busy


def test_extract_agy_voice_block():
    from gateway.message_formatter import extract_agy_voice_block

    # 1. Text with Chinese voice block
    raw_zh = """
Here is the detailed technical report.
```python
def foo():
    pass
```

[AGY_VOICE lang="zh"]
我已经为你跑完了全部 116 项单元测试，所有用例全部通过，并已自动完成 Git 提交。
[END_AGY_VOICE]
"""
    cleaned_zh, voice_zh = extract_agy_voice_block(raw_zh)
    assert "[AGY_VOICE" not in cleaned_zh
    assert "[END_AGY_VOICE]" not in cleaned_zh
    assert "Here is the detailed technical report" in cleaned_zh
    assert voice_zh is not None
    assert voice_zh.lang == "zh"
    assert "116 项单元测试" in voice_zh.text

    # 2. Text with English voice block
    raw_en = """
Analysis completed.

[AGY_VOICE lang="en"]
All tests passed successfully and changes have been committed.
[END_AGY_VOICE]
"""
    cleaned_en, voice_en = extract_agy_voice_block(raw_en)
    assert "[AGY_VOICE" not in cleaned_en
    assert voice_en is not None
    assert voice_en.lang == "en"
    assert "All tests passed" in voice_en.text

    # 3. Text without voice block
    raw_none = "Just a regular plain text reply without any voice block."
    cleaned_none, voice_none = extract_agy_voice_block(raw_none)
    assert cleaned_none == raw_none
    assert voice_none is None

    # 4. Text with leaked action options inside voice block
    raw_leaked = """
Main response text.

[AGY_VOICE lang="zh"]
总结已完成。
[AGY_ACTIONS]
- 🚀 开始测试
- 📝 查看变更
[END_AGY_ACTIONS]
- 选项A
- 选项B
[END_AGY_VOICE]
"""
    cleaned_leaked, voice_leaked = extract_agy_voice_block(raw_leaked)
    assert voice_leaked is not None
    assert voice_leaked.text == "总结已完成。"
    assert "选项" not in voice_leaked.text
    assert "AGY_ACTIONS" not in voice_leaked.text


def test_format_local_reset_time():
    from gateway.message_formatter import format_local_reset_time

    # Valid UTC ISO string with Z
    res = format_local_reset_time("2026-08-20T04:15:00Z")
    assert res != ""
    assert "2026" in res
    assert ":" in res

    # Empty or None string
    assert format_local_reset_time("") == ""
    assert format_local_reset_time(None) == ""

    # Invalid timestamp fallback
    assert format_local_reset_time("invalid-date") == "invalid-date"


def test_markdown_tables_with_nested_formatting():
    from gateway.message_formatter import markdown_to_telegram_html

    sample = """
| 模块 | 测试套件结果 | Git 提交状态 | 运行状态 |
| :--- | :--- | :--- | :--- |
| **agy-telegram** | **127 / 127 项通过 (100%)** | `2ac45cc` (feat: auto-create workspace directory...) | 🟢 正常运行中 |
| **learner** | **36 / 36 项通过 (100%)** | `1893045` (feat: add interactive audio playback bar...) | 🟢 运行在 8901 端口 |

1. [`gateway/commands.py`](file:///workspace/agy-telegram/gateway/commands.py)
"""
    res = markdown_to_telegram_html(sample)
    assert "<b>agy-telegram</b>" in res
    assert "<b>*<i>" not in res
    assert "</i>*" not in res
    assert "<code><code>" not in res
    assert "<code>gateway/commands.py</code>" in res


def test_distill_spoken_summary_cleaning_and_boundary_truncation():
    from gateway.message_formatter import distill_spoken_summary

    # 1. Chinese text with code, tables, headers, and bullet points
    chinese_text = """### 核心功能总结
我们优化了以下几个模块：
- 模块 A：完成了重构
- 模块 B：提升了性能

```python
def test():
    return 42
```

| 序号 | 项目 | 状态 |
|---|---|---|
| 1 | 网关 | 正常 |

所有自动化测试已通过。详细信息请见下方。"""

    res = distill_spoken_summary(chinese_text, has_exports=True, max_chars=500)
    assert res is not None
    assert res.lang == "zh"
    assert "```" not in res.text
    assert "def test" not in res.text
    assert "###" not in res.text
    assert "详细报告与分析文件已生成并附带于下方" in res.text
    assert res.text.endswith("。")

    # 2. English text with smart sentence boundary truncation
    long_en = "First sentence is clear and concise. " * 30 + "This final sentence should not be brutally sliced in the middle."
    res_en = distill_spoken_summary(long_en, has_exports=False, max_chars=300)
    assert res_en is not None
    assert res_en.lang == "en"
    assert len(res_en.text) <= 305
    # Must end at a sentence boundary
    assert res_en.text.endswith(".")
    assert "in the mi" not in res_en.text  # Not cut mid-word


def test_extract_protocol_blocks_with_inline_protocol_mentions():
    from gateway.message_formatter import extract_agy_voice_block, extract_agy_action_blocks, extract_agy_file_blocks

    text = """### 协议功能讲解
我们优化了以下几个模块：
- 模块 A：解释了 `[AGY_VOICE]` 和 `[AGY_ACTIONS]` 协议的作用。
- 模块 B：提升了 `[AGY_FILE]` 导出稳定性。

[AGY_ACTIONS]
- 🧪 运行测试: pytest
- 📊 查看状态: /status
[END_AGY_ACTIONS]

[AGY_VOICE lang="zh"]
我们完成了协议解析器的加固，确保正文中讨论协议标签时不会发生错误截断。
[END_AGY_VOICE]
"""
    cleaned_voice, voice = extract_agy_voice_block(text)
    assert voice is not None
    assert voice.lang == "zh"
    assert "我们完成了协议解析器的加固" in voice.text
    # Ensure inline mention in Section A and B was not destroyed
    assert "模块 A：解释了 `[AGY_VOICE]`" in cleaned_voice
    assert "模块 B：提升了 `[AGY_FILE]`" in cleaned_voice

    cleaned_actions, actions = extract_agy_action_blocks(cleaned_voice)
    assert len(actions) == 2
    assert actions[0].label == "🧪 运行测试"
    assert "模块 A：解释了 `[AGY_VOICE]`" in cleaned_actions
    assert "模块 B：提升了 `[AGY_FILE]`" in cleaned_actions







