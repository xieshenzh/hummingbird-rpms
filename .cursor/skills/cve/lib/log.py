"""Export coding-agent session transcripts for attachment to Jira tickets.

Different agents store conversation history in different, mostly
undocumented formats. This module renders a session/conversation into a
plain-text transcript suitable for `cve_helper.py log-message`, so agents
don't need to manually hunt for log files or paste raw JSON/JSONL.

Supported agents:

- **OpenCode**: no per-session file. `~/.local/share/opencode/log/opencode.log`
  is one shared, unbounded log across every session/project and must never be
  attached. Full transcripts live in `~/.local/share/opencode/opencode.db`
  (SQLite: `session`, `message`, `part` tables).
- **Claude Code**: one JSONL file per session under
  `~/.claude/projects/<cwd-with-slashes-as-dashes>/<session-uuid>.jsonl`.
- **Cursor**: best-effort. Format is undocumented and has changed across
  Cursor releases; see `export_cursor_session()` for details and caveats.
"""

import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

# ---- OpenCode ----

OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"


def _opencode_format_part(part: dict) -> str | None:
    part_type = part.get("type")
    if part_type == "text":
        text = part.get("text", "").strip()
        return text or None
    if part_type == "reasoning":
        text = part.get("text", "").strip()
        return f"[reasoning] {text}" if text else None
    if part_type == "tool":
        tool = part.get("tool", "?")
        state = part.get("state", {})
        status = state.get("status", "")
        input_str = json.dumps(state.get("input", {}), ensure_ascii=False)
        if len(input_str) > 500:
            input_str = input_str[:500] + "...(truncated)"
        output_str = str(state.get("output", ""))
        if len(output_str) > 2000:
            output_str = output_str[:2000] + "...(truncated)"
        return f"[tool:{tool} status={status}]\ninput: {input_str}\noutput: {output_str}"
    if part_type in ("step-start", "step-finish"):
        return None
    return f"[{part_type}] {json.dumps(part, ensure_ascii=False)[:300]}"


def _opencode_latest_session_id(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id FROM session ORDER BY time_updated DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f"No OpenCode sessions found in {db_path}")
    return row[0]


