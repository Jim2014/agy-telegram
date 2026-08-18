# agy-telegram — 通过 Telegram 直接与 agy (Antigravity CLI) 对话

一个轻量、实时的 Telegram 网关：在 Telegram 里直接和 `agy`（Antigravity CLI / Gemini 2.5 Pro）对话，实时查看**思考过程、工具调用与中间流式输出**，支持**中途引导（Steering）**与 `/` 斜杠命令控制，不经过 Hermes 中转（省 token、看细节）。

---

## 目录
- [核心特性](#核心特性)
- [运行环境准备](#运行环境准备)
  - [选项 A: 使用 Conda 环境 (推荐，环境名: agy-telegram)](#选项-a-使用-conda-环境-推荐环境名-agy-telegram)
  - [选项 B: 使用 Python venv](#选项-b-使用-python-venv)
- [运行时目录与文件隔离 (runtime/)](#运行时目录与文件隔离-runtime)
- [配置指南 (.env)](#配置指南-env)
  - [第一步: 获取 Telegram Bot Token](#第一步-获取-telegram-bot-token)
  - [第二步: 配置 .env](#第二步-配置-env)
- [启动与运行](#启动与运行)
- [Telegram 命令与交互指南](#telegram-命令与交互指南)
  - [支持的命令列表](#支持的命令列表)
  - [中途引导 (Steering) 工作机制](#中途引导-steering-工作机制)
- [架构与模块说明](#架构与模块说明)
- [测试与验证](#测试与验证)

---

## 核心特性

1. **Telegram Long-Polling**: 基于 Telegram Bot API，无需公网 IP 或 Webhook 配置。
2. **实时流式与中间过程可视化**: 实时捕获 agy 的 `stream-json` 事件，展示工具调用（`run_command`, `list_dir`, `view_file` 等）、思考状态并限频推回 Telegram。
3. **中途引导 (Steering) 支持**: 当 agy 正在思考或执行多步工具时，无需中断或 `/stop` 整个会话，系统可直接将补充指引与追加要求 **Steer（注入）** 进当前运行会话，平滑接续执行。
4. **项目化会话持久化与隔离**: 支持多项目工作区管理（`/new`, `/open`, `/delete`, `/reset`, `/projects`），每个项目独立保存 `path` 与 `conversation_id`，跨项目持久化无缝切换。
5. **灵活参数调整**: 通过 `/model` 切换模型，通过 `/effort` 调整推理强度 (low/medium/high)，通过 `/dir` 调整当前项目工作区。
6. **运行时隔离与 Git 保护**: 所有运行时日志（`runtime/gateway.log`）、进程 PID（`runtime/gateway.pid`）、会话持久化（`runtime/sessions.json`）均自动归档在 `runtime/` 目录，已加入 `.gitignore` 彻底杜绝误提交。
7. **安全与健壮性 (Harden & Auto-Heal)**:
   - 支持 `ALLOWED_CHAT_IDS` 白名单鉴权。
   - **Telegram 网络异常免疫**：所有 Telegram API 调用统一走安全封装，遭遇 `httpx.ReadError`、超时、网络瞬断时自动降级并不中断流式任务或退出主进程。
   - **看门狗崩溃自动拉起 (Watchdog Supervisor)**：提供独立的看门狗守护进程（`runtime/watchdog.sh` / `./restart.sh watch`），周期巡检进程状态，意外退出后 10 秒内自动拉起。
   - 保留 `/stop` 供强制中断正在执行的 agy 子进程。
   - 自动处理 Telegram 4096 字符截断、限频 (Throttling) 与 HTML 标签容错降级。
   - 全套单元测试、网络抗逆测试与 agy 冒烟测试。

---

## 运行环境准备

### 选项 A: 使用 Conda 环境 (推荐，环境名: `agy-telegram`)

已提供 `environment.yml`（Python 3.10）：

```bash
cd /Users/jim/hermes-shared/agy-telegram

# 1. 创建并激活 conda 环境
conda env create -f environment.yml
conda activate agy-telegram

# 2. 安装依赖 (如未自动安装)
pip install -r requirements.txt
```

### 选项 B: 使用 Python venv

```bash
cd /Users/jim/hermes-shared/agy-telegram

# 1. 创建虚拟环境
python3 -m venv venv

# 2. 安装依赖
./venv/bin/pip install -r requirements.txt
```

---

## 运行时目录与文件隔离 (`runtime/`)

为了保持代码库整洁并防止运行时动态文件被提交到 Git：

```
agy-telegram/
├── runtime/              # 运行时动态数据目录 (已加入 .gitignore)
│   ├── gateway.log       # 运行日志
│   ├── gateway.pid       # 守护进程 PID 文件
│   ├── watchdog.sh       # 看门狗自动拉起守护脚本
│   ├── watchdog.log      # 看门狗运行与自愈日志
│   ├── watchdog.pid      # 看门狗 PID 文件
│   └── sessions.json     # 跨会话与多项目持久化数据
├── gateway/              # 核心网关 Python 模块
├── tests/                # 单元测试与冒烟测试
├── restart.sh            # 服务与看门狗启停控制脚本
├── environment.yml       # Conda 运行环境定义
└── README.md
```

- 系统启动时会自动确保 `runtime/` 目录存在。
- 若存在旧版本根目录下的 `sessions.json`，系统会自动兼容迁移加载。

---

## 配置指南 (.env)

### 第一步: 获取 Telegram Bot Token

1. 打开 Telegram，搜索官方账号 [@BotFather](https://t.me/BotFather)。
2. 发送 `/newbot` 指令，按照提示输入 Bot 的名称与 username（例如 `my_agy_bot`）。
3. BotFather 会生成形如 `123456789:ABCdefGHIjklMNOpqrsTUVwxyz` 的 API Token，复制该 Token。

### 第二步: 配置 `.env`

从模板创建 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 内容：

```ini
# 必填: 从 @BotFather 获取的 Bot Token
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# 可选: 允许访问的 Telegram Chat ID 列表(逗号分隔)。留空则允许所有用户
# 可在 Telegram 搜索 @userinfobot 获取自己的 Chat ID
ALLOWED_CHAT_IDS=

# agy CLI 路径 (默认为 /Users/jim/.local/bin/agy 或系统 PATH 中的 agy)
AGY_BIN_PATH=/Users/jim/.local/bin/agy

# 默认工作空间 (--add-dir)，留空默认使用当前仓库根目录
AGY_WORKING_DIR=/Users/jim/hermes-shared/agy-telegram

# 默认模型与推理强度
DEFAULT_MODEL=
DEFAULT_EFFORT=medium

# 消息流式刷新限频间隔 (秒，默认 1.0)
STREAM_THROTTLE_SECONDS=1.0

# 单次请求超时时间 (秒，默认 300)
AGY_TIMEOUT_SECONDS=300

# 日志级别 (INFO / DEBUG) 与路径
LOG_LEVEL=INFO
LOG_FILE=runtime/gateway.log
SESSIONS_FILE=runtime/sessions.json
```

---

## 启动与运行

### 1. 检查配置有效性

```bash
conda run -n agy-telegram python main.py --check-config
# 或使用脚本:
./restart.sh --check
```

### 2. 运行 agy 真实冒烟测试

验证本地 `agy` CLI 能否正常调用并输出流式 JSON：

```bash
conda run -n agy-telegram python main.py --smoke-test
```

### 3. 启动与管理服务

使用后台管理脚本 `restart.sh`：

```bash
# 启动 / 重启网关
./restart.sh

# 查看网关与看门狗运行状态
./restart.sh status

# 启动崩溃自动拉起看门狗 (Watchdog)
./restart.sh watch

# 停止网关与看门狗
./restart.sh stop

# 仅停止看门狗
./restart.sh stop-watch
```

日志文件输出在 `runtime/gateway.log` 与 `runtime/watchdog.log`：
```bash
tail -f -n 50 runtime/gateway.log
tail -f -n 50 runtime/watchdog.log
```

---

## Telegram 命令与交互指南

### 支持的命令列表

在 Telegram 中与你的 Bot 对话：

| 命令 | 说明 | 示例 |
| :--- | :--- | :--- |
| **直接发送文本** | 处于空闲时启动新轮次；处于运行中时**自动作为 Steering 注入当前会话** | `请帮我查看当前目录下的文件` |
| `/steer <instruction>` | 显式向正在执行的任务注入引导指令（无需打断当前进程） | `/steer 请使用异步写法` |
| `/stop` | 强制中断并杀死当前正在执行的 agy 进程 | `/stop` |
| `/file <path>` | 发送当前项目工作区中的指定文件至 Telegram 客户端 | `/file README.md` |
| `/export` | 将当前会话状态或最新输出导出为独立排版的 HTML 文件并发送 | `/export` |
| `/new <name> <path>` | 创建新项目并设为当前活跃工作区（已存在则切换） | `/new myapp /Users/jim/myapp` |
| `/open <name>` | 打开并切换到已有项目，恢复其历史会话 | `/open myapp` |
| `/projects` | 列出所有项目、活跃标记、工作区路径与最后活跃时间 | `/projects` |
| `/delete <name>` | 删除指定项目（如为活跃项目则重置活跃状态） | `/delete myapp` |
| `/reset` | 清空当前活跃项目的 `conversation_id`（重置会话） | `/reset` |
| `/dir <path>` | 修改当前活跃项目的工作目录路径 | `/dir /Users/jim/my-project` |
| `/status` | 查看当前活跃项目、会话 ID、模型、推理强度及运行状态 | `/status` |
| `/model <name>` | 切换当前对话使用的模型（或 `/model reset` 恢复默认） | `/model gemini-2.5-pro` |
| `/effort <level>` | 调整推理强度：`low`、`medium`、`high` | `/effort high` |
| `/help` | 查看帮助说明与命令列表 | `/help` |

### 中途引导 (Steering) 与系统提示词注入机制

- **会话首轮系统提示词自动注入 (System Prompt Injection)**：
  网关在每次启动全新 `agy` 会话（首轮）时，自动注入预设的高质量系统提示词，规范 `agy` 输出行为：
  1. **简短/普通回答**：直接使用 Markdown 纯文本回复（网关自动转为 Telegram 富文本 HTML，窄屏阅读友好）。
  2. **长篇/复杂内容（Diff、测试报告、长代码、大表格、调研报告等）**：引导 `agy` 生成自包含、移动友好的独立单页 HTML，并通过 `[AGY_FILE]` 协议交由网关作为附件发送。
  3. **会话级持久与防重复注入**：系统提示仅在会话首轮（或 `/reset` 重置后的首轮）注入，后续多轮对话及 mid-flight Steering 不会重复追加提示词；提示词仅供模型理解，完全不污染 Telegram 用户的正文输出。
  4. **灵活配置支持**：默认内置开箱即用的系统提示词规范；支持在 `.env` 中使用 `AGY_SYSTEM_PROMPT`（支持 `\n` 多行）或 `AGY_SYSTEM_PROMPT_FILE`（指向外部 Markdown/文本文件）进行自定义覆盖。

- **Steering 引导**：当 agy 正在执行耗时任务时，用户直接输入新消息或发送 `/steer <指示>`，网关利用 `stream-json` 管道向 `stdin` 注入指令，Agent 将在完成当前步后无缝吸纳新指令继续作答。
- **显式文件导出协议 (`[AGY_FILE]`)**：
  agy 可在回复中输出结构化块，显式指示网关生成并发送移动端友好的单页 HTML 文件附件：
  ```text
  [AGY_FILE]
  filename: diff_report.html      # 可选，导出的文件名（默认 report.html）
  title: Code Diff Report         # 可选，HTML 页面标题（默认 agy Response）
  layout: mobile                  # 可选，移动优先布局（默认 mobile）
  ---
  <文件内容>
  [END_AGY_FILE]
  ```
  `<文件内容>` 支持三种模式：
  1. **Markdown**：将 Markdown 文本通过网关渲染为移动优先的独立单页 HTML（带代码高亮与 Diff 增删染色）。
  2. **原始 HTML (Raw HTML)**：直接提供完整 HTML 或片段，网关自动补充移动端 `viewport` 与响应式样式。
  3. **文件路径 (File Path)**：提供工作区相对或绝对路径（如 `runtime/artifacts/report.html`），网关自动读取该文件并发送（文件不存在时友好提示错误且不崩溃）。
  - **正文清洁**：网关在发送前自动从可见正文中剔除 `[AGY_FILE]` 块，正文仅保留摘要预览与已发送提示。
  - **多文件支持**：单次回复支持多个 `[AGY_FILE]` 块，一次性导出多个文件。
  - **移动优先**：内置 `<meta name="viewport" content="width=device-width, initial-scale=1.0">`、自适应容器（适配 360~430px 屏幕）、代码与表格水平横向滚动、深浅色模式自适应。
- **启发式兜底导出**：无显式 `[AGY_FILE]` 标记时，若输出为大 Diff 或超长文本（> 2000 字），仍自动通过启发式规则导出为 HTML 文件。
- **直接提取工作区文件**：输入 `/file <路径>` 可直接调取并下载工作区内的任意代码或配置文件。

---

## 架构与模块说明

每个 Python 模块均设计为高内聚、低耦合，且均支持独立命令行测试（规则 6）：

- `gateway/config.py` (`python -m gateway.config`): 配置加载与校验，包含 `DEFAULT_SYSTEM_PROMPT` 定义与 `AGY_SYSTEM_PROMPT` / `AGY_SYSTEM_PROMPT_FILE` 覆盖解析。
- `gateway/session_manager.py` (`python -m gateway.session_manager`): 会话状态管理与持久化 (`runtime/sessions.json`)。
- `gateway/message_formatter.py` (`python -m gateway.message_formatter`): Markdown 到 Telegram 原生富文本 HTML 转换、`[AGY_FILE]` 显式协议解析与提取、移动优先独立 HTML 报告生成（Diff 染色高亮、深浅色自适应）与分块截断。
- `gateway/agy_bridge.py` (`python -m gateway.agy_bridge "<prompt>"`): `agy` 子进程异步双向通信、`stream-json` 双向流解析、首轮系统提示词精准注入、Steering 注入与进程生命周期管理。
- `gateway/telegram_client.py` (`python -m gateway.telegram_client`): 异步 Telegram Bot API 客户端（Long polling、流式编辑、`sendDocument` 文件多部件上传、429 限流退避）。
- `gateway/bot.py` (`python -m gateway.bot`): 调度中心、斜杠命令路由、系统提示词注入调度、Steering 路由、`[AGY_FILE]` 显式多文件导出与启发式兜底导出调度。
- `main.py` (`python main.py`): 启动入口与 CLI 参数控制。

---

## 测试与验证

运行全部单元测试及集成冒烟测试：

```bash
conda run -n agy-telegram pytest -v
# 或
./venv/bin/pytest -v
```

测试覆盖范围（66 项全部通过）：
- `test_config.py`: 配置解析、Token 格式验证、白名单过滤、默认 runtime 路径、默认系统提示词内容校验、环境变量及文件覆盖测试
- `test_session_manager.py`: 会话 CRUD、持久化与恢复、并发状态保护、Legacy 迁移
- `test_message_formatter.py`: HTML 实体转义、Markdown 转 Telegram HTML、代码高亮、标签自闭合、表格转换、独立移动 HTML 文档导出、文件导出条件判断、`sanitize_filename`、`detect_content_mode`、`parse_agy_file_block`、`extract_agy_file_blocks`、`render_agy_file` 三种模式（Markdown / Raw HTML / File Path）、移动端视口与响应式元素验证
- `test_agy_bridge.py`: 命令构建、stream-json 协议解析、Mock 管道流、首轮系统提示词精准注入、次轮跳过注入、`steer()` 注入与 `is_running()` 状态判断
- `test_telegram_client.py`: API 调用、HTML 实体解析失败自动降级、304 抑制、`send_document` 多部件上传
- `test_bot.py`: 所有斜杠命令路由、未授权拦截、流式处理管线、首轮系统提示词注入与次轮多轮会话传递、`/steer` 引导、`/file` 文件发送、`/export` 导出、代码 Diff 启发式自动生成 HTML 附件、`[AGY_FILE]` 显式协议导出（Markdown / Raw HTML / File Path / Missing File / 多文件）、`/stop` 中断、Telegram 网络异常抗逆与自动降级保护
- `test_smoke_agy.py`: 真实的 `agy` CLI 联调冒烟测试


