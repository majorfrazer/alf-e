# Alf-E — Claude Code Working Rules

## Project
Alf-E is a self-hosted personal AI agent running on an Intel N100 mini PC.
This repo contains the engine: API server, AI agent loop, playbook system, HA integration, and model router.

## Rules
- **Always ask before modifying files** — propose changes, wait for approval
- **Prefer self-healing automations** with diagnostic logging
- **Use snake_case** for all IDs, variables, file names
- **Never expose API keys** — all secrets via .env and environment variables
- **Minimal patches** to working code rather than full rewrites
- **Run tests** before committing: `pytest tests/ -v`

## Architecture
- `server.py` — FastAPI backend (replaces Streamlit)
- `engine/agent.py` — Agentic tool-use loop with Claude API
- `engine/model_router.py` — Provider-agnostic AI model routing
- `engine/ha_connector.py` — Home Assistant read + write
- `engine/memory.py` — SQLite persistent memory (multi-user)
- `engine/playbook_loader.py` — TOML playbook parser
- `engine/playbook_schema.py` — Pydantic validation schemas
- `playbooks/*.toml` — Domain-specific configurations

## Key Conventions
- Playbook TOML supports two formats: dict-of-tables (cole_sandbox) and array-of-tables (device_trader)
- HA entity IDs always come from playbook config, never hardcoded
- Model routing reads provider config from playbook, never hardcoded model IDs
- All user conversations are isolated by user_id in SQLite
- Action approval tiers: autonomous / notify / confirm / admin_only
