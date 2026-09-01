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
from urllib.parse import unquote, urlparse

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
        return (
            f"[tool:{tool} status={status}]\ninput: {input_str}\noutput: {output_str}"
        )
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
# Desktop Cursor stores this under ~/.config/Cursor/User (or macOS Application
# Support). Remote/SSH Cursor often uses ~/.cursor-server/data/User instead,
# and agent chats are also mirrored as JSONL under
# ~/.cursor/projects/<slug>/agent-transcripts/<id>/<id>.jsonl. --cursor tries
# the composer DB roots first (exact workspace path before parents), then falls
# back to agent-transcripts.
#
# If any of this has changed again on the installed Cursor version, the
# functions below raise a clear RuntimeError rather than silently producing
# a bogus/empty transcript.

CURSOR_PROJECTS_DIR = Path.home() / ".cursor/projects"


def _cursor_user_dirs() -> list[Path]:
    """Return existing Cursor User directories (desktop and remote/server)."""
    home = Path.home()
    candidates: list[Path] = []
    if os.uname().sysname == "Darwin":
        candidates.append(home / "Library/Application Support/Cursor/User")
    else:
        candidates.append(home / ".config/Cursor/User")
    candidates.extend(
        [
            home / ".cursor-server/data/User",
        ]
    )
    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_dir():
            continue
        seen.add(resolved)
        out.append(path)
    return out


def _cursor_global_dbs() -> list[Path]:
    dbs: list[Path] = []
    seen: set[Path] = set()
    for user_dir in _cursor_user_dirs():
        for rel in ("globalStorage/state.vscdb",):
            db = user_dir / rel
            try:
                resolved = db.resolve()
            except OSError:
                continue
            if resolved in seen or not db.is_file():
                continue
            seen.add(resolved)
            dbs.append(db)
    return dbs


# Back-compat aliases for docs / older call sites.
_CURSOR_USER_DIRS = _cursor_user_dirs()
_CURSOR_GLOBAL_DBS = _cursor_global_dbs()
CURSOR_WORKSPACE_STORAGE_DIR = next(
    (
        user_dir / "workspaceStorage"
        for user_dir in _CURSOR_USER_DIRS
        if (user_dir / "workspaceStorage").is_dir()
    ),
    Path.home() / ".config/Cursor/User/workspaceStorage",
)
CURSOR_GLOBAL_DB = (
    _CURSOR_GLOBAL_DBS[0]
    if _CURSOR_GLOBAL_DBS
    else Path.home() / ".config/Cursor/User/globalStorage/state.vscdb"
)


_SQLITE_ALLOWED_TABLES = frozenset({"ItemTable", "cursorDiskKV"})


