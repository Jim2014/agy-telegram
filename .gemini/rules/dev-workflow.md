# Development Workflow & Engineering Standards for agy-telegram

You MUST strictly follow this standard engineering cycle for any code modifications or task completions in this repository:

## 1. Execution Flow Lifecycle

```
[Code Modifications / Feature Implementation]
                   │
                   ▼
[1. Run Full Automated Test Suite (pytest in conda env agy-telegram)]
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
    [Tests Fail]       [100% Pass]
         │                   │
         ▼                   ▼
   [Fix Issues]     [2. Update README.md / ROADMAP.md]
                             │
                             ▼
                    [3. Git Commit Changes (English Message)]
                             │
                             ▼
                    [4. Restart Service (./restart.sh)]
                             │
                             ▼
                    [5. Verify Running Status & Conclude]
```

## 2. Mandatory Rules & Requirements

1. **Python Environment**:
   - Always use the conda environment `agy-telegram` (`/opt/homebrew/Caskroom/miniconda/base/envs/agy-telegram/bin/pytest` or python) when executing scripts, running tests, or installing dependencies.
   - Maintain `environment.yml` and `requirements.txt` for any new dependencies.

2. **Automated Testing & Verification**:
   - Every python file must be modular and independently executable/testable.
   - Run the complete pytest test suite before committing: all unit, integration, and resilient network tests must achieve 100% pass.
   - Add new unit tests covering any newly introduced features, command handlers, or bug fixes.

3. **Documentation & Roadmap**:
   - Update `README.md` and `ROADMAP.md` whenever code, architecture, or slash commands are modified.

4. **Mandatory 100% English Standards**:
   - All code comments, docstrings, variable/function names, bot UI/menu messages, interactive buttons, flash alerts, git commit messages, and project documentation MUST be in **English** at all times (see `.gemini/rules/language-standards.md`). Never hardcode Chinese or non-English strings in UI keyboards or bot messages.

5. **Automated Git Commit & Service Restart**:
   - Automatically commit code changes to git after code completion and after all builds and tests pass, before restarting services or concluding the task.
   - After committing, run `./restart.sh` to smoothly reload the gateway service and watchdog daemon, and verify that the service PID is running.
