#!/usr/bin/with-contenv bashio
# Alf-E Add-on entrypoint — HA add-on mode (bashio)
# Uses a restart loop so connector deployments can reload the server.

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
bashio::log.info "Supervisor token available — using internal HA API"

# ── Restart loop ─────────────────────────────────────────────────────────────
# Watches for .restart_requested (written by _deploy_connector when a
# new connector is approved). Kills and restarts uvicorn, then clears the flag.
cd /app

while true; do
    rm -f /app/.restart_requested

    bashio::log.info "Starting Alf-E server..."
    uvicorn server:app --host 0.0.0.0 --port 8000 --no-access-log &
    UVICORN_PID=$!

    # Poll every 2s for restart flag or process death
    while kill -0 "$UVICORN_PID" 2>/dev/null; do
        sleep 2
        if [ -f /app/.restart_requested ]; then
            bashio::log.info "Connector deployed — restarting server..."
            kill "$UVICORN_PID"
            wait "$UVICORN_PID" 2>/dev/null || true
            break
        fi
    done

    # If no restart flag and process exited — propagate the exit code
    if [ ! -f /app/.restart_requested ]; then
        wait "$UVICORN_PID" 2>/dev/null
        EXIT_CODE=$?
        bashio::log.error "Server exited unexpectedly (code $EXIT_CODE)"
        exit $EXIT_CODE
    fi

    bashio::log.info "Reloading in 1 second..."
    sleep 1
done
