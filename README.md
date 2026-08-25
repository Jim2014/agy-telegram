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
2. **实时流式与中间过程可视化**: 实时捕获 agy 的 `stream-json` 事件，展示丰富工具调用（`run_command`, `view_file`, `grep_search`, `invoke_subagent`, `read_url_content`, `generate_image`, `schedule` 等）及其执行耗时，并在 Telegram 中动态高亮实时状态。
3. **中途引导 (Steering) 与长连接进程池 (Session Daemon)**: 当 agy 正在思考或执行多步工具时，可通过 `/steer` 或直接发消息将指令注入当前会话；支持 `--input-format stream-json` 多轮长连接保活，消弭每轮冷启动开销。
4. **声明式 Agent 规则定制**: 遵循 AGY v1.1.15 规范，采用 `.gemini/rules/telegram-output.md` 与 `.gemini/rules.json` 声明格式与 `[AGY_FILE]` 导出规则，彻底净化用户 Prompt 历史并维持系统级约束。
5. **项目化会话持久化与隔离**: 支持多项目工作区管理（`/new`, `/open`, `/delete`, `/reset`, `/projects`），每个项目独立保存 `path` 与 `conversation_id`，跨项目持久化无缝切换。
6. **灵活参数调整**: 通过 `/model` 切换模型，通过 `/effort` 调整推理强度 (low/medium/high)，通过 `/dir` 调整当前项目工作区。
7. **语音消息与音频交互 (Voice Messages & Faster-Whisper STT / TTS Broadcast)**: 支持直接在 Telegram 中发送语音条或音频文件，网关自动接收并下载至 `runtime/voice/` 目录，通过内置 `faster-whisper` 离线语音识别引擎秒级转录为文本后交由 `agy` 执行，彻底杜绝大模型因直接读取二进制音频导致上下文损坏的问题；在任务运行中发送语音亦可实时转录并 Steering 注入当前会话；内置动态双语 TTS 引擎，支持在文本与 HTML 报告消息下方常驻 `[ 🔊 Send Audio ]` 按需语音播报按钮，或自动合成语音条。
8. **文件与图片附件交互 (Document & Photo Attachments & Mini App WebApp)**: 支持在 Telegram 中直接发送文档、代码文件、数据表格或图片，网关自动接收下载至 `runtime/documents/` 并将文件绝对路径与元数据传给 `agy` 深度分析与处理；支持中途 Steering 注入；生成的 HTML 文档与报告自动集成内置 WebApp 服务器（`:8765`），附带 `[ 🌐 Open in Telegram ]` 与 `[ 🔊 Send Audio ]` 交互按钮。
9. **运行时隔离与 Git 保护**: 所有运行时日志（`runtime/gateway.log`）、进程 PID（`runtime/gateway.pid`）、会话持久化（`runtime/sessions.json`）、语音缓存（`runtime/voice/`）与文件附件（`runtime/documents/`）均自动归档在 `runtime/` 目录，已加入 `.gitignore` 彻底杜绝误提交。
10. **安全与健壮性 (Harden & Auto-Heal)**:
    - 支持 `ALLOWED_CHAT_IDS` 白名单鉴权。
    - **Telegram 网络异常免疫**：所有 Telegram API 调用统一走安全封装，遭遇 `httpx.ReadError`、超时、网络瞬断时自动降级并不中断流式任务或退出主进程。
    - **看门狗崩溃自动拉起 (Watchdog Supervisor)**：提供独立的看门狗守护进程（`runtime/watchdog.sh` / `./restart.sh watch`），周期巡检进程状态，意外退出后 10 秒内自动拉起。
    - 保留 `/stop` 供强制中断正在执行的 agy 子进程。
    - 自动处理 Telegram 4096 字符截断、限频 (Throttling) 与 HTML 标签容错降级。
    - 全套单元测试（144 项全覆盖）、网络抗逆测试与 agy 冒烟测试。详细演进规划请参考 [ROADMAP.md](ROADMAP.md)。
11. **跨项目多智能体通信协议 (`AGY_IPC`) 与专属置顶卡片管理**:
    - **跨工作区去中心化协同**：支持智能体通过 `[AGY_IPC to="<target>"]...[END_AGY_IPC]` 发起跨目录/跨项目任务派发与信封包装（`[AGY_INCOMING]`），结果自动回流主对话。
    - **专属独立置顶折叠卡片**：Telegram 顶部为每个目标项目维护独立固定的原生折叠卡片（`<blockquote expandable>`），流式追踪工具执行与耗时。
    - **一键交互式 Unpin 按钮**：卡片附带 `[ 📌 Unpin ]` 按钮，点击一键调用 Telegram Bot API 取消置顶并同步网关状态。
    - **独立审计日志**：在 `runtime/ipc.log` 完整记录跨项目 `DISPATCH`、`INIT`、`TOOL` 与 `COMPLETE` 全生命周期。

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

