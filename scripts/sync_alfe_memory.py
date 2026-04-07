"""
Alf-E → Claude Code Memory Sync

Pulls Alf-E's memory export and writes it into the Claude Code memory
directory so every Claude Code session starts with full Alf-E context.

Called automatically by the Claude Code session-start hook in:
  /Users/frasercole/.claude/settings.json  (hooks.PreToolUse or session start)
  or
  .claude/settings.local.json  (project-level)

Can also be run manually:
  python3 scripts/sync_alfe_memory.py [--alfe-url http://localhost:8099]

What it writes:
  ~/.claude/projects/.../memory/alfe_context.md    ← all stored facts
  ~/.claude/projects/.../memory/alfe_recent.md     ← recent conversation topics
"""

import sys
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_ALFE_URL = "http://localhost:8099"
MEMORY_DIR = Path.home() / ".claude" / "projects" / "-Users-frasercole-Documents-Alf-E-alf-e-v2-0" / "memory"


def fetch_export(alfe_url: str) -> dict:
    """Fetch /api/memory/export from Alf-E."""
    url = f"{alfe_url.rstrip('/')}/api/memory/export"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"[sync] Could not reach Alf-E at {url}: {e}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"[sync] Unexpected error: {e}", file=sys.stderr)
        return {}


def write_context_memory(data: dict, memory_dir: Path) -> None:
    """Write context facts to alfe_context.md."""
    facts = data.get("context_facts", [])
    exported_at = data.get("exported_at", "unknown")[:19]

    lines = [
        "---",
        "name: Alf-E context facts",
        "description: All facts Alf-E has stored from Fraser's conversations — auto-synced at session start",
        "type: project",
        "---",
        "",
        f"*Synced from Alf-E at {exported_at}. Do not edit manually — overwritten each session.*",
        "",
    ]

    if not facts:
        lines.append("No context facts stored yet.")
    else:
        # Group by domain
        domains: dict[str, list] = {}
        for f in facts:
            domains.setdefault(f["domain"], []).append(f)

        for domain, entries in sorted(domains.items()):
            lines.append(f"## {domain}")
            for e in entries:
                updated = e.get("updated", "")[:10]
                source  = e.get("source", "")
                lines.append(f"- **{e['key']}**: {e['value']}  *(updated {updated}, via {source})*")
            lines.append("")

    lines += [
        "## Usage summary",
        f"- Messages (30d): {data.get('messages_30d', 0)}",
        f"- Cost (30d): ${data.get('cost_30d_usd', 0):.4f} USD",
        "",
        "## Active users",
    ]
    for u in data.get("users", []):
        lines.append(f"- {u['user_id']}: {u['messages']} messages (last seen {u.get('last_seen', '?')})")

    path = memory_dir / "alfe_context.md"
    path.write_text("\n".join(lines))
    print(f"[sync] Wrote {len(facts)} context facts → {path}")


def write_recent_memory(data: dict, memory_dir: Path) -> None:
    """Write recent conversation topics to alfe_recent.md."""
    topics = data.get("recent_topics", [])
    exported_at = data.get("exported_at", "unknown")[:19]

    lines = [
        "---",
        "name: Alf-E recent conversations",
        "description: What Fraser has been asking Alf-E about in the last 7 days — auto-synced at session start",
        "type: project",
        "---",
        "",
        f"*Synced from Alf-E at {exported_at}. Most recent first.*",
        "",
    ]

    if not topics:
        lines.append("No recent conversations.")
    else:
        for t in topics[:30]:  # cap at 30 to keep memory concise
            at   = t.get("at", "")
            user = t.get("user", "?")
            msg  = t.get("message", "").replace("\n", " ").strip()
            lines.append(f"- `{at}` [{user}]: {msg}")

    path = memory_dir / "alfe_recent.md"
    path.write_text("\n".join(lines))
    print(f"[sync] Wrote {len(topics)} recent topics → {path}")


def update_memory_index(memory_dir: Path) -> None:
    """Add alfe_context.md and alfe_recent.md to MEMORY.md index if not already there."""
    index_path = memory_dir / "MEMORY.md"
    if not index_path.exists():
        return

    content = index_path.read_text()
    additions = []

    if "alfe_context.md" not in content:
        additions.append("- [Alf-E context facts](alfe_context.md) — stored facts from all Alf-E conversations (auto-synced)")
    if "alfe_recent.md" not in content:
        additions.append("- [Alf-E recent conversations](alfe_recent.md) — what Fraser asked Alf-E in the last 7 days (auto-synced)")

    if additions:
        index_path.write_text(content.rstrip() + "\n" + "\n".join(additions) + "\n")
        print(f"[sync] Updated MEMORY.md index")


def main():
    parser = argparse.ArgumentParser(description="Sync Alf-E memory → Claude Code")
    parser.add_argument("--alfe-url", default=DEFAULT_ALFE_URL, help="Alf-E server URL")
    parser.add_argument("--memory-dir", default=str(MEMORY_DIR), help="Claude Code memory directory")
    args = parser.parse_args()

    memory_dir = Path(args.memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)

    print(f"[sync] Fetching Alf-E memory from {args.alfe_url}...")
    data = fetch_export(args.alfe_url)

    if not data or "error" in data:
        print("[sync] Alf-E not reachable or memory empty — skipping sync")
        return

    write_context_memory(data, memory_dir)
    write_recent_memory(data, memory_dir)
    update_memory_index(memory_dir)
    print("[sync] Done.")


if __name__ == "__main__":
    main()
