# Telegram Output & Export Protocol for agy-telegram

When interacting with the user via the `agy-telegram` gateway on Telegram, you MUST follow these output rules:

## 0. Strict Dynamic Language Matching Invariant
- You MUST determine your response language SOLELY based on the user's MOST RECENT (latest) message in the conversation, completely IGNORING any previous conversation history or past turns:
  - If the user's latest prompt is in **English**, your entire text response and `[AGY_VOICE lang="en"]` MUST be in **English**.
  - If the user's latest prompt is in **Chinese**, your entire text response and `[AGY_VOICE lang="zh"]` MUST be in **Chinese**.
  - NEVER reply in Chinese if the user's latest message is in English, even if all previous turns were in Chinese.

## 1. Short / Standard Responses (Default)
- For regular questions, concise explanations, status updates, or brief code snippets, reply directly with plain text using standard GitHub-flavored Markdown.
- The gateway automatically converts standard Markdown (bold, italic, inline code, and code blocks) into Telegram HTML.
- Keep answers concise, clear, and easy to read on mobile screens.

## 2. Long / Complex Responses (Direct Standalone HTML Document Generation)
- When output is long or complex (such as large git diffs/patches, test/coverage reports, build logs, multi-file code modifications, or detailed analysis reports), **DO NOT** paste the full content into the plain text message and **DO NOT** use Markdown as the document export format.
- Instead:
  1. Generate a standalone, single-page, mobile-friendly HTML document with embedded `<style>`, responsive `<meta name="viewport" content="width=device-width, initial-scale=1.0">`, clean typography, auto dark/light theme support, and touch-scrollable `<pre>` and `<table>`. You can either write it directly as an `.html` file in the workspace or output raw HTML inside `[AGY_FILE]`.
  2. Provide a concise summary or preview in the text response with a note (e.g. `📄 Full details attached in the HTML report below`).
  3. Export and send the HTML document using the `[AGY_FILE]` protocol block. The gateway will directly send this HTML file without Markdown conversion.

## 3. [AGY_FILE] Protocol Specification
To export a document, output an explicit block enclosed by `[AGY_FILE]` and `[END_AGY_FILE]` tags (each on its own standalone line):

```text
[AGY_FILE]
filename: <descriptive_filename.html>
title: <Document Title>
---
<Direct Standalone HTML Content, or Workspace HTML File Path>
[END_AGY_FILE]
```

### Protocol Rules:
- **Headers** (before `---` separator):
  - `filename`: Desired filename ending in `.html` (e.g. `diff_report.html`, `test_results.html`).
  - `title`: Human-readable title for the document header.
- **Content Modes** (after `---` separator):
  - **Workspace File Path**: Relative or absolute path to an `.html` file in the workspace (e.g. `runtime/artifacts/report.html`).
  - **Raw HTML**: Complete standalone `<!DOCTYPE html>...</html>` or HTML fragment.

## 4. [AGY_ACTIONS] Interactive Quick Action Buttons Protocol
When concluding a response where follow-up actions, next steps, or decision choices are recommended, output an `[AGY_ACTIONS]` block before `[AGY_VOICE]`:

```text
[AGY_ACTIONS]
- 🧪 Run Tests: pytest -v
- 📝 View Git Diff: /diff
- 🚀 Commit & Deploy
[END_AGY_ACTIONS]
```

### Action Protocol Rules:
- Format: `- <Button Label>: <Prompt or Command>` (if no colon is present, the label is used as prompt).
- Keep button labels concise (10-25 characters) with an emoji prefix.
- Provide 2 to 4 actionable next steps.

## 5. [AGY_VOICE] Spoken Podcast Audio Protocol (Audio Podcast Edition)
At the very end of your response (AFTER completing all written text and any `[AGY_ACTIONS]`), append an acoustic-friendly, spoken podcast audio version inside `[AGY_VOICE lang="zh|en"]` and `[END_AGY_VOICE]` tags (each on its own standalone line):

```text
[AGY_VOICE lang="en"]
I have executed all 126 automated tests and every test passed successfully. In the codebase, we updated the message formatting engine and ensured that language detection always aligns with your latest input. All services are running smoothly, and we are ready for the next steps.
[END_AGY_VOICE]
```

### Voice Protocol Rules:
1. **Complete Written Text First**: You MUST finish writing the entire written response 100% to completion. NEVER stop mid-sentence or truncate the written text before opening `[AGY_VOICE]`.
2. **Complete Meaning & Coverage (Acoustic-Friendly Adaptation)**: Convey all substantive results, decisions, architectural concepts, and reasoning from your response, but adapt it completely for auditory listening.
3. **Natural Spoken Broadcast Tone**: Imagine speaking as an articulate tech podcast host or audiobook narrator. The narrative must flow smoothly and conversationally.
4. **STRICTLY AVOID Unspeakable / Visual Artifacts**:
   - **NEVER recite long file paths or URLs**: Instead of saying `/Users/jim/.../message_formatter.py`, say "the message formatter module" or "消息格式化模块".
   - **NEVER read raw code syntax or symbols**: Do not read function signatures with brackets, colons, underscores, backticks, or variable names like `foo_bar_baz`. Explain what the code does or describe the logic in natural spoken words.
   - **NO Markdown or table markup**: Strip all asterisks, hashes, backticks, pipes, and dashes.
5. **Do Not Include Buttons / Command Syntax**: NEVER repeat button choices, action menus (`[AGY_ACTIONS]`), decision lists, or slash commands.
6. **Language Code**: Specify `lang="zh"` for Chinese or `lang="en"` for English based on the conversation language.
7. **Stripped by Gateway**: The gateway automatically extracts and strips this block from visible text and synthesizes it into spoken audio.


