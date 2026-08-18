# agy-telegram — 通过 Telegram 直接与 agy (Antigravity CLI) 对话

一个轻量网关：在 Telegram 里直接和 `agy`（Antigravity CLI / Gemini Pro）对话，
能实时看到它的中间输出，支持 `/命令`，不经过 Hermes 中转（省 token、看细节）。

## 为什么做这个

用户（Jim）有时需要针对复杂的项目直接与 agy 讨论方案、观察它的中间输出
（process.log / 思考过程 / 工具调用）。通过 Hermes 中转会：消耗 Hermes token、
且中间细节不可见。所以做一个独立的 Telegram ↔ agy 网关。

## 核心功能

1. Telegram bot 接收用户消息
2. 每次对话 / 每个任务 = 一个独立的 agy 会话（conversation_id）
3. 把 agy 的**中间输出流**（stream 模式）实时推回 Telegram，用户能看到思考/过程
4. 支持 `/` 斜杠命令控制（如新会话、上下文、模型/effort 选择）
5. 可选：配合 tasks/ 文件系统工作区（process.log/state.md），过程持久化可追溯

## 技术方向（供 agy task 参考）

- 语言：Python
- Telegram：直接用 **Bot API**（httpx/python-telegram-bot 或原生 HTTP + long polling）
- 桥接 agy：调用 `agy --output-format stream-json --print ...`，把 stream 转发成 Telegram 消息（分块/编辑消息，避免刷屏）
- 会话管理：每个 Telegram chat 一行 agy conversation；`/new` 另起
- 认证：bot token 天然隔离；如需额外白名单可加 chat_id 过滤

## 文档

- 约定：见 `tasks/README_tasks/`（若有）
- 任务历史：`tasks/taskNN/`

## 状态

开发中，见 tasks/ 各任务。