def _sqlite_get(db_path: Path, table: str, key: str) -> str | None:
    if table not in _SQLITE_ALLOWED_TABLES:
        raise ValueError(f"Unexpected table name: {table!r}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            f"SELECT value FROM {table} WHERE key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _cursor_folder_to_path(folder: str) -> Path | None:
    """Map a workspace.json folder URI to a local filesystem path."""
    if not folder:
        return None
    if folder.startswith("file://"):
        return Path(unquote(folder[len("file://") :]))
    parsed = urlparse(folder)
    if parsed.scheme in {"vscode-remote", "vscode-vfs"}:
        path = unquote(parsed.path or "")
        return Path(path) if path else None
    if folder.startswith("/"):
        return Path(folder)
    return None


def _cursor_workspace_storage_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for user_dir in _cursor_user_dirs():
        for name in ("workspaceStorage",):
            root = user_dir / name
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved in seen or not root.is_dir():
                continue
            seen.add(resolved)
            roots.append(root)
    return roots


def _cursor_workspace_candidates(cwd: Path) -> list[Path]:
    """Workspace storage dirs for cwd, exact path matches before parents."""
    target = cwd.resolve()
    exact: list[Path] = []
    parents: list[tuple[int, Path]] = []
    roots = _cursor_workspace_storage_roots()
    if not roots:
        raise RuntimeError(
            "Cursor workspaceStorage not found under ~/.config/Cursor/User "
            "or ~/.cursor-server/data/User"
        )
    for root in roots:
        for entry in root.iterdir():
            workspace_json = next(
                (
                    entry / name
                    for name in ("workspace.json",)
                    if (entry / name).is_file()
                ),
                None,
            )
            if workspace_json is None:
                continue
            try:
                data = json.loads(workspace_json.read_text())
            except json.JSONDecodeError:
                continue
            folder_path = _cursor_folder_to_path(data.get("folder", ""))
            if folder_path is None:
                continue
            try:
                folder_path = folder_path.resolve()
            except OSError:
                continue
            if folder_path == target:
                exact.append(entry)
            else:
                try:
                    if target.is_relative_to(folder_path):
                        parents.append((len(folder_path.parts), entry))
                except (ValueError, OSError):
                    continue
    parents.sort(key=lambda item: item[0], reverse=True)
    return exact + [entry for _, entry in parents]


def _cursor_find_workspace_storage(cwd: Path) -> Path:
    candidates = _cursor_workspace_candidates(cwd)
    if not candidates:
        raise RuntimeError(
            f"No Cursor workspaceStorage entry found for {cwd.resolve()}. "
            "Cursor may not have been opened on this folder, or the format has "
            "changed again."
        )
    return candidates[0]


def _cursor_latest_composer_id_with_mtime(
    workspace_dir: Path,
) -> tuple[str, str, float]:
    state_db = next(
        (
            workspace_dir / name
            for name in ("state.vscdb",)
            if (workspace_dir / name).is_file()
        ),
        None,
    )
    if state_db is None:
        raise RuntimeError(
            f"Cursor workspace state.vscdb not found under {workspace_dir}"
        )
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
    # Cursor stores lastUpdatedAt in epoch milliseconds.
    updated_ms = latest.get("lastUpdatedAt", 0) or 0
    # Divide by 1000; guard against legacy builds that stored seconds instead
    # (values < 10^10 are implausibly small for ms, so treat as seconds).
    updated = (
        float(updated_ms) / 1000.0 if updated_ms > 10_000_000_000 else float(updated_ms)
    )
    return (
        latest["composerId"],
        latest.get("name", latest["composerId"]),
        updated,
    )


def _cursor_latest_composer_id(workspace_dir: Path) -> tuple[str, str]:
    composer_id, name, _mtime = _cursor_latest_composer_id_with_mtime(workspace_dir)
    return composer_id, name


def _cursor_bubble_text(bubble: dict) -> str | None:
    text = (bubble.get("text") or bubble.get("richText") or "").strip()
    return text or None


def _render_cursor_transcript(composer_id: str, name: str) -> str:
    global_dbs = _cursor_global_dbs()
    if not global_dbs:
        raise RuntimeError(
            "Cursor global state.vscdb not found under ~/.config/Cursor/User "
            "or ~/.cursor-server/data/User"
        )

    bubbles: list[dict] = []
    last_error: str | None = None
    for global_db in global_dbs:
        conn = sqlite3.connect(f"file:{global_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            composer_raw = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{composer_id}",),
            ).fetchone()
            if not composer_raw:
                last_error = f"No composerData:{composer_id} in {global_db}"
                continue
            composer_data = json.loads(composer_raw["value"])

            if "conversation" in composer_data:
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
            if bubbles:
                break
            last_error = (
                f"No messages found for Cursor composer {composer_id} ({name}) "
                f"in {global_db}"
            )
        finally:
            conn.close()

    if not bubbles:
        raise RuntimeError(
            last_error
            or f"No messages found for Cursor composer {composer_id} ({name})."
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


def _cursor_project_slug(path: Path) -> str:
    """Map /home/prarit/rpms -> home-prarit-rpms for ~/.cursor/projects/."""
    return str(path.resolve()).lstrip("/").replace("/", "-")


def _cursor_agent_transcript_dirs(cwd: Path) -> list[Path]:
    """agent-transcripts dirs for cwd, exact project slug first."""
    projects = CURSOR_PROJECTS_DIR
    if not projects.is_dir():
        return []
    target = cwd.resolve()
    dirs: list[Path] = []
    exact = projects / _cursor_project_slug(target) / "agent-transcripts"
    if exact.is_dir():
        dirs.append(exact)
    for parent in target.parents:
        candidate = projects / _cursor_project_slug(parent) / "agent-transcripts"
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
        if parent == Path(parent.anchor):
            break
    return dirs


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cursor_find_agent_transcript(cwd: Path, transcript_id: str | None = None) -> Path:
    dirs = _cursor_agent_transcript_dirs(cwd)
    if not dirs:
        raise RuntimeError(
            f"No Cursor agent-transcripts directory found for {cwd.resolve()} "
            f"under {CURSOR_PROJECTS_DIR}"
        )
    if transcript_id:
        for transcripts_dir in dirs:
            nested = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
            if nested.is_file():
                return nested
            flat = transcripts_dir / f"{transcript_id}.jsonl"
            if flat.is_file():
                return flat
        raise RuntimeError(
            f"No Cursor agent transcript {transcript_id!r} under {dirs[0]}"
        )

    candidates: list[Path] = []
    for transcripts_dir in dirs:
        candidates.extend(transcripts_dir.glob("*/*.jsonl"))
        candidates.extend(transcripts_dir.glob("*.jsonl"))
    if not candidates:
        raise RuntimeError(f"No Cursor agent transcript JSONL files under {dirs[0]}")
    return max(candidates, key=_safe_mtime)


def _render_agent_transcript(jsonl_path: Path) -> str:
    lines = [f"# Cursor agent transcript {jsonl_path.stem}", ""]
    with open(jsonl_path) as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            role = str(entry.get("role") or "unknown").upper()
            message = entry.get("message")
            texts: list[str] = []
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                if content.strip():
                    texts.append(content.strip())
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "text":
                        continue
                    text = str(block.get("text") or "").strip()
                    if text:
                        texts.append(text)
            if not texts:
                continue
            lines.append(f"## {role}")
            lines.extend(texts)
            lines.append("")
    if len(lines) <= 2:
        raise RuntimeError(
            f"No text messages found in Cursor agent transcript {jsonl_path}"
        )
    return "\n".join(lines)


def _export_cursor_composer_session(
    composer_id: str | None,
    *,
    cwd: Path,
    out_path: Path | None,
) -> Path:
    name = composer_id or ""
    if composer_id is None:
        errors: list[str] = []
        for workspace_dir in _cursor_workspace_candidates(cwd):
            try:
                composer_id, name = _cursor_latest_composer_id(workspace_dir)
                text = _render_cursor_transcript(composer_id, name)
                return _write_output(text, out_path, prefix=f"cursor-{composer_id}-")
            except RuntimeError as err:
                errors.append(f"{workspace_dir.name}: {err}")
                continue
        if errors:
            raise RuntimeError("; ".join(errors))
        raise RuntimeError(
            f"No Cursor workspaceStorage entry found for {cwd.resolve()}"
        )
    text = _render_cursor_transcript(composer_id, name)
    return _write_output(text, out_path, prefix=f"cursor-{composer_id}-")


def export_cursor_session(
    composer_id: str | None = None,
    *,
    cwd: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    """Export a Cursor composer/chat session transcript. Best-effort only.

    When `composer_id` is set, try it as a Composer id and then as an
    agent-transcript uuid.

    When omitted, compare the newest Composer session for this workspace with
    the newest agent-transcript for the project slug and export whichever is
    newer. Active agent chats therefore win over stale Composer history, while
    remote hosts without a Composer DB still fall back to agent-transcripts.
    """
    resolved_cwd = (cwd or Path.cwd()).resolve()
    errors: list[str] = []

    if composer_id is not None:
        try:
            return _export_cursor_composer_session(
                composer_id, cwd=resolved_cwd, out_path=out_path
            )
        except RuntimeError as err:
            errors.append(f"composer-db: {err}")
        try:
            transcript_path = _cursor_find_agent_transcript(
                resolved_cwd, transcript_id=composer_id
            )
            rendered = _render_agent_transcript(transcript_path)
            return _write_output(
                rendered, out_path, prefix=f"cursor-agent-{transcript_path.stem}-"
            )
        except RuntimeError as err:
            errors.append(f"agent-transcripts: {err}")
        raise RuntimeError(
            "Failed to export Cursor session via composer DB or "
            "agent-transcripts: " + " | ".join(errors)
        )

    composer_candidate: tuple[float, str, str] | None = None
    try:
        found_id: str | None = None
        found_name = ""
        found_mtime = 0.0
        last_err: str | None = None
        for workspace_dir in _cursor_workspace_candidates(resolved_cwd):
            try:
                found_id, found_name, found_mtime = (
                    _cursor_latest_composer_id_with_mtime(workspace_dir)
                )
                break
            except RuntimeError as err:
                last_err = f"{workspace_dir.name}: {err}"
                continue
        if found_id is None:
            raise RuntimeError(last_err or "no composer workspace matched")
        composer_text = _render_cursor_transcript(found_id, found_name)
        composer_candidate = (found_mtime, composer_text, f"cursor-{found_id}-")
    except RuntimeError as err:
        errors.append(f"composer-db: {err}")

    agent_candidate: tuple[float, str, str] | None = None
    try:
        transcript_path = _cursor_find_agent_transcript(resolved_cwd)
        agent_text = _render_agent_transcript(transcript_path)
        agent_candidate = (
            float(transcript_path.stat().st_mtime),
            agent_text,
            f"cursor-agent-{transcript_path.stem}-",
        )
    except RuntimeError as err:
        errors.append(f"agent-transcripts: {err}")

    if composer_candidate is None and agent_candidate is None:
        raise RuntimeError(
            "Failed to export Cursor session via composer DB or "
            "agent-transcripts: " + " | ".join(errors)
        )
    if agent_candidate is None:
        assert composer_candidate is not None
        _mtime, rendered, prefix = composer_candidate
    elif composer_candidate is None:
        _mtime, rendered, prefix = agent_candidate
    else:
        _mtime, rendered, prefix = max(
            (composer_candidate, agent_candidate), key=lambda item: item[0]
        )
    return _write_output(rendered, out_path, prefix=prefix)


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
