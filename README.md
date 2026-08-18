# agy-telegram — 通过 Telegram 直接与 agy (Antigravity CLI) 对话

一个轻量、实时的 Telegram 网关：在 Telegram 里直接和 `agy`（Antigravity CLI / Gemini 2.5 Pro）对话，实时查看**思考过程、工具调用与中间流式输出**，支持 `/` 斜杠命令控制，不经过 Hermes 中转（省 token、看细节）。

---

## 目录
- [核心特性](#核心特性)
- [运行环境准备](#运行环境准备)
  - [选项 A: 使用 Python venv (推荐)](#选项-a-使用-python-venv-推荐)
  - [选项 B: 使用 Conda 环境 (环境名: agy-telegram)](#选项-b-使用-conda-环境-环境名-agy-telegram)
- [配置指南 (.env)](#配置指南-env)
  - [第一步: 获取 Telegram Bot Token](#第一步-获取-telegram-bot-token)
  - [第二步: 配置 .env](#第二步-配置-env)
- [启动与运行](#启动与运行)
- [Telegram 命令使用指南](#telegram-命令使用指南)
- [架构与模块说明](#架构与模块说明)
- [测试与验证](#测试与验证)

---

## 核心特性

1. **Telegram Long-Polling**: 基于 Telegram Bot API，无需公网 IP 或 Webhook 配置。
2. **实时流式与中间过程可视化**: 实时捕获 agy 的 `stream-json` 事件，展示工具调用（`run_command`, `list_dir`, `view_file` 等）、思考状态并限频推回 Telegram。
3. **会话持久化与隔离**: 每个 Telegram chat 维护独立的 `conversation_id`，支持 `/new` 丢弃旧会话、`/status` 查看当前状态。
4. **灵活参数调整**: 通过 `/model` 切换模型，通过 `/effort` 调整推理强度 (low/medium/high)，通过 `/dir` 切换工作区。
5. **安全与健壮性**:
   - 支持 `ALLOWED_CHAT_IDS` 白名单鉴权。
   - 支持 `/stop` 中断正在执行的 agy 子进程。
   - 自动处理 Telegram 4096 字符截断、限频 (Throttling) 与 HTML 标签容错降级。
   - 全套单元测试与 agy 冒烟测试。

---

## 运行环境准备

### 选项 A: 使用 Python venv (推荐)

```bash
cd /Users/jim/hermes-shared/agy-telegram

# 1. 创建虚拟环境
python3 -m venv venv

# 2. 安装依赖
./venv/bin/pip install -r requirements.txt
```

### 选项 B: 使用 Conda 环境 (环境名: `agy-telegram`)

根据项目约定，已提供 `environment.yml`：

```bash
cd /Users/jim/hermes-shared/agy-telegram

# 1. 创建并激活 conda 环境
conda env create -f environment.yml
conda activate agy-telegram

# 2. 安装依赖 (如未自动安装)
pip install -r requirements.txt
```

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

# 日志级别 (INFO / DEBUG)
LOG_LEVEL=INFO
```

---

## 启动与运行

### 1. 检查配置有效性

```bash
./venv/bin/python main.py --check-config
```

### 2. 运行 agy 真实冒烟测试

验证本地 `agy` CLI 能否正常调用并输出流式 JSON：

```bash
./venv/bin/python main.py --smoke-test
```

### 3. 启动网关服务

```bash
./venv/bin/python main.py
```

服务启动后，将在终端显示 `🤖 Connected to Telegram as @YourBot`，并写入 `gateway.log`。

---

## Telegram 命令使用指南

在 Telegram 中与你的 Bot 对话：

| 命令 | 说明 | 示例 |
| :--- | :--- | :--- |
| **直接发送文本** | 将消息直接发送给 agy 处理，实时返回工具调用与回答 | `请帮我查看当前目录下的文件` |
| `/new` | 重置会话，开始全新的 agy 对话（清除 `conversation_id`） | `/new` |
| `/status` | 查看当前 Chat ID、会话 ID、模型、推理强度及运行状态 | `/status` |
| `/model <name>` | 切换当前对话使用的模型（或 `/model reset` 恢复默认） | `/model gemini-2.5-pro` |
| `/effort <level>` | 调整推理强度：`low`、`medium`、`high` | `/effort high` |
| `/dir <path>` | 切换 agy 的工作目录（或 `/dir reset` 恢复默认） | `/dir /Users/jim/my-project` |
| `/stop` | 强制中断当前正在执行的 agy 任务 | `/stop` |
| `/help` | 查看帮助说明与命令列表 | `/help` |

---

## 架构与模块说明

每个 Python 模块均设计为高内聚、低耦合，且均支持独立命令行测试（规则 6）：

- `gateway/config.py` (`python -m gateway.config`): 配置加载与校验。
- `gateway/session_manager.py` (`python -m gateway.session_manager`): 会话状态管理与持久化 (`sessions.json`)。
- `gateway/message_formatter.py` (`python -m gateway.message_formatter`): 消息美化、流式进度排版与 4000 字符分块。
- `gateway/agy_bridge.py` (`python -m gateway.agy_bridge "<prompt>"`): `agy` 子进程异步执行、`stream-json` 解析与进程生命周期管理。
- `gateway/telegram_client.py` (`python -m gateway.telegram_client`): 异步 Telegram Bot API 客户端（Long polling、流式编辑、429 限流退避）。
- `gateway/bot.py` (`python -m gateway.bot`): 调度中心、斜杠命令路由与流式更新任务池。
- `main.py` (`python main.py`): 启动入口与 CLI 参数控制。

---

## 测试与验证

运行全部单元测试及集成冒烟测试：

```bash
./venv/bin/pytest -v
```

测试覆盖范围包括：
- `test_config.py`: 配置解析、Token 格式验证、白名单过滤
- `test_session_manager.py`: 会话 CRUD、持久化与恢复、并发状态保护
- `test_message_formatter.py`: HTML 转义、工具状态美化、分块策略、流式窗口截断
- `test_agy_bridge.py`: 命令构建、stream-json 协议解析、Mock 管道流
- `test_telegram_client.py`: API 调用、HTML 实体解析失败自动降级、304 抑制
- `test_bot.py`: 所有斜杠命令路由、未授权拦截、流式处理管线、`/stop` 中断
- `test_smoke_agy.py`: 真实的 `agy` CLI 联调冒烟测试
