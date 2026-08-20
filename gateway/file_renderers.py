"""Dynamic file-to-HTML template renderers based on file extensions and types."""

from __future__ import annotations

import csv
import html
import io
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple


BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svgz",
    ".pdf", ".epub",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a",
    ".pyc", ".pyo", ".pyd", ".class",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".ogg", ".mov", ".webm", ".flac",
    ".sqlite", ".db", ".sqlite3",
}


def is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary by extension or by scanning initial bytes for null characters."""
    ext = file_path.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True

    try:
        with open(file_path, "rb") as f:
            chunk = f.read(4096)
            if b"\x00" in chunk:
                return True
        return False
    except OSError:
        return True


def format_file_size(size_bytes: int) -> str:
    """Format byte size to readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def highlight_code_syntax(code_text: str, lang: str) -> List[str]:
    """
    Tokenize and apply syntax highlight spans to code lines.
    Returns list of HTML-safe lines with syntax spans.
    """
    lines = code_text.splitlines()
    if not lines:
        return [""]

    py_kw = {"def", "class", "import", "from", "as", "return", "if", "elif", "else",
             "for", "while", "in", "try", "except", "finally", "with", "raise",
             "assert", "yield", "lambda", "async", "await", "pass", "break", "continue"}
    js_kw = {"function", "const", "let", "var", "return", "if", "else", "for", "while",
             "import", "export", "from", "default", "class", "extends", "new", "this",
             "async", "await", "try", "catch", "finally", "throw", "switch", "case", "break"}
    gen_kw = py_kw | js_kw | {"select", "from", "where", "insert", "update", "delete", "create", "table"}

    highlighted_lines: List[str] = []

    for raw_line in lines:
        esc = html.escape(raw_line, quote=False)

        # Highlight comments
        if lang in ("python", "py", "sh", "bash", "yaml", "yml", "toml"):
            comment_match = re.search(r"(#.*)$", esc)
            if comment_match:
                c_text = comment_match.group(1)
                pre_c = esc[:comment_match.start()]
                esc = pre_c + f'<span class="hl-cmt">{c_text}</span>'
        elif lang in ("javascript", "js", "typescript", "ts", "java", "c", "cpp", "rust", "go"):
            comment_match = re.search(r"(//.*)$", esc)
            if comment_match:
                c_text = comment_match.group(1)
                pre_c = esc[:comment_match.start()]
                esc = pre_c + f'<span class="hl-cmt">{c_text}</span>'

        # Highlight strings
        esc = re.sub(r'(&quot;.*?&quot;|\'.*?\'|`.*?`)', r'<span class="hl-str">\1</span>', esc)

        # Highlight decorators (@...) in python
        if lang in ("python", "py"):
            esc = re.sub(r'^(\s*)(@\w+)', r'\1<span class="hl-dec">\2</span>', esc)

        # Highlight keywords
        def repl_kw(m: re.Match) -> str:
            w = m.group(0)
            if w.lower() in gen_kw:
                return f'<span class="hl-kw">{w}</span>'
            if w in ("True", "False", "None", "true", "false", "null", "undefined"):
                return f'<span class="hl-val">{w}</span>'
            return w

        esc = re.sub(r'\b[a-zA-Z_]\w*\b', repl_kw, esc)

        # Highlight numbers
        esc = re.sub(r'\b(\d+(\.\d+)?)\b', r'<span class="hl-num">\1</span>', esc)

        highlighted_lines.append(esc)

    return highlighted_lines