### 🤖 自适应斜杠菜单与命令中心 (Adaptive Pure LRU Command Menu)

网关采用**自适应纯 LRU (Least Recently Used) 算法与置顶设计**，确保所有命令全部入驻 Telegram 菜单，且最近使用的命令自动排在最前面：
- **📌 `/help` 永远置顶固定**：无论如何排序，`/help` 始终固定位于第 0 位，点击可呼出集成所有子系统的**交互式命令中心仪表盘**（带快捷一键点击按钮）。
- **⚡ 纯 LRU 算法智能重排**：网关后台动态记录当前 Chat 各命令的最近使用时间戳（`last_used_timestamp`），**最后用到的命令会自动浮动到最前面（紧跟 `/help` 之后）**；未使用的命令按默认优先级排序。
- **📱 全命令完整入驻菜单**：全部 19 条支持的斜杠命令均完整注册到 Telegram `/` 弹出菜单中，用户在对话中键入任意命令或别名后，菜单秒级自适应更新当前 Chat 的命令顺序。

- **📌 顶部常驻工作区看板 (Pinned Status Banner)**：在聊天顶部自动置顶当前工作区看板（项目名、路径、模型、Token 统计、存活时长与状态），每次切换项目或每轮任务完成时静默自动刷新，手机抬头即见，自带 `[ 📊 Status ]`、`[ 📁 Projects ]`、`[ 🪙 Quota ]`、`[ 📖 Help ]` 快捷入口。
- **/help 命令中心**：展示所有快捷功能入口（`[ 📊 Status ]`、`[ 🪙 Quota & Usage ]`、`[ 📁 Projects ]`、`[ 🧠 Model & Effort ]`、`[ 🧩 Skills & Tools ]`、`[ 📦 Artifacts ]`、`[ 📁 File Tree ]`、`[ 📄 Export ]`）。
- **🔘 对话决策与后续操作选项 (Decision Flow & Action Buttons)**：
  - 当 agy 调用 `ask_question` 工具或在末尾返回建议操作时，直接在消息下方渲染交互式行内按钮（如 `[ 🧪 运行单元测试 ]`、`[ 📝 查看 Diff ]`、`[ 🚀 提交变更 ]`），点击一键执行，无需在手机上打字！
- **📑 原生可折叠工具进度 (`<blockquote expandable>`)**：长日志、多工具流执行过程自动包裹在可折叠块中，手机端默认仅占 3 行高度，点击即可就地展开。
- **⚡ 即时消息 Reaction 反馈**：用户发送提示后立即反应 `👀`，执行工具反应 `⚡`，成功完成反应 `✅`，遇到异常反应 `⚠️`。
- **/usage 配额看板**：直观展示 Gemini 7天周限制与 5小时速率限制剩余百分比（进度条格式）、重置倒计时与 AI 积分余额，附带 `[ 🔄 Refresh Quota ]` 与 `[ 🧠 Switch Model ]`。
- **/skills 技能库**：分类展示当前 Agent 具备的 Builtin 及各插件（Firebase, Web Guidance, Xcode, AGY SDK 等）技能总览。
- **/artifacts 产物管理**：列出会话生成的 HTML 报告、图表与 `scratch/` 脚本，附带 `[ 📥 一键下载 ]` 按钮。
- **/projects 项目菜单**：附带每个项目的点击按钮（如 `[ 🌟 myapp ]`、`[ 📂 backend ]`）及 `[ 🔄 Refresh ]`，一键切换工作区。
- **/effort 推理强度**：附带 `[ 🟢 Low ]`、`[ 🟡 Medium ]`、`[ 🔴 High ]` 快速选择按钮，即时生效。
- **/model 模型切换**：展示当前激活模型与所有模型选项，附带 `[ ⚡ Gemini 3.7 Flash ]`、`[ 🧠 Gemini 3.1 Pro ]` 按钮。

### 支持的命令列表

在 Telegram 中与你的 Bot 对话：

