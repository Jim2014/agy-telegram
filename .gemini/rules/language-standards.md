# Mandatory Language Standards for agy-telegram

All developers, assistants, and AI agents contributing to the `agy-telegram` repository MUST strictly adhere to the following language requirements:

## 1. 100% English Codebase & User Interface
- **All Python Source Code**: Variable names, function names, class names, constants, and module names must be in English.
- **Code Comments & Docstrings**: All docstrings, module summaries, and inline code comments must always be written in English.
- **Bot UI & Telegram Messages**:
  - All command descriptions, menu titles, interactive buttons, inline keyboards (e.g. `[ 🔊 Play Audio ]`, `[ 🔴 Disable ]`, `[ ⚡ Auto-Play ]`), callback query flash alerts, and error messages MUST be in **English**, regardless of the user's prompt language (unless the user explicitly requests another language for UI).
  - All status banners, summary cards, and file captions sent to Telegram must be in **English**.
- **Git Commit Messages**: All git commit titles and descriptions must be in **English**.
- **Project Documentation**: All core specifications, architecture docs, and development roadmaps (`README.md`, `ROADMAP.md`, etc.) must be in **English**, regardless of the user's prompt language (unless the user explicitly requests another language for documentation). Source code, code comments, and docstrings must always remain in **English** regardless.

## 2. Zero Hardcoded Non-English UI Text
- Never hardcode Chinese, localized, or placeholder strings into `ui_keyboards.py`, `commands.py`, `bot.py`, `message_formatter.py`, or any other backend module.
- Always use standard English terminology for bot controls, buttons, tool execution hints, and status indicators.