def get_base_css() -> str:
    """Common responsive CSS styles for all file templates."""
    return """
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --accent-hover: #0ea5e9;
      --accent-bg: rgba(56, 189, 248, 0.12);
      --success: #4ade80;
      --success-bg: rgba(74, 222, 128, 0.15);
      --error: #f87171;
      --error-bg: rgba(248, 113, 113, 0.15);
      --hover-bg: rgba(255, 255, 255, 0.05);
      --hl-kw: #c084fc;
      --hl-str: #34d399;
      --hl-cmt: #64748b;
      --hl-num: #fb923c;
      --hl-dec: #38bdf8;
      --hl-val: #f472b6;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f8fafc;
        --card-bg: #ffffff;
        --border: #e2e8f0;
        --text: #0f172a;
        --text-muted: #64748b;
        --accent: #0284c7;
        --accent-hover: #0369a1;
        --accent-bg: rgba(2, 132, 199, 0.08);
        --success: #16a34a;
        --success-bg: rgba(22, 163, 74, 0.12);
        --error: #dc2626;
        --error-bg: rgba(220, 38, 38, 0.1);
        --hover-bg: rgba(0, 0, 0, 0.03);
        --hl-kw: #7e22ce;
        --hl-str: #059669;
        --hl-cmt: #94a3b8;
        --hl-num: #ea580c;
        --hl-dec: #0284c7;
        --hl-val: #db2777;
      }
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font-sans);
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 12px;
      -webkit-font-smoothing: antialiased;
    }

    .container {
      max-width: 1000px;
      margin: 0 auto;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
    }

    .file-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
      align-items: center;
    }

    .file-title-group {
      flex: 1;
      min-width: 240px;
    }

    .file-title {
      font-size: 1.2rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
      word-break: break-all;
    }

    .file-path {
      font-family: var(--font-mono);
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-top: 4px;
      word-break: break-all;
    }

    .file-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }

    .badge {
      font-size: 0.72rem;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 6px;
      background: var(--accent-bg);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.2);
    }

    .header-actions {
      display: flex;
      gap: 8px;
    }

    .btn {
      padding: 6px 12px;
      font-size: 0.8rem;
      font-weight: 600;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--accent);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: all 0.15s;
      white-space: nowrap;
    }

    .btn:hover {
      background: var(--accent-bg);
      border-color: var(--accent);
    }

    .btn.copied {
      background: var(--success-bg);
      color: var(--success);
      border-color: var(--success);
    }

    .content-area {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }

    /* Syntax highlight tokens */
    .hl-kw { color: var(--hl-kw); font-weight: 600; }
    .hl-str { color: var(--hl-str); }
    .hl-cmt { color: var(--hl-cmt); font-style: italic; }
    .hl-num { color: var(--hl-num); }
    .hl-dec { color: var(--hl-dec); font-weight: 600; }
    .hl-val { color: var(--hl-val); font-weight: 600; }

    /* Code line viewer */
    .code-table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--font-mono);
      font-size: 0.82rem;
      line-height: 1.55;
    }

    .code-row:hover {
      background: var(--hover-bg);
    }

    .line-num {
      width: 48px;
      padding: 2px 12px;
      text-align: right;
      color: var(--text-muted);
      user-select: none;
      border-right: 1px solid var(--border);
      opacity: 0.6;
      vertical-align: top;
    }

    .line-code {
      padding: 2px 14px;
      white-space: pre;
      word-break: normal;
      vertical-align: top;
    }

    /* Toast */
    .toast {
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: #1e293b;
      color: #38bdf8;
      border: 1px solid #38bdf8;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      box-shadow: 0 6px 20px rgba(0,0,0,0.3);
      z-index: 9999;
      opacity: 0;
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      pointer-events: none;
    }

    .toast.show {
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }
    """


