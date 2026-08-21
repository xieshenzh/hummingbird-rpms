"""Load hummingbird_cve_analysis libs and rhjira-compatible Jira credentials.

Write commands in cve_helper.py import jira_client / pulp / gitlab_client from
the hummingbird-cve-analysis package. This module does not call cve_analysis.py.

Install one of:
  pip install 'hummingbird-cve-analysis @ git+https://gitlab.com/redhat/hummingbird/tools.git#subdirectory=hummingbird-cve-analysis'
  export HUMMINGBIRD_TOOLS_ROOT=/path/to/hummingbird/tools
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Mapping

DEFAULT_JIRA_URL = "https://redhat.atlassian.net"
DEFAULT_AGENT_ENV = Path.home() / ".config" / "rhjira" / "agent.env"
ENV_ASSIGNMENT_RE = re.compile(
    r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
TOOLS_ROOT_ENV = "HUMMINGBIRD_TOOLS_ROOT"
PACKAGE_SUBDIR = "hummingbird-cve-analysis"


class AnalysisImportError(RuntimeError):
    """Raised when hummingbird_cve_analysis cannot be imported."""


@dataclass(frozen=True)
class JiraAuth:
    token: str
    base_url: str
    basic_auth_user: str | None = None
    source: str = "env"


def parse_agent_env(text: str) -> dict[str, str]:
    """Parse `export KEY=VALUE` lines from rhjira agent.env without executing it.

    Strips matching outer single- or double-quotes only. Nested or escaped
    quotes within values are not handled; this matches typical agent.env
    content.
    """
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_ASSIGNMENT_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def read_agent_env(path: Path | None = None) -> dict[str, str]:
    env_path = path or DEFAULT_AGENT_ENV
    try:
        return parse_agent_env(env_path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def load_jira_auth(
    environ: Mapping[str, str] | None = None,
    *,
    agent_env_path: Path | None = None,
) -> JiraAuth:
    """Resolve Jira REST credentials, mapping rhjira agent.env names.

    Preference order for the token: process env, then ~/.config/rhjira/agent.env.
    JIRA_URL and JIRA_SERVER are the same host (rhjira uses SERVER).
    JIRA_EMAIL / JIRA_USER become Basic-auth user for jira_client.
    """
    env = dict(os.environ if environ is None else environ)
    source = "env"
    if not env.get("JIRA_TOKEN"):
        file_vals = read_agent_env(agent_env_path)
        if file_vals.get("JIRA_TOKEN"):
            source = str(agent_env_path or DEFAULT_AGENT_ENV)
            for key, value in file_vals.items():
                env.setdefault(key, value)

    token = (env.get("JIRA_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "JIRA_TOKEN is not set. Install rhjira and configure "
            f"{DEFAULT_AGENT_ENV}, or export JIRA_TOKEN."
        )
    base_url = (
        env.get("JIRA_URL") or env.get("JIRA_SERVER") or DEFAULT_JIRA_URL
    ).rstrip("/")
    user = (env.get("JIRA_USER") or env.get("JIRA_EMAIL") or "").strip() or None
    return JiraAuth(
        token=token,
        base_url=base_url,
        basic_auth_user=user,
        source=source,
    )


def tools_package_roots(environ: Mapping[str, str] | None = None) -> list[Path]:
    """Candidate directories that contain the hummingbird_cve_analysis package."""
    env = os.environ if environ is None else environ
    roots: list[Path] = []
    configured = (env.get(TOOLS_ROOT_ENV) or "").strip()
    if configured:
        roots.append(Path(configured).expanduser() / PACKAGE_SUBDIR)
        roots.append(Path(configured).expanduser())
    # Sibling of this rpms checkout: ../tools/hummingbird-cve-analysis
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent.parent
    roots.append(repo_root.parent / "tools" / PACKAGE_SUBDIR)
    return roots


def ensure_analysis_on_path(environ: Mapping[str, str] | None = None) -> Path | None:
    """Put a tools checkout on sys.path if the package is not already installed."""
    if "hummingbird_cve_analysis" in sys.modules:
        module = sys.modules["hummingbird_cve_analysis"]
        path = getattr(module, "__file__", None)
        return Path(path).resolve() if path else None
    try:
        import hummingbird_cve_analysis as installed

        path = getattr(installed, "__file__", None)
        return Path(path).resolve() if path else None
    except ImportError:
        pass

    for root in tools_package_roots(environ):
        pkg = root / "hummingbird_cve_analysis"
        if pkg.is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
            return root
    return None


def import_analysis_lib(name: str) -> ModuleType:
    """Import hummingbird_cve_analysis.lib.<name> or raise AnalysisImportError."""
    ensure_analysis_on_path()
    try:
        return __import__(f"hummingbird_cve_analysis.lib.{name}", fromlist=[name])
    except ImportError as err:
        raise AnalysisImportError(
            "hummingbird_cve_analysis is not installed. "
            f"Set {TOOLS_ROOT_ENV} to a hummingbird/tools checkout, or pip install "
            "the hummingbird-cve-analysis package from that repo "
            f"(subdirectory {PACKAGE_SUBDIR})."
        ) from err


def jira_client_module() -> ModuleType:
    return import_analysis_lib("jira_client")


def pulp_module() -> ModuleType:
    return import_analysis_lib("pulp")


def gitlab_client_module() -> ModuleType:
    return import_analysis_lib("gitlab_client")