| 命令 / 交互 | 说明 | 示例 |
| :--- | :--- | :--- |
| **直接发送文本** | 处于空闲时启动新轮次；处于运行中时**自动作为 Steering 注入当前会话** | `请帮我查看当前目录下的文件` |
| **🎙️ 发送语音 / 音频** | 自动下载至本地并通过 faster-whisper 离线识别为文本后交由 agy 执行；运行中发送则自动 Steering | 🎙️ 发送一段语音指令 |
| **📎 发送文件 / 图片** | 自动下载至本地 `runtime/documents/`，将路径与文件元数据传给 agy 检查与处理；支持中途 Steering | 📎 发送代码/文档/图片附件 |
| `/voice [mode]` | 配置或切换语音播报模式：`disable`（禁用）、`autogen`（按需播放）、`autoplay`（自动发送语音条） | `/voice` 或 `/voice autoplay` |
| `/pin` 或 `/banner` | 重新生成并置顶当前工作区实时状态看板至 Telegram 聊天窗口顶部 | `/pin` |
| `/help` | 查看帮助说明与交互式命令中心仪表盘（永远置顶） | `/help` |
| `/usage` 或 `/quota` | **0 Token 实时查询** Google Antigravity 周配额、5小时限制剩余率与重置时间、AI 积分 | `/usage` |
| `/skills` 或 `/plugins` | 查看当前工作区已注册的所有 Agent 技能与插件工具清单 | `/skills` |
| `/artifacts [序号]` | 浏览当前会话产生的独立报告、代码 diff 与调试脚本，或指定序号直接下载 | `/artifacts` 或 `/artifacts 1` |
| `/status` | 查看当前活跃项目、会话累计统计（轮次、累计 Token、思考 Token、会话存活时长、总运行耗时）、模型及系统状态 | `/status` |
| `/projects` | 列出所有项目、活跃标记、工作区路径与最后活跃时间 | `/projects` |
| `/open <name>` | 打开并切换到已有项目，恢复其历史会话与统计 | `/open myapp` |
| `/new <name> <path>` | 创建新项目并设为当前活跃工作区（目录不存在则自动递归创建 `mkdir -p` 并提示；已存在则切换） | `/new myapp /Users/jim/myapp` |
| `/model <name>` | 切换当前对话使用的模型（如 `/model gemini-3.7-flash` 或 `/model gemini-3.1-pro`） | `/model gemini-3.7-flash` |
| `/effort <level>` | 调整推理强度：`low`、`medium`、`high` | `/effort high` |
| `/dir` 或 `/ls [path]` | 扫描工作区目录并生成交互式折叠 HTML 文件树，支持一键点击复制 `/file` 与 `/file -h` 指令 | `/dir` 或 `/ls tests` |
| `/file [-h] <path>` | 发送工作区文件；使用 `-h` 参数动态转换为对应格式的独立 HTML 文件（如 `bot.py.html`、`README.md.html`） | `/file -h bot.py` |
| `/export` | 将当前会话状态或最新输出导出为独立排版的 HTML 文件并发送 | `/export` |
| `/steer <instruction>` | 显式向正在执行的任务注入引导指令（无需打断当前进程） | `/steer 请使用异步写法` |
| `/stop` | 强制中断并杀死当前正在执行的 agy 进程 | `/stop` |
| `/reset` | 清空当前活跃项目的 `conversation_id` 并重置会话统计为零（开启全新会话） | `/reset` |
| `/delete <name>` | 删除指定项目（如为活跃项目则重置活跃状态） | `/delete myapp` |

### 📄 文件多模板 HTML 动态渲染 (`/file -h`)

使用 `/file -h <path>` 时，网关会根据文件后缀名自动匹配专有渲染模板并输出 `<filename>.html`：
- **代码文件** (`.py`, `.js`, `.ts`, `.html`, `.css`, `.json`, `.yaml`, `.sql`, `.sh`, `.rs`, `.go` 等)：生成带行号、语法着色高亮、一键代码复制和移动端自适应排版的 HTML 视图。
- **Markdown 文件** (`.md`, `.markdown`)：解析并渲染为排版优美的排版文章视图（标题、列表、富文本、表格、代码块）。
- **Diff / Patch 文件** (`.diff`, `.patch`)：统一补丁视图，绿底新增行、红底删除行、行号与变更块高亮。
- **表格数据文件** (`.csv`, `.tsv`)：解析并渲染为响应式数据表格，支持表头固定与行计数。
- **纯文本 / 日志 / 配置** (`.txt`, `.log`, `.env*`, `.gitignore`)：等宽控制台文本视图与行号。
- **二进制 / 不支持的文件** (如图片、音视频、压缩包等)：自动优雅回退并发送原始原文件，不进行无效转换。

### 中途引导 (Steering) 与系统提示词注入机制