def render_code_template(
    file_name: str,
    rel_path: str,
    raw_content: str,
    lang: str,
    file_size_str: str,
) -> str:
    """Render source code with syntax highlighting and line numbers."""
    highlighted_lines = highlight_code_syntax(raw_content, lang)
    total_lines = len(highlighted_lines)

    rows_html: List[str] = []
    for idx, h_line in enumerate(highlighted_lines, start=1):
        code_content = h_line if h_line else "&nbsp;"
        rows_html.append(
            f'<tr class="code-row">'
            f'<td class="line-num">{idx}</td>'
            f'<td class="line-code">{code_content}</td>'
            f'</tr>'
        )

    table_body = "\n".join(rows_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>📄 {html.escape(file_name)}</title>
  <style>
    {get_base_css()}
  </style>
</head>
<body>
  <div class="container">
    <div class="file-header">
      <div class="file-title-group">
        <div class="file-title">
          <span>📄</span> <span>{html.escape(file_name)}</span>
        </div>
        <div class="file-path">{html.escape(rel_path)}</div>
        <div class="file-badges">
          <span class="badge">🏷️ {html.escape(lang.upper())}</span>
          <span class="badge">📊 {total_lines} lines</span>
          <span class="badge">💾 {file_size_str}</span>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn" onclick="copyRawCode()">📋 Copy Code</button>
      </div>
    </div>

    <div class="content-area">
      <table class="code-table">
        <tbody>
          {table_body}
        </tbody>
      </table>
    </div>
  </div>

  <div id="toast" class="toast"></div>
  <textarea id="rawCodeStore" style="display:none;">{html.escape(raw_content)}</textarea>

  <script>
    function showToast(text) {{
      const toast = document.getElementById('toast');
      toast.textContent = text;
      toast.classList.add('show');
      setTimeout(() => {{
        toast.classList.remove('show');
      }}, 2000);
    }}

    function copyRawCode() {{
      const text = document.getElementById('rawCodeStore').value;
      navigator.clipboard.writeText(text).then(() => {{
        showToast('✅ Code copied to clipboard!');
      }}).catch(err => {{
        const ta = document.getElementById('rawCodeStore');
        ta.style.display = 'block';
        ta.select();
        document.execCommand('copy');
        ta.style.display = 'none';
        showToast('✅ Code copied!');
      }});
    }}
  </script>
</body>
</html>
"""


def render_markdown_template(
    file_name: str,
    rel_path: str,
    raw_content: str,
    file_size_str: str,
) -> str:
    """Render Markdown file into article HTML layout with tables, diffs, and code blocks."""
    try:
        import markdown

        body_html = markdown.markdown(
            raw_content,
            extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        )
        # Wrap tables for touch horizontal scroll
        body_html = re.sub(r"(<table>.*?</table>)", r'<div style="width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;margin:16px 0;">\1</div>', body_html, flags=re.DOTALL)
    except Exception:
        body_html = f"<pre><code>{html.escape(raw_content)}</code></pre>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>📄 {html.escape(file_name)}</title>
  <style>
    {get_base_css()}
    .article-body {{
      padding: 24px 20px;
      line-height: 1.7;
    }}
    .article-body ul {{
      padding-left: 24px;
      margin: 10px 0;
    }}
    .article-body table {{
      width: 100%;
      border-collapse: collapse;
      margin: 16px 0;
    }}
    .article-body th, .article-body td {{
      padding: 8px 12px;
      border: 1px solid var(--border);
      text-align: left;
    }}
    .article-body th {{
      background: var(--hover-bg);
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="file-header">
      <div class="file-title-group">
        <div class="file-title">
          <span>📖</span> <span>{html.escape(file_name)}</span>
        </div>
        <div class="file-path">{html.escape(rel_path)}</div>
        <div class="file-badges">
          <span class="badge">🏷️ MARKDOWN</span>
          <span class="badge">💾 {file_size_str}</span>
        </div>
      </div>
    </div>

    <div class="content-area article-body">
      {body_html}
    </div>
  </div>
</body>
</html>
"""


def render_diff_template(
    file_name: str,
    rel_path: str,
    raw_content: str,
    file_size_str: str,
) -> str:
    """Render Diff and Patch files with additions, deletions, and hunk coloring."""
    lines = raw_content.splitlines()
    rows_html: List[str] = []

    for idx, l in enumerate(lines, start=1):
        esc = html.escape(l, quote=False)
        row_cls = ""
        if esc.startswith("+") and not esc.startswith("+++"):
            row_cls = "diff-add"
        elif esc.startswith("-") and not esc.startswith("---"):
            row_cls = "diff-sub"
        elif esc.startswith("@@") or esc.startswith("diff --git"):
            row_cls = "diff-info"

        code_val = esc if esc else "&nbsp;"
        rows_html.append(
            f'<tr class="code-row {row_cls}">'
            f'<td class="line-num">{idx}</td>'
            f'<td class="line-code">{code_val}</td>'
            f'</tr>'
        )

    table_body = "\n".join(rows_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>📊 {html.escape(file_name)}</title>
  <style>
    {get_base_css()}
    .diff-add {{ background: var(--success-bg) !important; color: var(--success); }}
    .diff-sub {{ background: var(--error-bg) !important; color: var(--error); }}
    .diff-info {{ background: var(--accent-bg) !important; color: var(--accent); font-weight: 600; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="file-header">
      <div class="file-title-group">
        <div class="file-title">
          <span>📊</span> <span>{html.escape(file_name)}</span>
        </div>
        <div class="file-path">{html.escape(rel_path)}</div>
        <div class="file-badges">
          <span class="badge">🏷️ DIFF / PATCH</span>
          <span class="badge">📊 {len(lines)} lines</span>
          <span class="badge">💾 {file_size_str}</span>
        </div>
      </div>
    </div>

    <div class="content-area">
      <table class="code-table">
        <tbody>
          {table_body}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def render_csv_template(
    file_name: str,
    rel_path: str,
    raw_content: str,
    file_size_str: str,
    delimiter: str = ",",
) -> str:
    """Render CSV / TSV tabular files into an interactive data table."""
    reader = csv.reader(io.StringIO(raw_content), delimiter=delimiter)
    all_rows = list(reader)

    if not all_rows:
        table_html = "<div style='padding:20px;color:var(--text-muted);'>Empty dataset</div>"
        header_count = 0
        total_rows = 0
    else:
        headers = all_rows[0]
        header_count = len(headers)
        total_rows = len(all_rows) - 1

        th_cells = "".join(f'<th style="padding:8px 12px;border:1px solid var(--border);background:var(--hover-bg);font-weight:600;text-align:left;">{html.escape(c)}</th>' for c in headers)
        thead = f'<thead><tr>{th_cells}</tr></thead>'

        tb_rows: List[str] = []
        for r in all_rows[1:]:
            td_cells = "".join(f'<td style="padding:6px 12px;border:1px solid var(--border);font-family:var(--font-mono);font-size:0.85rem;">{html.escape(c)}</td>' for c in r)
            tb_rows.append(f'<tr>{td_cells}</tr>')
        tb_rows_str = "\n".join(tb_rows)
        tbody = f'<tbody>{tb_rows_str}</tbody>'
        table_html = f'<table style="width:100%;border-collapse:collapse;">{thead}{tbody}</table>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>📊 {html.escape(file_name)}</title>
  <style>
    {get_base_css()}
    .table-container {{
      padding: 12px;
      overflow-x: auto;
      max-height: 75vh;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="file-header">
      <div class="file-title-group">
        <div class="file-title">
          <span>📊</span> <span>{html.escape(file_name)}</span>
        </div>
        <div class="file-path">{html.escape(rel_path)}</div>
        <div class="file-badges">
          <span class="badge">🏷️ DATA TABLE</span>
          <span class="badge">📐 {header_count} cols × {total_rows} rows</span>
          <span class="badge">💾 {file_size_str}</span>
        </div>
      </div>
    </div>

    <div class="content-area table-container">
      {table_html}
    </div>
  </div>
</body>
</html>
"""


def render_file_to_html(
    target_file: Path,
    project_name: str = "",
    rel_path: str = "",
) -> Tuple[bool, str, str]:
    """
    Convert a workspace file to a standalone, formatted HTML file based on its extension.
    Returns: (is_converted, html_content_or_empty, output_filename)
    If file is binary or unsupported, returns (False, "", target_file.name).
    """
    if not target_file.is_file():
        return False, "", target_file.name

    if is_binary_file(target_file):
        return False, "", target_file.name

    try:
        raw_content = target_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False, "", target_file.name

    name = target_file.name
    output_filename = f"{name}.html"
    effective_rel = rel_path or name
    file_size_str = format_file_size(target_file.stat().st_size)
    ext = target_file.suffix.lower()

    # 1. Markdown
    if ext in (".md", ".markdown", ".mdown", ".mkd"):
        html_doc = render_markdown_template(name, effective_rel, raw_content, file_size_str)
        return True, html_doc, output_filename

    # 2. Diff & Patch
    if ext in (".diff", ".patch"):
        html_doc = render_diff_template(name, effective_rel, raw_content, file_size_str)
        return True, html_doc, output_filename

    # 3. CSV & TSV
    if ext == ".csv":
        html_doc = render_csv_template(name, effective_rel, raw_content, file_size_str, delimiter=",")
        return True, html_doc, output_filename
    if ext == ".tsv":
        html_doc = render_csv_template(name, effective_rel, raw_content, file_size_str, delimiter="\t")
        return True, html_doc, output_filename

    # 4. Source Code Mapping
    code_ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".vue": "vue",
        ".svelte": "svelte",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".ini": "ini",
        ".cfg": "config",
        ".sql": "sql",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".cc": "cpp",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".rb": "ruby",
        ".php": "php",
        ".r": "r",
        ".lua": "lua",
        ".dart": "dart",
        ".proto": "protobuf",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".dockerfile": "dockerfile",
    }

    if ext in code_ext_map or name.lower() in ("dockerfile", "makefile", "rakefile"):
        lang = code_ext_map.get(ext, "code")
        html_doc = render_code_template(name, effective_rel, raw_content, lang, file_size_str)
        return True, html_doc, output_filename

    # 5. Plain Text / Logs / Config
    text_exts = {".txt", ".log", ".env", ".gitignore", ".gitattributes", ".lock", ".xml"}
    if ext in text_exts or name.startswith(".env"):
        html_doc = render_code_template(name, effective_rel, raw_content, "text", file_size_str)
        return True, html_doc, output_filename

    # 6. Fallback for text files
    html_doc = render_code_template(name, effective_rel, raw_content, "text", file_size_str)
    return True, html_doc, output_filename
