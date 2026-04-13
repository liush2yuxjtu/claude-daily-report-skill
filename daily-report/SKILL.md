---
name: daily-report
description: Scan ~/.claude/projects/**/*.jsonl Claude session logs to reconstruct what was done on a given day, then generate paired /tmp/*report.md and /tmp/*report.html reports and open the HTML view. Use when the user asks for a daily report, 日报, 今日总结, today's work, "what did I do today", a Claude Code session recap, a history-based work summary, or Scrum standup preparation (Scrum站会, 晨会, standup). This skill MUST use haiku subagents for analysis. No bundled scripts are used — all steps use native tools (Glob, Read, Bash, Write).
---

# Daily Report

Build a same-day work summary from Claude Code session logs under `~/.claude/projects`.

## Non-Negotiables

- **No hard-coded scripts.** Use Glob, Read, Bash, and Write tools directly.
- Always launch exactly 3 haiku subagents for analysis — never do analysis in the main thread.
- Default source root: `~/.claude/projects`.
- Default date: today (`date +%F`).
- Default output prefix: `/tmp/daily-report-YYYY-MM-DD`.
- Produce paired output: `/tmp/*report.md` + `/tmp/*report.html`.
- Open the HTML when complete: `open -a "Google Chrome" <path>`.

## Workflow

### 1. Scan Raw Activity

Use Bash to extract today's JSONL records from all session files:

```bash
DATE=$(date +%F)
PREFIX="/tmp/daily-report-$DATE"

# Collect all session files that contain today's date
grep -rl "$DATE" ~/.claude/projects/ --include="*.jsonl" 2>/dev/null \
  | xargs grep "$DATE" 2>/dev/null \
  | head -5000 > "$PREFIX.data.jsonl"

echo "Lines found: $(wc -l < "$PREFIX.data.jsonl")"
echo "Unique cwds: $(grep -o '"cwd":"[^"]*"' "$PREFIX.data.jsonl" | sort -u | wc -l)"
```

If the output file is empty, report "今日没有找到会话记录" and stop.

### 2. Launch 3 Haiku Subagents in Parallel

Read [references/agent-team-contract.md](references/agent-team-contract.md) for the full agent contract.

Spawn all 3 agents in a **single parallel batch**, each with `model: "haiku"`:

| Agent | Task | Output file |
|-------|------|------------|
| Timeline | 按时间顺序梳理今日工作流 | `$PREFIX-timeline.md` |
| Deliverables | 列出交付物：文件写入、构建、报告 | `$PREFIX-deliverables.md` |
| Signal | 识别阻塞、重复问题、未完成项 | `$PREFIX-signals.md` |

Each agent receives: the path to `$PREFIX.data.jsonl` and its output path. Agents use Read tool to read the data file, then Write tool to save their bullets.

Wait for all 3 to complete before continuing.

### 3. Synthesize and Render

After all 3 bullet files exist, the **main thread** reads them and directly writes the final paired output using the Write tool — no rendering script.

**Markdown report** (`$PREFIX-report.md`): structured summary with sections for Timeline, Deliverables, Signals, and (if requested) Scrum Standup.

**HTML report** (`$PREFIX-report.html`): single-page styled HTML with the same sections. Use clean CSS inline styles, no external dependencies.

Then open: `open -a "Google Chrome" "$PREFIX-report.html"`

### 4. Scrum Standup Section (if requested)

If the user mentions Scrum站会, standup, 晨会, or morning meeting, append a dedicated section to both output files:

```
## Scrum 站会准备（三问）

**昨天/今天完成了什么？**
- [来自 deliverables 的关键交付]

**今天计划做什么？**
- [来自 signals 的 next-action 项]

**有哪些阻塞或需要帮助？**
- [来自 signals 的 blocker 项]
```

Keep it factual, brief, and focused on development progress. Do NOT include product planning or requirements discussions.

### 5. Deliver

- State the date scanned and output paths.
- Print the Scrum 三问 inline in the response.
- Note if no sessions or no file writes were found.

## Notes

- Timestamps are inside JSONL records, not file mtimes — always filter by content.
- File writes come from `Write`, `Edit`, `MultiEdit` tool-use records in the JSONL.
- Group output by `cwd` for project context.
- For Scrum standup: focus on dev progress and blockers only; skip product/requirement topics per Scrum rules.