- **会话首轮系统提示词自动注入 (System Prompt Injection)**：
  网关在每次启动全新 `agy` 会话（首轮）时，自动注入预设的高质量系统提示词，规范 `agy` 输出行为：
  1. **简短/普通回答**：直接使用 Markdown 纯文本回复（网关自动转为 Telegram 富文本 HTML，窄屏阅读友好）。
  2. **长篇/复杂内容（Diff、测试报告、长代码、大表格、调研报告等）**：引导 `agy` 生成自包含、移动友好的独立单页 HTML，并通过 `[AGY_FILE]` 协议交由网关作为附件发送。
  3. **会话级持久与防重复注入**：系统提示仅在会话首轮（或 `/reset` 重置后的首轮）注入，后续多轮对话及 mid-flight Steering 不会重复追加提示词；提示词仅供模型理解，完全不污染 Telegram 用户的正文输出。
  4. **灵活配置支持**：默认内置开箱即用的系统提示词规范；支持在 `.env` 中使用 `AGY_SYSTEM_PROMPT`（支持 `\n` 多行）或 `AGY_SYSTEM_PROMPT_FILE`（指向外部 Markdown/文本文件）进行自定义覆盖。

- **Steering 引导**：当 agy 正在执行耗时任务时，用户直接输入新消息或发送 `/steer <指示>`，网关利用 `stream-json` 管道向 `stdin` 注入指令，Agent 将在完成当前步后无缝吸纳新指令继续作答。
- **直接生成与发送 HTML 文件 (`[AGY_FILE]`)**：
  agy 直接生成自包含、移动友好的独立单页 HTML（或直接生成工作区 HTML 文件），通过 `[AGY_FILE]` 协议交由网关直接发送，无需以 Markdown 作为中间格式转换：
  ```text
  [AGY_FILE]
  filename: diff_report.html      # 可选，导出的文件名（默认 report.html）
  title: Code Diff Report         # 可选，HTML 页面标题（默认 agy Response）
  ---
  <直接独立 HTML 内容，或工作区生成的 HTML 文件路径>
  [END_AGY_FILE]
  ```
  `---` 后的内容支持两种模式：
  1. **工作区文件路径 (Workspace File Path)**：提供工作区相对或绝对路径（如 `runtime/artifacts/report.html` 或 `diff_report.html`），网关直接读取并发送该 HTML 文件。
  2. **直接独立 HTML (Raw Standalone HTML)**：直接输出完整的 `<!DOCTYPE html><html>...</html>` 内容，网关直接作为 HTML 文件发送，不经过 Markdown 中转。
  - **正文清洁**：网关在发送前自动从可见正文中剔除 `[AGY_FILE]` 块，正文仅保留摘要预览与已发送提示。
  - **多文件支持**：单次回复支持多个 `[AGY_FILE]` 块，一次性导出多个文件。
  - **移动优先**：自动确保移动端 `<meta name="viewport" content="width=device-width, initial-scale=1.0">`、代码与表格横向滚动自适应。
- **直接提取工作区文件**：输入 `/file <路径>` 可直接调取并下载工作区内的任意代码或配置文件。

---

## 架构与模块说明

每个 Python 模块均设计为高内聚、单一职责，且均支持独立命令行测试（规则 6）：

- `gateway/config.py` (`python -m gateway.config`): 配置加载与校验，包含 `DEFAULT_SYSTEM_PROMPT` 定义与 `AGY_SYSTEM_PROMPT` / `AGY_SYSTEM_PROMPT_FILE` 覆盖解析。
- `gateway/session_manager.py` (`python -m gateway.session_manager`): 会话状态管理与多项目持久化 (`runtime/sessions.json`)。
- `gateway/ui_keyboards.py` (`python -m gateway.ui_keyboards`): 交互式内联键盘构建器（多项目切换面板、模型切换、推理强度调节）。
- `gateway/transcriber.py` (`python -m gateway.transcriber`): 基于 `faster-whisper` 的高性能离线语音识别转录引擎（多语种检测、模型单例缓存、异步非阻塞）。
- `gateway/media_handlers.py` (`python -m gateway.media_handlers`): 语音消息与文件附件本地下载、语音实时转录与转发调度（自动保存至 `runtime/voice/` 与 `runtime/documents/`，支持中途 Steering）。
- `gateway/commands.py` (`python -m gateway.commands`): 全部 14 个斜杠命令的业务逻辑与交互式 Callback 回调分发。
- `gateway/dir_tree.py` (`python -m gateway.dir_tree`): 工作区目录树递归扫描与独立交互式 HTML 文件导航器生成。
- `gateway/file_renderers.py` (`python -m gateway.file_renderers`): 代码、Markdown、Diff、CSV 表格等多类型文件的 HTML 渲染。
- `gateway/message_formatter.py` (`python -m gateway.message_formatter`): Markdown 到 Telegram 原生富文本 HTML 转换、`[AGY_FILE]` 显式协议解析与提取、移动优先独立 HTML 报告生成与分块截断。
- `gateway/agy_bridge.py` (`python -m gateway.agy_bridge "<prompt>"`): `agy` 子进程异步双向通信、`stream-json` 双向流解析、首轮系统提示词精准注入、Steering 注入与进程生命周期管理。

