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
7. **安全与健壮性**:
   - 支持 `ALLOWED_CHAT_IDS` 白名单鉴权。
   - 保留 `/stop` 供强制中断正在执行的 agy 子进程。
   - 自动处理 Telegram 4096 字符截断、限频 (Throttling) 与 HTML 标签容错降级。
   - 全套单元测试与 agy 冒烟测试。

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
│   └── sessions.json     # 跨会话与多项目持久化数据
├── gateway/              # 核心网关 Python 模块
├── tests/                # 单元测试与冒烟测试
├── restart.sh            # 服务启停控制脚本
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

# 查看运行状态
./restart.sh status

# 停止网关
./restart.sh stop
```

日志文件输出在 `runtime/gateway.log`：
```bash
tail -f -n 50 runtime/gateway.log
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

### 中途引导 (Steering) 工作机制

- 当 agy 正在执行耗时任务（如运行命令、分析大代码库）时，用户可以直接输入新消息或发送 `/steer <指示>`。
- 网关利用 `agy` 原生 `stream-json` 协议管道实时向子进程 `stdin` 发送 Steering 消息，保留当前会话上下文，Agent 将在执行完当前步后无缝吸纳新指示并继续作答。
- 若用户确定需要立即放弃当前任务，随时发送 `/stop` 即可彻底终止。

---

## 架构与模块说明

每个 Python 模块均设计为高内聚、低耦合，且均支持独立命令行测试（规则 6）：

- `gateway/config.py` (`python -m gateway.config`): 配置加载与校验。
- `gateway/session_manager.py` (`python -m gateway.session_manager`): 会话状态管理与持久化 (`runtime/sessions.json`)。
- `gateway/message_formatter.py` (`python -m gateway.message_formatter`): 消息美化、流式进度排版与 4000 字符分块。
- `gateway/agy_bridge.py` (`python -m gateway.agy_bridge "<prompt>"`): `agy` 子进程异步双向通信、`stream-json` 双向流解析、Steering 注入与进程生命周期管理。
- `gateway/telegram_client.py` (`python -m gateway.telegram_client`): 异步 Telegram Bot API 客户端（Long polling、流式编辑、429 限流退避）。
- `gateway/bot.py` (`python -m gateway.bot`): 调度中心、斜杠命令路由、Steering 路由与流式更新任务池。
- `main.py` (`python main.py`): 启动入口与 CLI 参数控制。

---

## 测试与验证

运行全部单元测试及集成冒烟测试：

```bash
conda run -n agy-telegram pytest -v
```

测试覆盖范围（33 项全部通过）：
- `test_config.py`: 配置解析、Token 格式验证、白名单过滤、默认 runtime 路径
- `test_session_manager.py`: 会话 CRUD、持久化与恢复、并发状态保护、Legacy 迁移
- `test_message_formatter.py`: HTML 转义、工具状态美化、分块策略、流式窗口截断
- `test_agy_bridge.py`: 命令构建、stream-json 协议解析、Mock 管道流、`steer()` 注入测试
- `test_telegram_client.py`: API 调用、HTML 实体解析失败自动降级、304 抑制
- `test_bot.py`: 所有斜杠命令路由、未授权拦截、流式处理管线、`/steer` 引导、`/stop` 中断
- `test_smoke_agy.py`: 真实的 `agy` CLI 联调冒烟测试
