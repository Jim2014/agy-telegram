# Telegram Output & Export Protocol for agy-telegram

When interacting with the user via the `agy-telegram` gateway on Telegram, you MUST follow these output rules:

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
When concluding a response where follow-up actions, next steps, or decision choices are recommended, you can output an `[AGY_ACTIONS]` block at the very end of your response. The gateway will automatically convert these into interactive clickable Telegram buttons:

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

## 5. [AGY_VOICE] Spoken Podcast Audio Protocol
At the very end of your response, ALWAYS append a conversational spoken audio block inside `[AGY_VOICE lang="zh|en"]` and `[END_AGY_VOICE]` tags (each on its own standalone line):

```text
[AGY_VOICE lang="zh"]
我已经为你跑完了全部 123 项测试，所有用例全部通过，并已自动完成 Git 提交，网关服务运行正常。
[END_AGY_VOICE]
```

### Voice Protocol Rules:
1. **Plain Text ONLY**: NEVER use Markdown syntax (no `**`, `#`, ````, tables, or backticks).
2. **Spoken Podcast Style**: Natural, conversational, fluent tone, suitable for listening. No artificial length restriction — answer thoroughly or summarize as appropriate for the prompt.
3. **Language Code**: Specify `lang="zh"` for Chinese or `lang="en"` for English based on the conversation language.
4. **Stripped by Gateway**: The gateway automatically extracts and strips this block from visible text and synthesizes it into spoken audio.