def _render_opencode_transcript(session_id: str, db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        session_row = conn.execute(
            "SELECT title FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        if not session_row:
            raise RuntimeError(f"Session {session_id} not found in {db_path}")
        title = session_row["title"]

        messages = conn.execute(
            "SELECT id, data FROM message WHERE session_id = ? "
            "ORDER BY time_created, id",
            (session_id,),
        ).fetchall()

        lines = [f"# OpenCode session {session_id}: {title}", ""]
        for msg in messages:
            role = json.loads(msg["data"]).get("role", "?")
            lines.append(f"## {role.upper()}")
            parts = conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created, id",
                (msg["id"],),
            ).fetchall()
            for part_row in parts:
                rendered = _opencode_format_part(json.loads(part_row["data"]))
                if rendered:
                    lines.append(rendered)
                    lines.append("")
            lines.append("")
        return "\n".join(lines)
    finally:
        conn.close()


def export_opencode_session(
    session_id: str | None = None,
    *,
    db_path: Path = OPENCODE_DB,
    out_path: Path | None = None,
) -> Path:
    """Export an OpenCode session transcript to a file and return its path.

    Defaults to the most recently updated session if `session_id` is omitted.
    """
    if not db_path.is_file():
        raise RuntimeError(f"OpenCode database not found: {db_path}")
    resolved_id = session_id or _opencode_latest_session_id(db_path)
    text = _render_opencode_transcript(resolved_id, db_path)
    return _write_output(text, out_path, prefix=f"opencode-{resolved_id}-")


# ---- Claude Code ----

CLAUDE_PROJECTS_DIR = Path.home() / ".claude/projects"


def _claude_project_dir_name(path: Path) -> str:
    # Claude Code turns the launch cwd into a directory name by replacing
    # every path separator (and leading/embedded dots) with a dash, e.g.
    # /Users/me/rpms -> -Users-me-rpms
    return str(path.resolve()).replace("/", "-")


def _claude_find_latest_jsonl(cwd: Path, projects_dir: Path) -> Path:
    exact = projects_dir / _claude_project_dir_name(cwd)
    candidates: list[Path] = []
    if exact.is_dir():
        candidates.extend(exact.glob("*.jsonl"))
    if not candidates:
        # Fall back to matching any project dir containing the repo's
        # basename, in case the session was launched from a different cwd
        # (e.g. a worktree) under the same project family.
        needle = cwd.resolve().name
        for project_dir in projects_dir.glob(f"*{needle}*"):
            if project_dir.is_dir():
                candidates.extend(project_dir.glob("*.jsonl"))
    if not candidates:
        raise RuntimeError(
            f"No Claude Code session files found for {cwd} under {projects_dir}"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _claude_format_content_block(block: dict) -> str | None:
    block_type = block.get("type")
    if block_type == "text":
        text = block.get("text", "").strip()
        return text or None
    if block_type == "thinking":
        text = block.get("thinking", "").strip()
        return f"[reasoning] {text}" if text else None
    if block_type == "tool_use":
        name = block.get("name", "?")
        input_str = json.dumps(block.get("input", {}), ensure_ascii=False)
        if len(input_str) > 500:
            input_str = input_str[:500] + "...(truncated)"
        return f"[tool_use:{name}]\ninput: {input_str}"
    if block_type == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        content_str = str(content)
        if len(content_str) > 2000:
            content_str = content_str[:2000] + "...(truncated)"
        return f"[tool_result]\n{content_str}"
    return None


def _render_claude_transcript(jsonl_path: Path) -> str:
    lines = [f"# Claude Code session {jsonl_path.stem}", ""]
    with open(jsonl_path) as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            entry = json.loads(raw_line)
            entry_type = entry.get("type")
            if entry_type not in ("user", "assistant"):
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role", entry_type)
            content = message.get("content")
            rendered_blocks: list[str] = []
            if isinstance(content, str):
                if content.strip():
                    rendered_blocks.append(content.strip())
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        rendered = _claude_format_content_block(block)
                        if rendered:
                            rendered_blocks.append(rendered)
            if not rendered_blocks:
                continue
            lines.append(f"## {role.upper()}")
            lines.extend(rendered_blocks)
            lines.append("")
    return "\n".join(lines)


def export_claude_session(
    session_path: str | Path | None = None,
    *,
    cwd: Path | None = None,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    out_path: Path | None = None,
) -> Path:
    """Export a Claude Code session transcript to a file and return its path.

    `session_path` may be an explicit `.jsonl` file. Otherwise the most
    recently modified session for `cwd` (default: current directory) is used.
    """
    if session_path is None:
        session_path = _claude_find_latest_jsonl(cwd or Path.cwd(), projects_dir)
    else:
        session_path = Path(session_path)
    if not session_path.is_file():
        raise RuntimeError(f"Claude Code session file not found: {session_path}")
    text = _render_claude_transcript(session_path)
    return _write_output(text, out_path, prefix=f"claude-{session_path.stem}-")


# ---- Cursor ----
#
# Best-effort only: Cursor's on-disk chat format is undocumented and has
# changed across releases (see forum reports around Cursor 0.43). This
# implementation follows the scheme used by open-source exporters
# (e.g. saharmor/cursor-view, somogyijanos/cursor-chat-export):
#
#   workspaceStorage/<hash>/workspace.json          -> {"folder": "file://<path>"}
#   workspaceStorage/<hash>/state.vscdb ItemTable    key "composer.composerData"
#       -> {"allComposers": [{"composerId": ..., "name": ..., ...}, ...]}
#   globalStorage/state.vscdb cursorDiskKV           key "composerData:<id>"
#       -> conversation content (older builds) or a pointer list
#          "fullConversationHeadersOnly": [{"bubbleId": ...}, ...] (newer builds)
#   globalStorage/state.vscdb cursorDiskKV           key "bubbleId:<id>:<bubbleId>"
#       -> one message: {"type": 1|2, "text"/"richText": ..., ...} (1=user, 2=assistant)
#
# If any of this has changed again on the installed Cursor version, the
# functions below raise a clear RuntimeError rather than silently producing
# a bogus/empty transcript.

if os.uname().sysname == "Darwin":
    _CURSOR_USER_DIR = Path.home() / "Library/Application Support/Cursor/User"
else:
    _CURSOR_USER_DIR = Path.home() / ".config/Cursor/User"

CURSOR_WORKSPACE_STORAGE_DIR = _CURSOR_USER_DIR / "workspaceStorage"
CURSOR_GLOBAL_DB = _CURSOR_USER_DIR / "globalStorage/state.vscdb"


_SQLITE_ALLOWED_TABLES = frozenset({"ItemTable", "cursorDiskKV"})


def _sqlite_get(db_path: Path, table: str, key: str) -> str | None:
    if table not in _SQLITE_ALLOWED_TABLES:
        raise ValueError(f"Unexpected table name: {table!r}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            f"SELECT value FROM {table} WHERE key = ?",  # noqa: S608 (table checked above)
            (key,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _cursor_find_workspace_storage(cwd: Path) -> Path:
    if not CURSOR_WORKSPACE_STORAGE_DIR.is_dir():
        raise RuntimeError(
            f"Cursor workspaceStorage not found: {CURSOR_WORKSPACE_STORAGE_DIR}"
        )
    target = cwd.resolve()
    for entry in CURSOR_WORKSPACE_STORAGE_DIR.iterdir():
        workspace_json = entry / "workspace.json"
        if not workspace_json.is_file():
            continue
        try:
            data = json.loads(workspace_json.read_text())
        except json.JSONDecodeError:
            continue
        folder = data.get("folder", "")
        folder_path = Path(folder.removeprefix("file://"))
        if folder_path == target or target.is_relative_to(folder_path):
            return entry
    raise RuntimeError(
        f"No Cursor workspaceStorage entry found for {target}. "
        "Cursor may not have been opened on this folder, or the format has "
        "changed again."
    )


def _cursor_latest_composer_id(workspace_dir: Path) -> tuple[str, str]:
    state_db = workspace_dir / "state.vscdb"
    if not state_db.is_file():
        raise RuntimeError(f"Cursor workspace state.vscdb not found: {state_db}")
    raw = _sqlite_get(state_db, "ItemTable", "composer.composerData")
    if not raw:
        raise RuntimeError(
            f"No composer.composerData in {state_db}; Cursor storage schema "
            "may have changed."
        )
    data = json.loads(raw)
    composers = data.get("allComposers") or []
    if not composers:
        raise RuntimeError(f"No composer sessions recorded in {state_db}")
    latest = max(composers, key=lambda c: c.get("lastUpdatedAt", 0))
    return latest["composerId"], latest.get("name", latest["composerId"])


def _cursor_bubble_text(bubble: dict) -> str | None:
    text = (bubble.get("text") or bubble.get("richText") or "").strip()
    return text or None


def _render_cursor_transcript(composer_id: str, name: str) -> str:
    if not CURSOR_GLOBAL_DB.is_file():
        raise RuntimeError(f"Cursor global state.vscdb not found: {CURSOR_GLOBAL_DB}")

    conn = sqlite3.connect(f"file:{CURSOR_GLOBAL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        composer_raw = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (f"composerData:{composer_id}",),
        ).fetchone()
        if not composer_raw:
            raise RuntimeError(
                f"No composerData:{composer_id} in {CURSOR_GLOBAL_DB}; Cursor "
                "storage schema may have changed."
            )
        composer_data = json.loads(composer_raw["value"])

        bubbles: list[dict] = []
        if "conversation" in composer_data:
            # Older builds embed the full conversation inline.
            bubbles = composer_data["conversation"]
        else:
            headers = composer_data.get("fullConversationHeadersOnly", [])
            for header in headers:
                bubble_id = header.get("bubbleId")
                if not bubble_id:
                    continue
                bubble_raw = conn.execute(
                    "SELECT value FROM cursorDiskKV WHERE key = ?",
                    (f"bubbleId:{composer_id}:{bubble_id}",),
                ).fetchone()
                if bubble_raw:
                    bubbles.append(json.loads(bubble_raw["value"]))
    finally:
        conn.close()

    if not bubbles:
        raise RuntimeError(
            f"No messages found for Cursor composer {composer_id} ({name})."
        )

    lines = [f"# Cursor session {composer_id}: {name}", ""]
    for bubble in bubbles:
        role = "user" if bubble.get("type") in (1, "user") else "assistant"
        text = _cursor_bubble_text(bubble)
        if not text:
            continue
        lines.append(f"## {role.upper()}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def export_cursor_session(
    composer_id: str | None = None,
    *,
    cwd: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    """Export a Cursor composer/chat session transcript. Best-effort only.

    Cursor's storage format is undocumented and has changed across releases;
    if this fails, dump raw rows from `cursorDiskKV`/`ItemTable` and adjust
    the parsing above rather than guessing.
    """
    name = composer_id or ""
    if composer_id is None:
        workspace_dir = _cursor_find_workspace_storage(cwd or Path.cwd())
        composer_id, name = _cursor_latest_composer_id(workspace_dir)
    text = _render_cursor_transcript(composer_id, name)
    return _write_output(text, out_path, prefix=f"cursor-{composer_id}-")


# ---- Shared helpers ----


def _write_output(text: str, out_path: Path | None, *, prefix: str) -> Path:
    if out_path is not None:
        out_path.write_text(text)
        return out_path
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".txt")
    with open(fd, "w") as fh:
        fh.write(text)
    return Path(name)


AGENT_EXPORTERS: dict[str, Callable[..., Path]] = {
    "opencode": export_opencode_session,
    "claude": export_claude_session,
    "cursor": export_cursor_session,
}


_AGENT_EXPORTER_KWARGS: dict[str, str] = {
    "opencode": "session_id",
    "claude": "session_path",
    "cursor": "composer_id",
}


def export_agent_log(agent: str, session_id: str | None = None) -> Path:
    """Dispatch to the exporter for `agent` ("opencode", "claude", or "cursor")."""
    try:
        exporter = AGENT_EXPORTERS[agent]
        kwarg = _AGENT_EXPORTER_KWARGS[agent]
    except KeyError:
        raise ValueError(
            f"Unknown agent {agent!r}; expected one of {sorted(AGENT_EXPORTERS)}"
        ) from None
    return exporter(**{kwarg: session_id}) if session_id else exporter()
