# Agent Team Contract

Use this contract whenever the skill builds a report. The main thread coordinates; it does not do the analysis itself.

## Team Shape

Create exactly 3 agents, all with `model: "haiku"`, in a single parallel batch:

### 1. Timeline Agent
- Read the `.data.jsonl` file provided.
- Output 5–10 bullets covering: session clusters, workstreams, chronological progression.
- Format: plain Markdown bullets (`- HH:MM — <what happened>`).
- Save to `$PREFIX-timeline.md`.

### 2. Deliverables Agent
- Read the `.data.jsonl` file provided.
- Output 5–10 bullets covering: files written (Write/Edit/MultiEdit tool calls), reports generated, builds run, notable Bash commands.
- Format: plain Markdown bullets (`- [project] filename or action`).
- Save to `$PREFIX-deliverables.md`.

### 3. Signal Agent
- Read the `.data.jsonl` file provided.
- Output 5–10 bullets covering: repeated/deduped prompts, identified blockers, unfinished items, likely next actions.
- Format: plain Markdown bullets, label blockers with `[BLOCKER]` prefix.
- Save to `$PREFIX-signals.md`.

## Agent Rules

- Each agent reads only its assigned `.data.jsonl` file.
- Each agent writes only its own output file using the Write tool.
- Agents do not inspect each other's output.
- Keep bullets factual and concise — no prose paragraphs.
- Do not write the final report — that is the main thread's job.

## Main Thread Responsibility

After all 3 agents finish:

1. Read `$PREFIX-timeline.md`, `$PREFIX-deliverables.md`, `$PREFIX-signals.md`.
2. Synthesize and write `$PREFIX-report.md` using the Write tool.
3. Synthesize and write `$PREFIX-report.html` using the Write tool (self-contained HTML, inline CSS).
4. Open HTML: `open -a "Google Chrome" "$PREFIX-report.html"`.
5. If Scrum standup was requested, append the 三问 section to both files and print it inline.
