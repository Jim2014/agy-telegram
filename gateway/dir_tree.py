"""Interactive directory tree scanner and standalone HTML viewer generator."""

from __future__ import annotations

import html
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_IGNORE_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "node_modules",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    ".DS_Store",
}


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (B, KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_file_icon(filename: str) -> str:
    """Return an appropriate emoji icon based on file extension or filename."""
    name_lower = filename.lower()
    if name_lower.startswith(".env"):
        return "🔒"
    if name_lower in (".gitignore", ".gitattributes", ".gitmodules"):
        return "🌿"
    if name_lower in ("dockerfile", "docker-compose.yml", "docker-compose.yaml"):
        return "🐳"
    if name_lower in ("makefile", "rakefile", "cmakelists.txt"):
        return "🛠️"

    ext = os.path.splitext(name_lower)[1]
    ext_map = {
        ".py": "🐍",
        ".pyc": "🐍",
        ".js": "📜",
        ".ts": "📜",
        ".jsx": "⚛️",
        ".tsx": "⚛️",
        ".vue": "💚",
        ".html": "🌐",
        ".htm": "🌐",
        ".css": "🎨",
        ".scss": "🎨",
        ".json": "⚙️",
        ".yaml": "⚙️",
        ".yml": "⚙️",
        ".toml": "⚙️",
        ".ini": "⚙️",
        ".cfg": "⚙️",
        ".env": "🔒",
        ".sh": "📜",
        ".bash": "📜",
        ".zsh": "📜",
        ".md": "📄",
        ".markdown": "📄",
        ".txt": "📄",
        ".rst": "📄",
        ".log": "📋",
        ".sql": "🗄️",
        ".db": "🗄️",
        ".sqlite": "🗄️",
        ".png": "🖼️",
        ".jpg": "🖼️",
        ".jpeg": "🖼️",
        ".gif": "🖼️",
        ".svg": "🖼️",
        ".webp": "🖼️",
        ".zip": "📦",
        ".tar": "📦",
        ".gz": "📦",
        ".7z": "📦",
        ".pdf": "📕",
        ".diff": "📊",
        ".patch": "📊",
    }
    return ext_map.get(ext, "📄")


def scan_directory_tree(
    root_path: str | Path,
    ignore_dirs: Optional[Set[str]] = None,
    max_items: int = 3000,
) -> Dict[str, Any]:
    """
    Recursively scan a directory tree and compute stats.
    Returns a dict with tree hierarchy, file counts, and sizes.
    """
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        return {
            "root_path": str(root),
            "exists": False,
            "total_files": 0,
            "total_dirs": 0,
            "total_size": 0,
            "total_size_str": "0 B",
            "tree": [],
        }

    ignores = ignore_dirs if ignore_dirs is not None else DEFAULT_IGNORE_DIRS
    total_files = 0
    total_dirs = 0
    total_size = 0
    item_count = 0

    def _scan_node(current_dir: Path, rel_parent: str = "") -> List[Dict[str, Any]]:
        nonlocal total_files, total_dirs, total_size, item_count
        if item_count >= max_items:
            return []

        entries: List[Dict[str, Any]] = []
        try:
            items = sorted(os.scandir(current_dir), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return []

        for item in items:
            name = item.name
            if name in ignores:
                continue

            rel_path = f"{rel_parent}/{name}" if rel_parent else name
            item_count += 1

            try:
                if item.is_dir(follow_symlinks=False):
                    total_dirs += 1
                    child_nodes = _scan_node(Path(item.path), rel_path)
                    entries.append({
                        "name": name,
                        "rel_path": rel_path,
                        "is_dir": True,
                        "children": child_nodes,
                        "children_count": len(child_nodes),
                    })
                else:
                    total_files += 1
                    try:
                        sz = item.stat(follow_symlinks=False).st_size
                    except OSError:
                        sz = 0
                    total_size += sz
                    entries.append({
                        "name": name,
                        "rel_path": rel_path,
                        "is_dir": False,
                        "size": sz,
                        "size_str": format_size(sz),
                        "icon": get_file_icon(name),
                    })
            except OSError:
                continue

        return entries

    tree_data = _scan_node(root)
    return {
        "root_path": str(root),
        "exists": True,
        "total_files": total_files,
        "total_dirs": total_dirs,
        "total_size": total_size,
        "total_size_str": format_size(total_size),
        "tree": tree_data,
        "is_truncated": item_count >= max_items,
    }


def _render_tree_nodes_html(nodes: List[Dict[str, Any]], depth: int = 0) -> str:
    """Render hierarchical tree nodes into HTML ul/li elements."""
    if not nodes:
        return ""

    html_parts = ['<ul class="tree-list">']
    for node in nodes:
        name_esc = html.escape(node["name"])
        rel_esc = html.escape(node["rel_path"])

        if node.get("is_dir"):
            child_html = _render_tree_nodes_html(node.get("children", []), depth + 1)
            count = len(node.get("children", []))
            html_parts.append(
                f'<li class="tree-node is-dir open" data-path="{rel_esc}">'
                f'<div class="node-row dir-row" onclick="toggleDir(this.parentElement)">'
                f'<span class="caret">▼</span>'
                f'<span class="icon">📁</span>'
                f'<span class="node-name">{name_esc}</span>'
                f'<span class="dir-badge">{count} items</span>'
                f'</div>'
                f'{child_html}'
                f'</li>'
            )
        else:
            size_str = html.escape(node.get("size_str", "0 B"))
            icon = node.get("icon", "📄")
            rel_path = node["rel_path"]
            cmd_copy = html.escape(f"/file {rel_path}")
            html_parts.append(
                f'<li class="tree-node is-file" data-path="{rel_esc}">'
                f'<div class="node-row file-row" data-cmd="{cmd_copy}" onclick="copyFileFromRow(this)">'
                f'<span class="spacer"></span>'
                f'<span class="icon">{icon}</span>'
                f'<span class="node-name">{name_esc}</span>'
                f'<span class="file-size">{size_str}</span>'
                f'<button class="copy-btn" title="Click to copy /file command" onclick="event.stopPropagation(); copyFileFromBtn(this)">'
                f'📋 Copy /file'
                f'</button>'
                f'</div>'
                f'</li>'
            )

    html_parts.append('</ul>')
    return "".join(html_parts)


def generate_dir_tree_html(
    root_path: str | Path,
    project_name: str = "",
    scan_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a responsive, standalone, mobile-first HTML document for interactive directory exploration."""
    data = scan_data or scan_directory_tree(root_path)
    root_str = str(root_path)
    proj_title = project_name or Path(root_str).name or "Workspace"
    total_files = data.get("total_files", 0)
    total_dirs = data.get("total_dirs", 0)
    total_size_str = data.get("total_size_str", "0 B")
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    tree_html = _render_tree_nodes_html(data.get("tree", []))

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>📁 {html.escape(proj_title)} - Directory Tree</title>
  <style>
    :root {{
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
      --hover-bg: rgba(255, 255, 255, 0.05);
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}

    @media (prefers-color-scheme: light) {{
      :root {{
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
        --hover-bg: rgba(0, 0, 0, 0.03);
      }}
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: var(--font-sans);
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 12px;
      -webkit-font-smoothing: antialiased;
    }}

    .container {{
      max-width: 900px;
      margin: 0 auto;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
    }}

    .header {{
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
    }}

    .header-title {{
      font-size: 1.25rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--text);
      word-break: break-word;
    }}

    .path-bar {{
      font-family: var(--font-mono);
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-top: 6px;
      word-break: break-all;
      background: var(--hover-bg);
      padding: 4px 8px;
      border-radius: 6px;
      display: inline-block;
    }}

    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}

    .badge {{
      font-size: 0.75rem;
      font-weight: 600;
      padding: 3px 8px;
      border-radius: 6px;
      background: var(--accent-bg);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.2);
    }}

    .toolbar {{
      padding: 12px 20px;
      background: var(--hover-bg);
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
    }}

    .search-box {{
      flex: 1;
      min-width: 200px;
      position: relative;
    }}

    .search-input {{
      width: 100%;
      padding: 8px 12px 8px 34px;
      font-size: 0.85rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--text);
      outline: none;
      transition: border-color 0.2s;
    }}

    .search-input:focus {{
      border-color: var(--accent);
    }}

    .search-icon {{
      position: absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 0.85rem;
      color: var(--text-muted);
    }}

    .tool-buttons {{
      display: flex;
      gap: 8px;
    }}

    .btn {{
      padding: 6px 12px;
      font-size: 0.8rem;
      font-weight: 600;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--text);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: background 0.15s;
    }}

    .btn:hover {{
      background: var(--hover-bg);
      border-color: var(--accent);
    }}

    .tip-banner {{
      padding: 8px 20px;
      background: var(--accent-bg);
      border-bottom: 1px solid var(--border);
      font-size: 0.8rem;
      color: var(--accent);
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .tree-container {{
      padding: 12px 10px;
      max-height: 72vh;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
    }}

    ul.tree-list {{
      list-style: none;
      padding-left: 18px;
    }}

    .tree-container > ul.tree-list {{
      padding-left: 0;
    }}

    .tree-node {{
      margin: 2px 0;
    }}

    .node-row {{
      display: flex;
      align-items: center;
      padding: 6px 8px;
      border-radius: 6px;
      cursor: pointer;
      user-select: none;
      transition: background 0.1s;
      gap: 6px;
      font-size: 0.88rem;
    }}

    .node-row:hover {{
      background: var(--hover-bg);
    }}

    .node-row:active {{
      background: var(--accent-bg);
    }}

    .caret {{
      width: 14px;
      font-size: 0.7rem;
      color: var(--text-muted);
      display: inline-block;
      text-align: center;
      transition: transform 0.15s;
    }}

    .spacer {{
      width: 14px;
    }}

    .tree-node.is-dir.collapsed > .dir-row .caret {{
      transform: rotate(-90deg);
    }}

    .tree-node.is-dir.collapsed > ul.tree-list {{
      display: none;
    }}

    .icon {{
      font-size: 1rem;
      line-height: 1;
    }}

    .node-name {{
      font-family: var(--font-mono);
      font-size: 0.85rem;
      word-break: break-all;
      flex: 1;
    }}

    .dir-badge {{
      font-size: 0.7rem;
      color: var(--text-muted);
      background: var(--hover-bg);
      padding: 2px 6px;
      border-radius: 4px;
    }}

    .file-size {{
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-right: 4px;
    }}

    .copy-btn {{
      padding: 3px 8px;
      font-size: 0.72rem;
      font-weight: 600;
      border-radius: 4px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--accent);
      cursor: pointer;
      opacity: 0.85;
      transition: all 0.15s;
      white-space: nowrap;
    }}

    .copy-btn:hover {{
      opacity: 1;
      background: var(--accent-bg);
      border-color: var(--accent);
    }}

    .copy-btn.copied {{
      background: var(--success-bg);
      color: var(--success);
      border-color: var(--success);
    }}

    .toast {{
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
      max-width: 90%;
      text-align: center;
      word-break: break-all;
    }}

    .toast.show {{
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }}

    .hidden-node {{
      display: none !important;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="header-title">
        <span>📁</span> <span>{html.escape(proj_title)}</span>
      </div>
      <div class="path-bar">{html.escape(root_str)}</div>
      <div class="badges">
        <span class="badge">📂 {total_dirs} Folders</span>
        <span class="badge">📄 {total_files} Files</span>
        <span class="badge">💾 {total_size_str}</span>
        <span class="badge">📅 {created_at}</span>
      </div>
    </div>

    <div class="toolbar">
      <div class="search-box">
        <span class="search-icon">🔍</span>
        <input type="text" class="search-input" id="searchBox" placeholder="Filter files or folders..." oninput="filterTree(this.value)">
      </div>
      <div class="tool-buttons">
        <button class="btn" onclick="expandAll()">⊞ Expand All</button>
        <button class="btn" onclick="collapseAll()">⊟ Collapse All</button>
      </div>
    </div>

    <div class="tip-banner">
      <span>💡</span>
      <span>Tap any file or click <b>Copy /file</b> to copy command, then paste in Telegram chat to receive the file.</span>
    </div>

    <div class="tree-container" id="treeContainer">
      {tree_html}
    </div>
  </div>

  <div id="toast" class="toast"></div>

  <script>
    function toggleDir(node) {{
      if (node.classList.contains('is-dir')) {{
        node.classList.toggle('collapsed');
      }}
    }}

    function expandAll() {{
      document.querySelectorAll('.tree-node.is-dir').forEach(el => {{
        el.classList.remove('collapsed');
      }});
    }}

    function collapseAll() {{
      document.querySelectorAll('.tree-node.is-dir').forEach(el => {{
        el.classList.add('collapsed');
      }});
    }}

    function showToast(text) {{
      const toast = document.getElementById('toast');
      toast.textContent = text;
      toast.classList.add('show');
      setTimeout(() => {{
        toast.classList.remove('show');
      }}, 2500);
    }}

    function copyFileCmd(cmd, btnEl) {{
      navigator.clipboard.writeText(cmd).then(() => {{
        showToast('✅ Copied: ' + cmd + ' (Paste in Telegram chat)');
        if (btnEl) {{
          const btn = btnEl.classList.contains('copy-btn') ? btnEl : btnEl.querySelector('.copy-btn');
          if (btn) {{
            const oldText = btn.textContent;
            btn.textContent = '✅ Copied!';
            btn.classList.add('copied');
            setTimeout(() => {{
              btn.textContent = oldText;
              btn.classList.remove('copied');
            }}, 1500);
          }}
        }}
      }}).catch(err => {{
        const ta = document.createElement('textarea');
        ta.value = cmd;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('✅ Copied: ' + cmd);
      }});
    }}

    function copyFileFromRow(rowEl) {{
      const cmd = rowEl.getAttribute('data-cmd');
      const btn = rowEl.querySelector('.copy-btn');
      if (cmd) copyFileCmd(cmd, btn);
    }}

    function copyFileFromBtn(btnEl) {{
      const row = btnEl.closest('.file-row');
      const cmd = row ? row.getAttribute('data-cmd') : null;
      if (cmd) copyFileCmd(cmd, btnEl);
    }}

    function filterTree(query) {{
      const q = query.trim().toLowerCase();
      const allNodes = document.querySelectorAll('.tree-node');

      if (!q) {{
        allNodes.forEach(n => n.classList.remove('hidden-node'));
        return;
      }}

      expandAll();

      allNodes.forEach(node => {{
        const path = (node.getAttribute('data-path') || '').toLowerCase();
        if (path.includes(q)) {{
          node.classList.remove('hidden-node');
          let parent = node.parentElement;
          while (parent && parent.id !== 'treeContainer') {{
            if (parent.classList.contains('tree-node')) {{
              parent.classList.remove('hidden-node');
              parent.classList.remove('collapsed');
            }}
            parent = parent.parentElement;
          }}
        }} else {{
          if (node.classList.contains('is-dir')) {{
            const matchingChild = node.querySelector('.tree-node:not(.hidden-node)');
            if (matchingChild) {{
              node.classList.remove('hidden-node');
            }} else {{
              node.classList.add('hidden-node');
            }}
          }} else {{
            node.classList.add('hidden-node');
          }}
        }}
      }});
    }}
  </script>
</body>
</html>
"""
    return html_doc
