# Task 01 — 构建 agy-telegram 网关：通过 Telegram 直接对话 agy

## 背景
用户需要一条独立通道：在 Telegram 里**直接**与 `agy`（Antigravity CLI / Gemini Pro）对话，
能看到 **agy 的中间输出**（思考/工具调用/流式过程），支持 `/` 斜杠命令，**不经过 Hermes 中转**
（省 token、看细节）。本任务一次性构建这个网关的核心可运行版本。

## 项目路径
- 项目根(新项目): /Users/jim/hermes-shared/agy-telegram
- 虚拟环境(需自建): 在项目内 `python3 -m venv venv`, 用 venv/bin/python
- 参考 README: /Users/jim/hermes-shared/agy-telegram/README.md
- agy CLI: /Users/jim/.local/bin/agy

## 需求细节

### 1. Telegram 桥接
- 用 Telegram **Bot API**, 以 **long polling** (getUpdates) 方式接收消息, 无需 webhook/公网端口。
- Bot token 从 `.env` 的 `BOT_TOKEN` 读取; 建 `.env.example` 模板(不含真实 token)。
- 每个 Telegram chat 一个独立 agy 会话; 维护 chat_id → conversation_id 映射。
- 可选: `ALLOWED_CHAT_IDS` 白名单(.env 可配), 空则放行所有。

### 2. 桥接 agy 与流式输出
- 调用 agy 子进程: `agy --add-dir <工作目录> --dangerously-skip-permissions --output-format stream-json --print "<用户消息>"`(或用 text 模式逐块)。
- **流式转发**: 用 Telegram `editMessageText` 把 agy 的流式输出分块/限频推回同一消息, 让用户**实时看到中间输出**(思考、工具调用、阶段)。注意 Telegram 消息长度 4096 截断与限频, 设计好分块策略(如整句+滚动截断、'...'续写)。
- 新 chat 用 `--new-project` 或有 `--conversation <id>` 续接; `/new` 命令另起会话丢弃旧 conversation_id。

### 3. 斜杠命令 (至少)
- `/new` 新建会话; `/model <名>` 或 `/effort low|medium|high` 切换模型/推理强度(传给 agy --model/--effort);
- `/help` 列出可用命令; `/status` 显示当前 conversation_id/模型。
- 非斜杠文本 → 当作发给 agy 的消息。

### 4. 健壮性
- 子进程超时管理(agy 可能久跑), 支持 /stop 中断当前。
- 错误处理: agy 报错(如模型无效)回显给用户; 崩溃不丢会话。
- 日志: 追加到 `gateway.log`。

### 5. 测试
- 至少: 解析器/命令处理单元测试(不打真实 API)。
- 可 mock 的 agy 子进程测试: 验证流式分块与 editMessageText 调用。
- 真实调用 agy 一次冒烟(可手动验证)。

## 范围约束
- 本任务在 agy-telegram 项目内独立构建, **不碰 learner / hermes 其它项目**。
- `.env` 不入库(加 .gitignore); 不提交真实 BOT_TOKEN。
- 保持简单可用优先, 不引入重型框架(纯 Python + httpx 或标准库即可)。
- 禁止 push(若建 git 仓库, 本地 commit 即可)。

## 过程记录要求
- 每步追加 process.log(带时间戳); 开始/结束更新 state.md。
- 本项目也遵循 tasks/ 约定: 本 task 目录即 tasks/task01。

## 产出
- 可运行的网关(入口脚本 + 依赖 requirements.txt + .env.example + README 运行说明)。
- 测试通过记录 + 一次真实 agy 冒烟结果。

## 验证标准
- 入口脚本可启动 long polling(无 BOT_TOKEN 时能提示并正常退出不崩)。
- 单测/冒烟通过。
- 详细写明: 用户如何创建 bot(token 从 BotFather 拿)、填 .env、运行、在 Telegram 里怎么用(/命令)。
- 因无法取得真实 BOT_TOKEN 不代表失败: 报告明确指出用户需填入自己的 token 即可联调。