### 📚 架构设计与协议文档 (Design Documents)

- [docs/cross_project_mesh_design.md](docs/cross_project_mesh_design.md): 跨项目去中心化多智能体通信协议与 Telegram 原生折叠置顶卡片设计方案 (`AGY_IPC` Protocol)。
- [ROADMAP.md](ROADMAP.md): 网关演进规划与后续里程碑。
- `gateway/telegram_client.py` (`python -m gateway.telegram_client`): 异步 Telegram Bot API 客户端（Long polling、流式编辑、`sendDocument` 文件多部件上传、429 限流退避）。
- `gateway/bot.py` (`python -m gateway.bot`): 调度核心引擎、Telegram 长轮询事件分发、流式桥接生命周期与异常保护。
- `main.py` (`python main.py`): 启动入口与 CLI 参数控制。

---

## 测试与验证

运行全部单元测试及集成冒烟测试：

```bash
conda run -n agy-telegram pytest -v
# 或
./venv/bin/pytest -v
```

测试覆盖范围（141 项全部通过）：
- `test_config.py`: 配置解析、Token 格式验证、白名单过滤、默认 runtime 路径、默认系统提示词内容校验、WebApp 端口/URL 配置、环境变量及文件覆盖测试
- `test_session_manager.py`: 会话 CRUD、持久化与恢复、并发状态保护、Legacy 迁移、累计 Token 与耗时统计、命令使用频次与 LRU 追踪、决策与动作状态暂存
- `test_ui_keyboards.py`: 多项目内联键盘、模型选择菜单、推理强度切换菜单渲染、自适应 LRU 命令菜单生成（`/help` 置顶、Top 8 截断）、配额/技能/产物/帮助命令中心面板、决策选项键盘、智能后续动作键盘、持久快捷键与动态输入框占位词、Telegram WebApp 原生弹窗按钮
- `test_webapp_server.py`: 零依赖纯异步 HTTP 服务启动与生命周期管理、文档注册、Telegram WebApp JS SDK 注入与主题自适应参数同步、`/health` 健康检查与路由防护
- `test_message_formatter.py`: HTML 实体转义、Markdown 转 Telegram HTML、代码高亮、标签自闭合、表格转换、独立移动 HTML 文档导出、文件导出条件判断、`sanitize_filename`、`detect_content_mode`、`parse_agy_file_block`、`extract_agy_file_blocks`、`extract_agy_action_blocks`、可折叠引用块渲染、移动端视口与响应式元素验证
- `test_dir_tree.py`: 目录树扫描、文件大小格式化与独立 HTML 导航器生成
- `test_file_renderers.py`: 代码高亮转换、Markdown 渲染、Diff 补丁视图、CSV 响应式表格渲染与二进制回退
- `test_agy_bridge.py`: 命令构建、stream-json 协议解析、Mock 管道流、声明式规则检测、跨轮次进程池长连接复用与空闲回收、`steer()` 注入与 `is_running()` 状态判断、跨项目工作区切换与首条消息事件完整传递
- `test_telegram_client.py`: API 调用、HTML 实体解析失败自动降级、304 抑制、`send_document` 多部件上传、语音文件下载、自适应 `set_my_commands`、`set_message_reaction` Emoji 反应
- `test_bot.py`: 所有斜杠命令路由（含 `/usage`, `/skills`, `/artifacts`）、集中式命令分发器、未授权拦截、流式处理管线、语音笔记/音频接收与转录、文件附件处理、中途 Steering 注入、`/dir` 目录浏览、`/file` 文件发送、`/export` 导出、产物一键下载、`ask_question` 原生决策选项与 `[AGY_ACTIONS]` 快捷按钮交互闭环、Telegram Mini App 实时弹窗集成、Telegram 网络异常抗逆与自动降级保护
- `test_ipc_router.py`: 跨项目 `AGY_IPC` 网格通信路由、信封封包与解析、置顶折叠卡片渲染与审计日志记录
- `test_transcriber.py`: faster-whisper 离线语音识别模型单例管理、同步与异步非阻塞音频转录
- `test_voice_tts.py`: 动态双语 TTS 语音合成、Edge-TTS 集成与 OGG Opus 编码
- `test_smoke_agy.py`: 真实的 `agy` CLI 联调冒烟测试


