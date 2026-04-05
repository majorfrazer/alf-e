#!/usr/bin/with-contenv bashio
# Alf-E Add-on entrypoint
# Reads configuration from HA add-on options and starts the server.

set -e

bashio::log.info "Starting Alf-E v2.0..."

# ── API Keys from add-on config ───────────────────────────────────────────────
export ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key')"

if bashio::config.exists 'google_api_key' && bashio::config.has_value 'google_api_key'; then
    export GOOGLE_API_KEY="$(bashio::config 'google_api_key')"
fi

# ── Playbook selection ────────────────────────────────────────────────────────
PLAYBOOK="$(bashio::config 'playbook')"
export ALFE_PLAYBOOK="playbooks/${PLAYBOOK}.toml"
bashio::log.info "Using playbook: ${ALFE_PLAYBOOK}"

# ── Home Assistant access via Supervisor token ────────────────────────────────
# SUPERVISOR_TOKEN is injected automatically by HA — no need to set it manually.
# server.py detects it and uses the internal supervisor URL.
bashio::log.info "Supervisor token available — will use internal HA API"

# ── Start server ──────────────────────────────────────────────────────────────
cd /app
exec uvicorn server:app --host 0.0.0.0 --port 8000 --no-access-log
