# daily-report — Claude Code Skill

A Claude Code skill that scans your `~/.claude/projects/` session logs and generates a structured daily work report with timeline, deliverables, signals, and optional Scrum standup preparation.

## What it does

1. **Scans** `~/.claude/projects/**/*.jsonl` for today's Claude Code sessions
2. **Launches 3 parallel Haiku sub-agents** to analyze:
   - Timeline — chronological workstream summary
   - Deliverables — files written, reports generated, builds run
   - Signals — blockers, repeated issues, unfinished items
3. **Renders** a paired report: `/tmp/daily-report-YYYY-MM-DD-report.md` + `.html`
4. **Opens** the HTML in Chrome automatically
5. **Optional Scrum standup** — add "standup" / "站会" / "晨会" to get the 3-question format

No Python scripts. No hard-coded dependencies. Uses Claude's native tools only.

---

## Installation

### macOS

**Step 1 — Install the skill**

```bash
# Create the global skills directory if it doesn't exist
mkdir -p ~/.claude/skills

# Clone or copy the skill
cp -r daily-report ~/.claude/skills/daily-report
```

**Step 2 — Verify before committing (test in /tmp)**

```bash
# Create a clean test workspace
mkdir -p /tmp/dr-test-workspace
cd /tmp/dr-test-workspace

# Ask Claude to explain the skill (should NOT say "Unknown skill")
claude --model claude-sonnet-4-6 -p '/daily-report do not run just explain what this would do?'
```

✅ Expected output: A full explanation of the 4-step workflow  
❌ Bad output: `Unknown skill: daily-report`

**Step 3 — Run it for real**

```bash
cd /tmp/dr-test-workspace

# Basic daily report
claude -p '/daily-report'

# With Scrum standup section
claude -p '/daily-report standup'
```

---

### Windows

> Requires: [Claude Code CLI](https://claude.ai/code) + Git Bash or PowerShell

**Step 1 — Install the skill**

```powershell
# In PowerShell or Git Bash
$skillsDir = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force -Path $skillsDir

# Copy the skill folder
Copy-Item -Recurse daily-report "$skillsDir\daily-report"
```

Or with Git Bash:

```bash
mkdir -p ~/.claude/skills
cp -r daily-report ~/.claude/skills/daily-report
```

**Step 2 — Verify before committing (test in temp folder)**

Open a new terminal and run from a neutral directory:

```powershell
# PowerShell
cd $env:TEMP
claude --model claude-sonnet-4-6 -p '/daily-report do not run just explain what this would do?'
```

Or Git Bash:

```bash
cd /tmp
claude --model claude-sonnet-4-6 -p '/daily-report do not run just explain what this would do?'
```

✅ Expected: Full 4-step explanation  
❌ Bad: `Unknown skill: daily-report`

**Step 3 — Run it for real**

```powershell
cd $env:TEMP
claude -p '/daily-report'

# With Scrum standup
claude -p '/daily-report standup'
```

> **Note (Windows):** The skill auto-opens the HTML report with `open -a "Google Chrome"` which is macOS-only. On Windows, open the report manually:
> ```powershell
> Start-Process "C:\Users\$env:USERNAME\AppData\Local\Temp\daily-report-YYYY-MM-DD-report.html"
> ```

---

## Verification Checklist

Run these checks after installation:

```bash
# 1. Skill is recognized (no "Unknown skill" error)
cd /tmp && claude -p '/daily-report do not run just explain what this would do?'

# 2. Skill files are in the right place
ls ~/.claude/skills/daily-report/
# Should show: SKILL.md  references/  scripts/  assets/

# 3. SKILL.md is readable
cat ~/.claude/skills/daily-report/SKILL.md | head -5
```

---

## Usage

| Command | What it does |
|---------|-------------|
| `/daily-report` | Today's report |
| `/daily-report 2026-04-12` | Report for a specific date |
| `/daily-report standup` | Today's report + Scrum 三问 |
| `/daily-report standup 2026-04-12` | Specific date + standup |

---

## Output Files

All output goes to `/tmp/` (or `%TEMP%` on Windows):

```
/tmp/daily-report-YYYY-MM-DD-report.html   ← main report (auto-opens in Chrome)
/tmp/daily-report-YYYY-MM-DD-report.md     ← Markdown version
/tmp/daily-report-YYYY-MM-DD-timeline.md   ← Haiku agent output
/tmp/daily-report-YYYY-MM-DD-deliverables.md
/tmp/daily-report-YYYY-MM-DD-signals.md
/tmp/daily-report-YYYY-MM-DD.data.jsonl    ← raw scan data
```

---

## Requirements

- [Claude Code CLI](https://claude.ai/code) installed and authenticated
- At least one Claude Code session logged today in `~/.claude/projects/`
- Google Chrome (for auto-open on macOS; manual open on Windows)

---

## Skill Structure

```
daily-report/
├── SKILL.md                        ← skill definition (no scripts)
└── references/
    └── agent-team-contract.md      ← 3-agent analysis contract
```

No bundled Python scripts. All scanning and rendering is done with Claude's native Bash/Read/Write/Glob tools.

---

## License

MIT
