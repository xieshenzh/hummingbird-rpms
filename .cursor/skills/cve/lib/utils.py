import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---- Skill dir bootstrap (makes cve_analysis_bridge importable from lib modules) ----

_LIB_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _LIB_DIR.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


# ---- Repo path helpers ----


def repo_root() -> Path:
    # parents[4]: lib/utils.py → lib/ → cve/ → skills/ → .cursor/ → repo root
    return Path(__file__).resolve().parents[4]


def _pkg_dir(package: str) -> Path:
    return repo_root() / "rpms" / package


# ---- Subprocess ----


def run_command(
    args: list[str],
    *,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, check=False, capture_output=True, text=True, cwd=cwd
        )
    except OSError as err:
        raise RuntimeError(f"Failed to execute {' '.join(args)}: {err}") from err


# ---- JSON path helper ----


def json_str(data: dict[str, Any], *path: str, default: str = "") -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return str(current)


# ---- HTTP helpers ----


def _http_read(
    url: str, timeout: float, extra_headers: dict[str, str] | None = None
) -> bytes:
    headers = {"User-Agent": "hummingbird-cve-helper"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code} fetching {url}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Failed fetching {url}: {err}") from err


def http_get_text(
    url: str,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
) -> str:
    return _http_read(url, timeout, extra_headers).decode("utf-8", errors="replace")


def http_get_json(
    url: str,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    text = http_get_text(url, timeout=timeout, extra_headers=extra_headers)
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON from {url}: {err}") from err


def http_download(url: str, dest: Path, timeout: float = 120.0) -> None:
    dest.write_bytes(_http_read(url, timeout))


# ---- Normalizers ----


def normalize_hum_key(value: str) -> str:
    trimmed = value.strip().upper()
    if trimmed.startswith("HUM-"):
        return trimmed
    if re.fullmatch(r"\d+", trimmed):
        return f"HUM-{trimmed}"
    raise ValueError(f"Invalid ticket key: {value}")


def normalize_nvr(nvr: str) -> str:
    name = Path(nvr.strip()).name
    return name.removesuffix(".src.rpm")


# ---- Misc helpers ----


def uses_vendored_deps(package: str) -> bool:
    return (_pkg_dir(package) / "go-vendor-tools.toml").exists()


# ---- Extract helper ----


def extract_vendor_field(block: str, field: str) -> str:
    m = re.search(rf"^\s{{2}}{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""
