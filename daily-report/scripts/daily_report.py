#!/usr/bin/env python3
"""Scan Claude session logs and render paired daily reports."""

from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


READ_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "Task",
}
WRITE_TOOLS = {
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
}
PATH_KEYS = {"file_path", "path", "paths", "notebook_path", "cwd"}
DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")
DEFAULT_PREFIX_TEMPLATE = "/tmp/daily-report-{date}"


@dataclass
class SessionSummary:
    source_path: str
    session_id: str
    cwd: str
    git_branch: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    record_count: int = 0
    user_prompt_count: int = 0
    assistant_text_count: int = 0
    tool_use_count: int = 0
    sidechain_record_count: int = 0
    prompt_previews: list[str] = field(default_factory=list)
    assistant_previews: list[str] = field(default_factory=list)
    tool_counts: Counter[str] = field(default_factory=Counter)
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan session JSONL files into summary JSON")
    scan.add_argument("--root", default=DEFAULT_ROOT, help="path to ~/.claude/projects")
    scan.add_argument("--date", default="today", help="local date in YYYY-MM-DD or 'today'")
    scan.add_argument("--timezone", default="local", help="IANA timezone or 'local'")
    scan.add_argument("--output", required=True, help="path to write JSON summary")

    render = subparsers.add_parser("render", help="render paired markdown/html report from summary JSON")
    render.add_argument("--input", required=True, help="JSON summary created by scan")
    render.add_argument("--output-prefix", required=True, help="target prefix; outputs end with -report.md/html")
    render.add_argument("--note", action="append", default=[], help="optional agent-team note file; repeat as needed")
    render.add_argument("--title", default="", help="custom report title")
    render.add_argument("--open", action="store_true", help="open the HTML report after rendering")

    workflow = subparsers.add_parser("workflow", help="scan and render in one shot")
    workflow.add_argument("--root", default=DEFAULT_ROOT, help="path to ~/.claude/projects")
    workflow.add_argument("--date", default="today", help="local date in YYYY-MM-DD or 'today'")
    workflow.add_argument("--timezone", default="local", help="IANA timezone or 'local'")
    workflow.add_argument(
        "--output-prefix",
        default="",
        help="target prefix; defaults to /tmp/daily-report-<date>",
    )
    workflow.add_argument("--note", action="append", default=[], help="optional agent-team note file; repeat as needed")
    workflow.add_argument("--title", default="", help="custom report title")
    workflow.add_argument("--open", action="store_true", help="open the HTML report after rendering")
    return parser.parse_args()


def resolve_timezone(name: str):
    if name == "local":
        return datetime.now().astimezone().tzinfo
    if ZoneInfo is None:
        raise SystemExit("zoneinfo is unavailable; use --timezone local")
    return ZoneInfo(name)


def resolve_target_date(raw: str, tzinfo) -> str:
    if raw == "today":
        return datetime.now(tzinfo).date().isoformat()
    return raw


def parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def parse_record_timestamp(record: dict[str, Any]) -> datetime | None:
    stamp = parse_timestamp(str(record.get("timestamp", "")))
    if stamp is not None:
        return stamp
    if record.get("type") == "file-history-snapshot":
        snapshot = record.get("snapshot", {})
        if isinstance(snapshot, dict):
            return parse_timestamp(str(snapshot.get("timestamp", "")))
    return None


def simplify_text(value: str, limit: int = 180) -> str:
    value = value.strip()
    if not value:
        return ""
    command_match = re.search(r"<command-name>(.*?)</command-name>", value, re.DOTALL)
    if command_match:
        return command_match.group(1).strip()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def unique_append(values: list[str], value: str, limit: int) -> None:
    if not value or value in values:
        return
    if len(values) < limit:
        values.append(value)


def extract_text_blocks(content: Any, role: str) -> list[str]:
    if isinstance(content, str):
        cleaned = simplify_text(content)
        return [cleaned] if cleaned else []
    if not isinstance(content, list):
        return []

    collected: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            cleaned = simplify_text(str(item.get("text", "")))
            if cleaned:
                collected.append(cleaned)
        elif role == "user" and item_type == "tool_result":
            continue
    return collected


