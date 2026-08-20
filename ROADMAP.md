# agy-telegram Development Roadmap

This document outlines the milestones and roadmap for integrating new Google Antigravity CLI (`agy` v1.1.15) capabilities into the `agy-telegram` gateway.

---

## Roadmap Items

### 📋 Milestone 1: Enhanced Real-Time Tool Execution & Spinner Hints
- [x] **1.1 Comprehensive Tool Support**: Expand `format_tool_summary` to cover all AGY tools (`invoke_subagent`, `manage_subagents`, `send_message`, `define_subagent`, `read_url_content`, `generate_image`, `schedule`, `manage_task`, `ask_question`).
- [x] **1.2 Real-time Tool Spinner & Elapsed Time**: Display dynamic live status indicators (e.g. `⚙️ grep_search ('pattern')... (1.4s)`) during active execution and exact duration upon completion.
- [x] **1.3 Multi-Agent & Subagent Visual Hierarchy**: Surface subagent roles and delegated workflows clearly in Telegram streaming messages.
- [x] **1.4 Unit & Integration Tests**: Add complete unit tests covering all tool formatting cases and streaming status.

---

### 📋 Milestone 2: Declarative Agent Rules Integration
- [x] **2.1 Output Protocol Rule Definition**: Create `.gemini/rules/telegram-output.md` containing the mobile-first HTML output standards and `[AGY_FILE]` protocol specification.
- [x] **2.2 Clean Prompt Architecture**: Decouple the ~50 line system prompt from user prompt text; load formatting rules cleanly via AGY rule discovery and agent configurations.
- [x] **2.3 Fallback Compatibility**: Ensure full backward compatibility when running in standalone mode or custom environments without local rule files.
- [x] **2.4 Testing & Validation**: Validate prompt generation and rule enforcement with automated tests.

---

### 📋 Milestone 3: Long-Lived Session Process Pool & Multi-Turn Stream-JSON
- [x] **3.1 Persistent Subprocess Daemon**: Keep `agy` subprocess alive across turns via `--input-format stream-json` rather than killing and respawning per message.
- [x] **3.2 Idle Timeout & Lifecycle Management**: Implement automatic idle cleanup (e.g., 5-minute timeout), graceful shutdown on `/stop`, `/reset`, or project/model switch.
- [x] **3.3 Concurrency & Steering Safety**: Maintain thread-safe stdin writing for prompt turns and mid-flight `/steer` inputs.
- [x] **3.4 Performance & Latency Benchmarks**: Measure and verify cold-start vs. warm-session latency improvements.

### 📋 Milestone 4: Quota Monitor, Skills Explorer, Artifacts & Adaptive LRU Command Menu
- [x] **4.1 Real-Time Quota & Usage Monitor (`/usage`, `/quota`)**: 0-token instant query of Google Antigravity weekly / 5-hour rate limits, reset timestamps, and AI credits with graphical progress bars.
- [x] **4.2 Agent Skills & Plugins Explorer (`/skills`, `/plugins`)**: Discover and list all loaded builtin and plugin capabilities.
- [x] **4.3 Conversation Artifacts Manager (`/artifacts`, `/scratch`)**: Browse and download reports, code diffs, and scratch scripts with 1-tap buttons.
- [x] **4.4 Adaptive LRU Command Menu & Interactive Center**: Keep `/help` pinned at top, dynamically bubble frequently used commands up, and limit mobile popup menu to top 8 items.

### 📋 Milestone 5: Telegram Interactive UI & Rich Display Evolution
- [x] **5.1 Dynamic Decision Option Buttons & `ask_question` Integration**: Automatically convert AGY `ask_question` tool options and `[AGY_ACTIONS]` suggested next steps into clickable Telegram Inline Keyboards; trigger follow-ups on tap without typing.
- [x] **5.2 Collapsible Logs & Expandable Blockquotes (`<blockquote expandable>`)**: Support Telegram Bot API 7.3+ `<blockquote expandable>` for long command outputs and progress traces to eliminate mobile screen clutter.
- [x] **5.3 Instant Message Reaction Feedback (`setMessageReaction`)**: Provide millisecond-level emoji feedback (`👀` on prompt received, `⚡` on tool execution, `✅` on success, `⚠️` on error).
- [x] **5.4 Dynamic Slash Command Dispatcher & LRU Integration**: Centralized slash command routing with persistent statistics and instant execution.
- [x] **5.5 Unit & Integration Testing**: 109 automated tests passing with complete coverage across interactive buttons, expandable blocks, reactions, and callbacks.

### 📋 Milestone 6: Telegram Mini App (Web App) Viewer & Advanced UI Extensions
- [x] **6.1 Telegram Mini App (Web App) In-Chat Popup Viewer**: Integrated a lightweight zero-dependency embedded WebApp server with `WebAppInfo(url=...)` enabling instant slide-up viewing of HTML reports, Git diffs, and directory navigators directly inside Telegram without file downloading.
- [x] **6.2 Dynamic Input Field Placeholder State Machine**: Persistent reply keyboard with dynamic `input_field_placeholder` and quick navigation shortcuts.
- [ ] **6.3 Pinned Project & Session Header Banner (`pinChatMessage`)**: Maintain a persistent pinned banner at the top of the chat showing the active project workspace, current model, reasoning effort, and quick navigation shortcuts.
- [ ] **6.4 Supergroup Forum Topics Multi-Project Mapping**: Automatically bind separate projects to distinct Telegram Forum Topics/threads (`message_thread_id`) when used in group workspaces.

---

## Status Log

| Milestone | Feature Description | Status | Completed At |
| :--- | :--- | :--- | :--- |
| **Milestone 1** | Enhanced Tool Execution & Spinner Hints | ✅ Completed | 2026-08-19 |
| **Milestone 2** | Declarative Agent Rules Integration | ✅ Completed | 2026-08-19 |
| **Milestone 3** | Long-Lived Session Process Pool | ✅ Completed | 2026-08-19 |
| **Milestone 4** | Quota, Skills, Artifacts & Adaptive LRU Menu | ✅ Completed | 2026-08-19 |
| **Milestone 5** | Interactive UI (Buttons, Expandable, Reactions) | ✅ Completed | 2026-08-19 |
| **Milestone 6 (6.1, 6.2)** | Mini App Webview Viewer & Dynamic Placeholders | ✅ Completed | 2026-08-19 |
| **Milestone 6** | Mini App Viewer, Placeholders & Pinned Banner | ⏳ Planned | - |
