"""Read, mask, and update the .env file while preserving comments and ordering."""
import re
from pathlib import Path

from . import config

ENV_PATH = config.BASE_DIR / ".env"
EXAMPLE_PATH = config.BASE_DIR / ".env.example"

# Fields that contain secrets and should be masked in API responses
SECRET_FIELDS = {"GOVEE_API_KEY", "QINGPING_APP_KEY", "QINGPING_APP_SECRET",
                 "GOVEE_PASSWORD"}


def _parse_lines(path: Path) -> list[str]:
    """Return raw lines from a file, or empty list if missing."""
    if path.exists():
        return path.read_text().splitlines(keepends=True)
    return []


def read_env() -> dict[str, str]:
    """Parse the .env file into a dict of key=value pairs (ignoring comments)."""
    result: dict[str, str] = {}
    for line in _parse_lines(ENV_PATH):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def mask_value(key: str, value: str) -> str:
    """Mask a secret value for safe display: show last 4 chars only."""
    if not value:
        return ""
    if key not in SECRET_FIELDS:
        return value
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def write_env(updates: dict[str, str]) -> list[str]:
    """Merge updates into the existing .env file, preserving structure.

    If no .env exists, copies from .env.example first.
    Returns a list of field names that were actually changed.
    """
    if not ENV_PATH.exists() and EXAMPLE_PATH.exists():
        ENV_PATH.write_text(EXAMPLE_PATH.read_text())
    elif not ENV_PATH.exists():
        ENV_PATH.write_text("")

    lines = _parse_lines(ENV_PATH)
    remaining = dict(updates)  # fields still to write
    changed: list[str] = []

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped)
        if m and m.group(1) in remaining:
            key = m.group(1)
            old_val = m.group(2).strip()
            new_val = remaining.pop(key)
            if old_val != new_val:
                changed.append(key)
            new_lines.append(f"{key}={new_val}\n")
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    # Append any fields that weren't already in the file
    if remaining:
        if new_lines and not new_lines[-1].strip() == "":
            new_lines.append("\n")
        for key, val in remaining.items():
            changed.append(key)
            new_lines.append(f"{key}={val}\n")

    ENV_PATH.write_text("".join(new_lines))
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass  # Windows doesn't support Unix permissions

    return changed
