"""File tool handlers: read_file, write_file."""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("alfe.tools.files")


def safe_path(path: str, safe_roots: list[str]) -> Optional[Path]:
    """Return a resolved Path if within allowed roots, else None."""
    if not safe_roots:
        safe_roots = ["/data/alfe_notes"]

    resolved = Path(path).resolve()
    for root in safe_roots:
        if str(resolved).startswith(str(Path(root).resolve())):
            return resolved
    return None


def handle_read_file(inp: dict, safe_roots: list[str]) -> str:
    path = safe_path(inp["path"], safe_roots)
    if not path:
        return f"Access denied: '{inp['path']}' is outside safe paths."
    if not path.exists():
        return f"File not found: {inp['path']}"
    try:
        content = path.read_text(encoding="utf-8")
        return f"Contents of {path}:\n\n{content}"
    except Exception as e:
        return f"Error reading file: {e}"


def handle_write_file(inp: dict, safe_roots: list[str]) -> str:
    path = safe_path(inp["path"], safe_roots)
    if not path:
        return f"Access denied: '{inp['path']}' is outside safe paths."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if inp.get("append") else "w"
        with open(path, mode, encoding="utf-8") as f:
            f.write(inp["content"])
        action = "Appended to" if inp.get("append") else "Written"
        return f"{action} {path} ({len(inp['content'])} chars)."
    except Exception as e:
        return f"Error writing file: {e}"
