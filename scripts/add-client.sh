#!/usr/bin/env bash
# add-client.sh — Add a new client HA site to King Alf-E
#
# Usage:
#   ./scripts/add-client.sh <name> <owner> "<nabu_casa_url>" "<ha_token>"
#
# Example:
#   ./scripts/add-client.sh brotherhood "Harley & Matt Scholz" \
#     "https://xxxx.ui.nabu.casa" "eyJhbGci..."
#
# What it does:
#   1. Adds [[ha_sites]] block to playbooks/fraser_nomad.toml
#   2. Adds HA_TOKEN_<NAME> to .env
#   3. Reminds you to restart alf-e

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAYBOOK="$REPO_ROOT/playbooks/fraser_nomad.toml"
ENVFILE="$REPO_ROOT/.env"

# ── Args ──────────────────────────────────────────────────────────────────────

if [[ $# -lt 4 ]]; then
    echo "Usage: $0 <name> <owner> <nabu_casa_url> <ha_token>"
    echo ""
    echo "  name          Short slug, no spaces (e.g. brotherhood)"
    echo "  owner         Human-readable name (e.g. \"Harley & Matt Scholz\")"
    echo "  nabu_casa_url https://xxxx.ui.nabu.casa"
    echo "  ha_token      Long-lived token from their HA Profile → Security"
    exit 1
fi

NAME="$(echo "$1" | tr '[:upper:]' '[:lower:]')"   # lowercase
OWNER="$2"
URL="$3"
TOKEN="$4"
ENV_VAR="HA_TOKEN_$(echo "$1" | tr '[:lower:]' '[:upper:]')"   # uppercase env var

# ── Validate ──────────────────────────────────────────────────────────────────

if [[ ! "$URL" =~ ^https:// ]]; then
    echo "Error: URL must start with https://"
    exit 1
fi

if grep -q "name = \"$NAME\"" "$PLAYBOOK"; then
    echo "Error: site '$NAME' already exists in $PLAYBOOK"
    exit 1
fi

if grep -q "^$ENV_VAR=" "$ENVFILE"; then
    echo "Error: $ENV_VAR already exists in .env"
    exit 1
fi

# ── Add to .env ───────────────────────────────────────────────────────────────

echo "" >> "$ENVFILE"
echo "# $OWNER" >> "$ENVFILE"
echo "$ENV_VAR=$TOKEN" >> "$ENVFILE"

echo "✓ Added $ENV_VAR to .env"

# ── Add to fraser_nomad.toml ──────────────────────────────────────────────────

cat >> "$PLAYBOOK" << TOML

[[ha_sites]]
name = "$NAME"
owner = "$OWNER"
url = "$URL"
token_env = "$ENV_VAR"
notes = "Added $(date +%Y-%m-%d)"
TOML

echo "✓ Added [[ha_sites]] block to fraser_nomad.toml"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Client '$NAME' added. Now:"
echo ""
echo "  1. Copy .env to N95:"
echo "     scp $ENVFILE alf-e@192.168.0.17:~/alf-e/.env"
echo ""
echo "  2. Commit the playbook change:"
echo "     git add playbooks/fraser_nomad.toml && git commit -m 'feat: add $NAME ha_site'"
echo ""
echo "  3. On the N95:"
echo "     cd ~/alf-e && git pull && docker compose restart alf-e"
echo ""
echo "  4. In Alf-E chat:"
echo "     \"Switch to $NAME site\""