def is_command_wrapper(content: Any) -> bool:
    if isinstance(content, str):
        return "<command-name>" in content or "<local-command-caveat>" in content
    if not isinstance(content, list):
        return False
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text", ""))
            if "<command-name>" in text or "<local-command-caveat>" in text:
                return True
    return False


def is_tool_result_only(content: Any) -> bool:
    if not isinstance(content, list) or not content:
        return False
    seen = False
    for item in content:
        if not isinstance(item, dict):
            return False
        if item.get("type") != "tool_result":
            return False
        seen = True
    return seen


def extract_tool_uses(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    tool_uses: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            tool_uses.append(item)
    return tool_uses


def walk_paths(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in PATH_KEYS:
                if isinstance(nested, str):
                    yield nested
                elif isinstance(nested, list):
                    for part in nested:
                        if isinstance(part, str):
                            yield part
            yield from walk_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_paths(nested)


def looks_like_path(value: str) -> bool:
    return value.startswith("/") or value.startswith("~") or "/" in value


def shell_head(command: str) -> str:
    first_line = command.strip().splitlines()[0] if command.strip() else ""
    if not first_line:
        return ""
    try:
        parts = shlex.split(first_line)
    except ValueError:
        parts = first_line.split()
    if not parts:
        return ""
    head = parts[0]
    if head in {"python", "python3", "bash", "zsh", "sh"} and len(parts) > 1:
        return f"{head} {parts[1]}"
    return head


def add_paths(bucket: list[str], paths: Iterable[str], limit: int = 12) -> None:
    for raw in paths:
        if not isinstance(raw, str):
            continue
        value = os.path.expanduser(raw)
        if not looks_like_path(value):
            continue
        unique_append(bucket, value, limit)


def update_time_bounds(session: SessionSummary, stamp: datetime) -> None:
    if session.start_at is None or stamp < session.start_at:
        session.start_at = stamp
    if session.end_at is None or stamp > session.end_at:
        session.end_at = stamp


def format_range(start: str, end: str) -> str:
    start_local = start[11:16] if len(start) >= 16 else start
    end_local = end[11:16] if len(end) >= 16 else end
    return f"{start_local}-{end_local}"


def format_datetime_local(value: datetime | None, tzinfo) -> str:
    if value is None:
        return ""
    return value.astimezone(tzinfo).isoformat(timespec="minutes")


def summarize_project_name(cwd: str) -> str:
    cwd = cwd.rstrip("/")
    return os.path.basename(cwd) or cwd or "(unknown)"


def build_summary(root: str, target_date: str, tzinfo) -> dict[str, Any]:
    sessions: dict[tuple[str, str], SessionSummary] = {}
    totals = Counter()
    records_seen = 0

    for path in sorted(Path(root).glob("**/*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    records_seen += 1
                    stamp = parse_record_timestamp(record)
                    if stamp is None:
                        continue
                    local_date = stamp.astimezone(tzinfo).date().isoformat()
                    if local_date != target_date:
                        continue

                    session_id = str(record.get("sessionId") or path.stem)
                    cwd = str(record.get("cwd") or "")
                    git_branch = str(record.get("gitBranch") or "")
                    key = (str(path), session_id)
                    session = sessions.setdefault(
                        key,
                        SessionSummary(
                            source_path=str(path),
                            session_id=session_id,
                            cwd=cwd,
                            git_branch=git_branch,
                        ),
                    )

                    session.record_count += 1
                    update_time_bounds(session, stamp)
                    if record.get("isSidechain"):
                        session.sidechain_record_count += 1

                    record_type = record.get("type")
                    message = record.get("message", {})
                    content = message.get("content") if isinstance(message, dict) else None

                    if record_type == "user":
                        if record.get("isMeta") or is_command_wrapper(content) or is_tool_result_only(content):
                            continue
                        for block in extract_text_blocks(content, "user"):
                            unique_append(session.prompt_previews, block, 6)
                            session.user_prompt_count += 1
                            totals["user_prompt_count"] += 1
                    elif record_type == "file-history-snapshot":
                        snapshot = record.get("snapshot", {})
                        if isinstance(snapshot, dict):
                            tracked = snapshot.get("trackedFileBackups", {})
                            if isinstance(tracked, dict):
                                add_paths(session.files_written, tracked.keys())
                    elif record_type == "assistant":
                        for block in extract_text_blocks(content, "assistant"):
                            unique_append(session.assistant_previews, block, 6)
                            session.assistant_text_count += 1
                            totals["assistant_text_count"] += 1
                        for tool_use in extract_tool_uses(content):
                            name = str(tool_use.get("name") or "unknown")
                            session.tool_counts[name] += 1
                            session.tool_use_count += 1
                            totals["tool_use_count"] += 1
                            input_payload = tool_use.get("input", {})
                            all_paths = list(walk_paths(input_payload))
                            if name in READ_TOOLS:
                                add_paths(session.files_read, all_paths)
                            elif name in WRITE_TOOLS:
                                add_paths(session.files_written, all_paths)
                                totals["file_write_count"] += len(
                                    [item for item in all_paths if looks_like_path(os.path.expanduser(item))]
                                )
                            else:
                                add_paths(session.files_read, all_paths, limit=8)

                            if name == "Bash" and isinstance(input_payload, dict):
                                command = str(input_payload.get("command", "")).strip()
                                head = shell_head(command)
                                if head:
                                    unique_append(session.commands, simplify_text(command, 140), 8)
                                    totals[f"bash_head::{head}"] += 1
        except OSError:
            continue

    session_items: list[dict[str, Any]] = []
    project_index: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "project_name": "",
            "cwd": "",
            "session_count": 0,
            "prompt_count": 0,
            "assistant_text_count": 0,
            "tool_use_count": 0,
            "write_count": 0,
            "sidechain_record_count": 0,
            "tool_counts": Counter(),
            "top_prompts": [],
            "files_written": [],
        }
    )

    for session in sorted(
        sessions.values(),
        key=lambda item: (
            item.start_at.isoformat() if item.start_at else "",
            item.cwd,
            item.source_path,
        ),
    ):
        start_local = format_datetime_local(session.start_at, tzinfo)
        end_local = format_datetime_local(session.end_at, tzinfo)
        project_name = summarize_project_name(session.cwd)
        tool_counts = dict(session.tool_counts.most_common(6))
        duration_minutes = 0
        if session.start_at and session.end_at:
            duration_minutes = int((session.end_at - session.start_at).total_seconds() // 60)

        session_items.append(
            {
                "project_name": project_name,
                "cwd": session.cwd,
                "git_branch": session.git_branch,
                "session_id": session.session_id,
                "source_path": session.source_path,
                "start_local": start_local,
                "end_local": end_local,
                "time_range": format_range(start_local, end_local) if start_local and end_local else "",
                "duration_minutes": duration_minutes,
                "record_count": session.record_count,
                "user_prompt_count": session.user_prompt_count,
                "assistant_text_count": session.assistant_text_count,
                "tool_use_count": session.tool_use_count,
                "sidechain_record_count": session.sidechain_record_count,
                "tool_counts": tool_counts,
                "prompt_previews": session.prompt_previews,
                "assistant_previews": session.assistant_previews,
                "files_read": session.files_read,
                "files_written": session.files_written,
                "commands": session.commands,
            }
        )

        project = project_index[session.cwd or "(unknown)"]
        project["project_name"] = project_name
        project["cwd"] = session.cwd
        project["session_count"] += 1
        project["prompt_count"] += session.user_prompt_count
        project["assistant_text_count"] += session.assistant_text_count
        project["tool_use_count"] += session.tool_use_count
        project["write_count"] += len(session.files_written)
        project["sidechain_record_count"] += session.sidechain_record_count
        project["tool_counts"].update(session.tool_counts)
        for prompt in session.prompt_previews[:3]:
            unique_append(project["top_prompts"], prompt, 6)
        for path in session.files_written:
            unique_append(project["files_written"], path, 8)

    project_items: list[dict[str, Any]] = []
    for project in sorted(
        project_index.values(),
        key=lambda item: (-item["session_count"], item["project_name"], item["cwd"]),
    ):
        project_items.append(
            {
                "project_name": project["project_name"],
                "cwd": project["cwd"],
                "session_count": project["session_count"],
                "prompt_count": project["prompt_count"],
                "assistant_text_count": project["assistant_text_count"],
                "tool_use_count": project["tool_use_count"],
                "write_count": project["write_count"],
                "sidechain_record_count": project["sidechain_record_count"],
                "top_tools": dict(project["tool_counts"].most_common(5)),
                "top_prompts": project["top_prompts"],
                "files_written": project["files_written"],
            }
        )

    bash_heads = {
        key.removeprefix("bash_head::"): value
        for key, value in totals.items()
        if key.startswith("bash_head::")
    }
    written_files = []
    for session in session_items:
        for path in session["files_written"]:
            if path not in written_files:
                written_files.append(path)

    return {
        "generated_at": datetime.now(tzinfo).isoformat(timespec="seconds"),
        "date": target_date,
        "timezone": str(tzinfo),
        "root": root,
        "records_scanned": records_seen,
        "session_count": len(session_items),
        "project_count": len(project_items),
        "user_prompt_count": totals["user_prompt_count"],
        "assistant_text_count": totals["assistant_text_count"],
        "tool_use_count": totals["tool_use_count"],
        "file_write_count": len(written_files),
        "sidechain_record_count": sum(item["sidechain_record_count"] for item in session_items),
        "bash_heads": dict(sorted(bash_heads.items(), key=lambda item: (-item[1], item[0]))[:12]),
        "projects": project_items,
        "sessions": session_items,
        "written_files": written_files,
    }


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def render_markdown(summary: dict[str, Any], note_payloads: list[tuple[str, str]], output_prefix: str, title: str) -> str:
    title_text = title or f"Daily Report - {summary['date']}"
    html_path = f"{output_prefix}-report.html"
    lines = [
        f"# {title_text}",
        "",
        f"Generated from `{summary['root']}` at `{summary['generated_at']}`.",
        "",
        "## Snapshot",
        f"- Date: `{summary['date']}`",
        f"- Timezone: `{summary['timezone']}`",
        f"- Sessions: `{summary['session_count']}`",
        f"- Projects: `{summary['project_count']}`",
        f"- User prompts: `{summary['user_prompt_count']}`",
        f"- Assistant text replies: `{summary['assistant_text_count']}`",
        f"- Tool uses: `{summary['tool_use_count']}`",
        f"- Files written: `{summary['file_write_count']}`",
        f"- Sidechain records: `{summary['sidechain_record_count']}`",
        f"- HTML report: `file://{html_path}`",
        "",
    ]

    if note_payloads:
        lines.extend(["## Agent Team Findings", ""])
        for label, body in note_payloads:
            lines.append(f"### {label}")
            lines.append("")
            lines.extend(body.splitlines())
            lines.append("")

    lines.extend(
        [
            "## Project Overview",
            "",
            "| Project | Sessions | Prompts | Writes | Top tools |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for project in summary["projects"]:
        top_tools = ", ".join(f"{name}x{count}" for name, count in project["top_tools"].items()) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(project["project_name"]),
                    str(project["session_count"]),
                    str(project["prompt_count"]),
                    str(project["write_count"]),
                    markdown_escape(top_tools),
                ]
            )
            + " |"
        )
    lines.append("")

    if summary["written_files"]:
        lines.extend(["## Deliverables", ""])
        for path in summary["written_files"][:40]:
            lines.append(f"- `{path}`")
        lines.append("")

    if summary["bash_heads"]:
        lines.extend(["## Command Mix", ""])
        for head, count in summary["bash_heads"].items():
            lines.append(f"- `{head}` x {count}")
        lines.append("")

    lines.extend(["## Session Timeline", ""])
    for session in summary["sessions"]:
        header = f"### {session['time_range'] or session['start_local'] or session['session_id']} · {session['project_name']}"
        if session["git_branch"]:
            header += f" (`{session['git_branch']}`)"
        lines.extend(
            [
                header,
                "",
                f"- Session file: `{session['source_path']}`",
                f"- Working directory: `{session['cwd'] or '(unknown)'}`",
                f"- Records: `{session['record_count']}`",
                f"- User prompts: `{session['user_prompt_count']}`",
                f"- Tool uses: `{session['tool_use_count']}`",
                f"- Sidechain records: `{session['sidechain_record_count']}`",
            ]
        )
        if session["prompt_previews"]:
            lines.append("- Prompt highlights:")
            lines.extend(f"  - {item}" for item in session["prompt_previews"][:4])
        if session["assistant_previews"]:
            lines.append("- Assistant highlights:")
            lines.extend(f"  - {item}" for item in session["assistant_previews"][:3])
        if session["files_written"]:
            lines.append("- Files written:")
            lines.extend(f"  - `{path}`" for path in session["files_written"][:6])
        if session["commands"]:
            lines.append("- Commands:")
            lines.extend(f"  - `{item}`" for item in session["commands"][:5])
        if session["tool_counts"]:
            tool_mix = ", ".join(f"{name}x{count}" for name, count in session["tool_counts"].items())
            lines.append(f"- Tool mix: {tool_mix}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_note_html(note_payloads: list[tuple[str, str]]) -> str:
    if not note_payloads:
        return ""
    chunks = ['<section class="panel"><h2>Agent Team Findings</h2>']
    for label, body in note_payloads:
        chunks.append(f"<h3>{html.escape(label)}</h3>")
        chunks.append(render_simple_markup(body))
    chunks.append("</section>")
    return "\n".join(chunks)


def render_simple_markup(text: str) -> str:
    lines = text.splitlines()
    html_parts: list[str] = []
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h4>{html.escape(stripped[4:])}</h4>")
            continue
        if stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h3>{html.escape(stripped[3:])}</h3>")
            continue
        if stripped.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{html.escape(stripped[2:])}</li>")
            continue
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        html_parts.append(f"<p>{html.escape(stripped)}</p>")
    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def render_html(summary: dict[str, Any], note_payloads: list[tuple[str, str]], output_prefix: str, title: str) -> str:
    title_text = title or f"Daily Report - {summary['date']}"
    md_path = f"{output_prefix}-report.md"
    project_rows = []
    for project in summary["projects"]:
        tools = ", ".join(f"{name}x{count}" for name, count in project["top_tools"].items()) or "-"
        project_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(project['project_name'])}</strong><div class=\"muted\">{html.escape(project['cwd'])}</div></td>"
            f"<td>{project['session_count']}</td>"
            f"<td>{project['prompt_count']}</td>"
            f"<td>{project['write_count']}</td>"
            f"<td>{html.escape(tools)}</td>"
            "</tr>"
        )

    session_blocks = []
    for session in summary["sessions"]:
        bullets = [
            f"<li><span class=\"label\">Session file</span> {html.escape(session['source_path'])}</li>",
            f"<li><span class=\"label\">Working directory</span> {html.escape(session['cwd'] or '(unknown)')}</li>",
            f"<li><span class=\"label\">Records</span> {session['record_count']}</li>",
            f"<li><span class=\"label\">User prompts</span> {session['user_prompt_count']}</li>",
            f"<li><span class=\"label\">Tool uses</span> {session['tool_use_count']}</li>",
            f"<li><span class=\"label\">Sidechain records</span> {session['sidechain_record_count']}</li>",
        ]
        if session["files_written"]:
            bullets.append(
                "<li><span class=\"label\">Files written</span> "
                + ", ".join(html.escape(item) for item in session["files_written"][:6])
                + "</li>"
            )
        if session["commands"]:
            bullets.append(
                "<li><span class=\"label\">Commands</span> "
                + ", ".join(html.escape(item) for item in session["commands"][:4])
                + "</li>"
            )
        prompt_items = "".join(f"<li>{html.escape(item)}</li>" for item in session["prompt_previews"][:4])
        assistant_items = "".join(f"<li>{html.escape(item)}</li>" for item in session["assistant_previews"][:3])
        tool_mix = ", ".join(f"{name}x{count}" for name, count in session["tool_counts"].items()) or "-"
        header = f"{session['time_range'] or session['start_local'] or session['session_id']} · {session['project_name']}"
        if session["git_branch"]:
            header += f" ({session['git_branch']})"
        session_blocks.append(
            "<details class=\"session\">"
            f"<summary>{html.escape(header)}</summary>"
            "<div class=\"session-body\">"
            f"<ul class=\"meta\">{''.join(bullets)}</ul>"
            f"<p><span class=\"label\">Tool mix</span> {html.escape(tool_mix)}</p>"
            + ("<h4>Prompt Highlights</h4><ul>" + prompt_items + "</ul>" if prompt_items else "")
            + ("<h4>Assistant Highlights</h4><ul>" + assistant_items + "</ul>" if assistant_items else "")
            + "</div></details>"
        )

    deliverables = "".join(f"<li>{html.escape(path)}</li>" for path in summary["written_files"][:40]) or "<li>None detected</li>"
    command_mix = "".join(
        f"<li><code>{html.escape(head)}</code> x {count}</li>" for head, count in summary["bash_heads"].items()
    ) or "<li>No Bash command data captured</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title_text)}</title>
  <style>
    :root {{
      --bg: #f5efe6;
      --paper: rgba(255, 252, 247, 0.92);
      --ink: #1f2933;
      --muted: #5f6c7b;
      --line: rgba(31, 41, 51, 0.14);
      --accent: #0f766e;
      --accent-soft: rgba(15, 118, 110, 0.12);
      --shadow: 0 22px 55px rgba(51, 65, 85, 0.12);
      --mono: "SFMono-Regular", "Menlo", "Monaco", monospace;
      --sans: "Iowan Old Style", "Palatino", "Georgia", serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: var(--sans);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(185, 28, 28, 0.10), transparent 22%),
        linear-gradient(180deg, #f8f4ec 0%, #efe7da 100%);
      min-height: 100vh;
    }}
    .wrap {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(255,255,255,0.82), rgba(255,248,238,0.96));
      border: 1px solid rgba(255,255,255,0.5);
      border-radius: 28px;
      box-shadow: var(--shadow);
      padding: 28px 28px 24px;
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -40px -60px auto;
      width: 180px;
      height: 180px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(15, 118, 110, 0.22), transparent 70%);
    }}
    h1, h2, h3, h4 {{ margin: 0 0 12px; font-weight: 700; }}
    h1 {{ font-size: clamp(2rem, 5vw, 3.3rem); line-height: 1.05; max-width: 14ch; }}
    h2 {{ font-size: 1.4rem; }}
    h3 {{ font-size: 1.1rem; margin-top: 18px; }}
    h4 {{ font-size: 1rem; margin-top: 18px; }}
    p {{ line-height: 1.65; margin: 0 0 12px; }}
    code {{
      font-family: var(--mono);
      font-size: 0.9em;
      background: rgba(15, 118, 110, 0.1);
      padding: 0.1rem 0.35rem;
      border-radius: 0.35rem;
    }}
    .muted {{ color: var(--muted); }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-top: 24px;
    }}
    .meta-card, .panel {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
    }}
    .meta-card {{
      padding: 16px 18px;
    }}
    .meta-card strong {{
      display: block;
      font-size: 1.45rem;
      margin-bottom: 6px;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      margin-top: 18px;
    }}
    .panel {{
      padding: 20px 22px;
      margin-top: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.96rem;
    }}
    th, td {{
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    ul {{ margin: 0 0 10px 1.2rem; padding: 0; }}
    li {{ margin-bottom: 6px; line-height: 1.5; }}
    .session {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 0;
      background: rgba(255,255,255,0.55);
      margin-bottom: 12px;
    }}
    .session summary {{
      list-style: none;
      cursor: pointer;
      padding: 16px 18px;
      font-weight: 700;
    }}
    .session summary::-webkit-details-marker {{ display: none; }}
    .session summary::after {{
      content: "+";
      float: right;
      color: var(--accent);
    }}
    .session[open] summary::after {{ content: "–"; }}
    .session-body {{
      padding: 0 18px 18px;
    }}
    .meta {{
      margin-left: 1.1rem;
    }}
    .label {{
      font-weight: 700;
      color: var(--muted);
      margin-right: 0.35rem;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 12px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 0.65rem 0.95rem;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    @media (max-width: 860px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .wrap {{ padding: 18px 12px 40px; }}
      .hero, .panel {{ border-radius: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <p class="muted">Claude session log summary</p>
      <h1>{html.escape(title_text)}</h1>
      <p>Generated from <code>{html.escape(summary['root'])}</code> at <code>{html.escape(summary['generated_at'])}</code>.</p>
      <div class="actions">
        <a class="pill" href="file://{html.escape(md_path)}">Open Markdown Pair</a>
      </div>
      <div class="meta-grid">
        <div class="meta-card"><strong>{summary['session_count']}</strong><span class="muted">Sessions</span></div>
        <div class="meta-card"><strong>{summary['project_count']}</strong><span class="muted">Projects</span></div>
        <div class="meta-card"><strong>{summary['user_prompt_count']}</strong><span class="muted">User prompts</span></div>
        <div class="meta-card"><strong>{summary['tool_use_count']}</strong><span class="muted">Tool uses</span></div>
        <div class="meta-card"><strong>{summary['file_write_count']}</strong><span class="muted">Files written</span></div>
        <div class="meta-card"><strong>{summary['sidechain_record_count']}</strong><span class="muted">Sidechain records</span></div>
      </div>
    </section>

    {render_note_html(note_payloads)}

    <section class="panel">
      <h2>Project Overview</h2>
      <table>
        <thead>
          <tr><th>Project</th><th>Sessions</th><th>Prompts</th><th>Writes</th><th>Top tools</th></tr>
        </thead>
        <tbody>
          {''.join(project_rows)}
        </tbody>
      </table>
    </section>

    <div class="layout">
      <section class="panel">
        <h2>Deliverables</h2>
        <ul>{deliverables}</ul>
      </section>
      <section class="panel">
        <h2>Command Mix</h2>
        <ul>{command_mix}</ul>
      </section>
    </div>

    <section class="panel">
      <h2>Session Timeline</h2>
      {''.join(session_blocks)}
    </section>
  </main>
</body>
</html>
"""


def load_notes(paths: list[str]) -> list[tuple[str, str]]:
    payloads: list[tuple[str, str]] = []
    for path in paths:
        file_path = Path(path)
        if not file_path.exists():
            continue
        body = file_path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        label = file_path.stem.replace("-", " ").replace("_", " ").strip().title()
        first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if first_line.startswith("#"):
            label = first_line.lstrip("# ").strip()
        payloads.append((label, body))
    return payloads


def ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def write_json(path: str, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_text(path: str, text: str) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def open_file(path: str) -> None:
    if platform.system() == "Darwin":
        subprocess.run(["open", path], check=False)
        return
    opener = "xdg-open"
    subprocess.run([opener, path], check=False)


def cmd_scan(args: argparse.Namespace) -> int:
    tzinfo = resolve_timezone(args.timezone)
    target_date = resolve_target_date(args.date, tzinfo)
    payload = build_summary(os.path.expanduser(args.root), target_date, tzinfo)
    write_json(args.output, payload)
    print(args.output)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as handle:
        summary = json.load(handle)
    note_payloads = load_notes(args.note)
    prefix = os.path.expanduser(args.output_prefix)
    md_path = f"{prefix}-report.md"
    html_path = f"{prefix}-report.html"
    markdown_text = render_markdown(summary, note_payloads, prefix, args.title)
    html_text = render_html(summary, note_payloads, prefix, args.title)
    write_text(md_path, markdown_text)
    write_text(html_path, html_text)
    if args.open:
        open_file(html_path)
    print(md_path)
    print(html_path)
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    tzinfo = resolve_timezone(args.timezone)
    target_date = resolve_target_date(args.date, tzinfo)
    prefix = os.path.expanduser(args.output_prefix or DEFAULT_PREFIX_TEMPLATE.format(date=target_date))
    scan_output = f"{prefix}.data.json"
    payload = build_summary(os.path.expanduser(args.root), target_date, tzinfo)
    write_json(scan_output, payload)
    render_args = argparse.Namespace(
        input=scan_output,
        output_prefix=prefix,
        note=args.note,
        title=args.title,
        open=args.open,
    )
    return cmd_render(render_args)


def main() -> int:
    args = parse_args()
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "render":
        return cmd_render(args)
    if args.command == "workflow":
        return cmd_workflow(args)
    raise AssertionError(f"unexpected command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
