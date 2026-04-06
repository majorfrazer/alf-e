#!/bin/bash
# Alf-E standalone entrypoint — Docker / N95 mode (no bashio)
# Used by docker-compose.yml. Reads secrets from environment / .env file.

set -e

echo "[Alf-E] Starting in standalone mode..."

# ── Validate required env vars ────────────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "[Alf-E] ERROR: ANTHROPIC_API_KEY is not set. Add it to your .env file."
    exit 1
fi

if [ -z "$ALFE_PLAYBOOK" ]; then
    export ALFE_PLAYBOOK="playbooks/cole_sandbox.toml"
fi

echo "[Alf-E] Using playbook: $ALFE_PLAYBOOK"

# ── Restart loop ─────────────────────────────────────────────────────────────
# Watches for .restart_requested (written by _deploy_connector).
cd /app

while true; do
    rm -f /app/.restart_requested

    echo "[Alf-E] Starting uvicorn..."
    uvicorn server:app --host 0.0.0.0 --port 8099 --no-access-log &
    UVICORN_PID=$!

    while kill -0 "$UVICORN_PID" 2>/dev/null; do
        sleep 2
        if [ -f /app/.restart_requested ]; then
            echo "[Alf-E] Connector deployed — restarting server..."
            kill "$UVICORN_PID"
            wait "$UVICORN_PID" 2>/dev/null || true
            break
        fi
    done

    if [ ! -f /app/.restart_requested ]; then
        wait "$UVICORN_PID" 2>/dev/null
        echo "[Alf-E] Server exited."
        exit 0
    fi

    echo "[Alf-E] Reloading in 1 second..."
    sleep 1
done
