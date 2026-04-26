#!/usr/bin/with-contenv bashio
# Alf-E Add-on entrypoint — HA add-on mode (bashio)
# Uses a restart loop so connector deployments can reload the server.

# ── API Keys from add-on config ───────────────────────────────────────────────
export ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key')"

if bashio::config.exists 'google_api_key' && bashio::config.has_value 'google_api_key'; then
    export GOOGLE_API_KEY="$(bashio::config 'google_api_key')"
fi

# ── Household identity ────────────────────────────────────────────────────────
OWNER_NAME="$(bashio::config 'owner_name' 'Household Owner')"
HOUSEHOLD_NAME="$(bashio::config 'household_name' 'My Home')"
TIMEZONE="$(bashio::config 'timezone' 'Australia/Brisbane')"

# ── Playbook selection ────────────────────────────────────────────────────────
# If a custom playbook slug is set, use it. Otherwise generate one from config.
PLAYBOOK_SLUG="$(bashio::config 'playbook' '')"

if [ -n "$PLAYBOOK_SLUG" ] && [ -f "/app/playbooks/${PLAYBOOK_SLUG}.toml" ]; then
    export ALFE_PLAYBOOK="playbooks/${PLAYBOOK_SLUG}.toml"
    bashio::log.info "Using custom playbook: ${ALFE_PLAYBOOK}"
else
    # Generate a fresh household playbook from the config values
    GENERATED="/data/household.toml"
    bashio::log.info "Generating playbook for: ${HOUSEHOLD_NAME} / ${OWNER_NAME}"

    cat > "$GENERATED" << TOML
[metadata]
name = "${HOUSEHOLD_NAME}"
description = "Alf-E for ${HOUSEHOLD_NAME}"
version = "1.0.0"
owner = "${OWNER_NAME}"
timezone = "${TIMEZONE}"

personality_prompt = """
You are Alf-E: the personal AI agent for ${HOUSEHOLD_NAME}.

WHO YOU'RE TALKING TO:
- Primary user is ${OWNER_NAME}.
- Fresh installation — devices will be added over time.

PERSONALITY:
- Warm, practical, and direct. Dry humour welcome.
- Honest about limitations — no sugarcoating.
- Keep responses tight. One answer per question.

RESPONSE RULES:
- Never repeat the same opening phrase twice in a conversation.
- Do not re-list your capabilities unless explicitly asked.
"""

[llm.default]
provider = "anthropic"
model = "claude-sonnet-4-6"
api_key_env = "ANTHROPIC_API_KEY"
max_tokens = 4096
temperature = 0.7
cost_per_1k_input = 0.003
cost_per_1k_output = 0.015
capabilities = ["general", "reasoning", "analysis"]

[llm.fast]
provider = "anthropic"
model = "claude-haiku-4-5-20251001"
api_key_env = "ANTHROPIC_API_KEY"
max_tokens = 1500
temperature = 0.7
cost_per_1k_input = 0.0008
cost_per_1k_output = 0.004
capabilities = ["quick", "status"]

[llm.heavy]
provider = "anthropic"
model = "claude-sonnet-4-6"
api_key_env = "ANTHROPIC_API_KEY"
max_tokens = 8192
temperature = 0.5
cost_per_1k_input = 0.003
cost_per_1k_output = 0.015
capabilities = ["reasoning", "analysis", "code"]

[home_assistant]
url = "http://supervisor/core"
token_env = "HA_API_TOKEN"

[[ha_sites]]
name = "home"
owner = "${OWNER_NAME}"
url = "http://supervisor/core"
token_env = "HA_API_TOKEN"
notes = "Local HA — supervisor internal API"

[sensors]

[[users]]
id = "owner"
name = "${OWNER_NAME}"
role = "owner"

[[notifications]]
channel = "pwa_push"
enabled = true
urgency_min = "normal"

[energy]
peak_rate = 0.0
offpeak_rate = 0.0
feed_in_rate = 0.0
peak_start = "06:00"
peak_end = "00:00"
solar_capacity_kw = 0.0
battery_capacity_kwh = 0.0
battery_min_soc = 20
currency = "AUD"

[security]
require_approval_for_writes = true
max_actions_per_minute = 30
max_actions_per_hour = 300
audit_log_retention_days = 90
safe_file_roots = ["/data/alfe_notes", "/data/reports"]

[[boundaries]]
id = "max_daily_spend"
description = "Max daily API spend before Alf-E self-limits to fast tier only"
type = "monetary"
limit = 2.00
unit = "USD"
escalation_message = "Daily API spend hit \${value} — switching to fast tier only."

[[connectors]]
id = "ha"
enabled = true

[[connectors]]
id = "memory"
enabled = true

[[scheduled_ops]]
id = "morning_briefing"
name = "Morning Briefing"
description = "Daily morning summary for ${OWNER_NAME}"
at_time = "07:00"
notify_on_complete = true

prompt = """
Generate a short morning briefing for ${OWNER_NAME}. Include:
1. Today's weather if available
2. Any calendar events today
3. Anything unusual from the home in the last 24 hours
Keep it concise and friendly.
"""
TOML

    export ALFE_PLAYBOOK="$GENERATED"
    bashio::log.info "Generated playbook at ${GENERATED}"
fi

# ── Home Assistant access via Supervisor token ────────────────────────────────
# SUPERVISOR_TOKEN is injected automatically by HA — no need to set it manually.
bashio::log.info "Supervisor token present — using internal HA API"

# ── Restart loop ─────────────────────────────────────────────────────────────
# Watches for .restart_requested (written by _deploy_connector when a
# new connector is approved). Kills and restarts uvicorn, then clears the flag.
cd /app

while true; do
    rm -f /app/.restart_requested

    bashio::log.info "Starting Alf-E server..."
    uvicorn server:app --host 0.0.0.0 --port 8000 --no-access-log &
    UVICORN_PID=$!

    # Poll every 1s for restart flag or process death
    while kill -0 "$UVICORN_PID" 2>/dev/null; do
        sleep 1
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
