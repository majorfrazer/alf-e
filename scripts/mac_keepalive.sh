#!/bin/bash
# Alf-E Mac Keepalive — run before leaving for holidays
# Prevents the Mac from sleeping and killing the local dev server.
# Only needed until N95 arrives April 17.
#
# Usage:
#   bash scripts/mac_keepalive.sh
#
# To stop it later:
#   killall caffeinate

echo "[Alf-E] Starting Mac keepalive..."
echo "[Alf-E] Mac will NOT sleep while this is running."
echo "[Alf-E] To stop: killall caffeinate"
echo ""

# -d: prevent display sleep
# -i: prevent idle sleep
# -m: prevent disk sleep
caffeinate -d -i -m &
CAFF_PID=$!
echo "[Alf-E] caffeinate running (PID $CAFF_PID)"

# Also verify Tailscale is connected
if command -v tailscale &>/dev/null; then
    STATUS=$(tailscale status 2>/dev/null | head -3)
    echo ""
    echo "[Tailscale] Status:"
    echo "$STATUS"
else
    echo "[Tailscale] Not found in PATH — check Tailscale app is running"
fi

echo ""
echo "[Alf-E] Pre-holiday checklist:"
echo "  ✓ Mac will not sleep"
echo "  ? Tailscale running (check above)"
echo "  ? Gmail credentials ready (run: python3 scripts/gmail_auth.py)"
echo "  ? ANTHROPIC_API_KEY updated in .env if needed"
echo "  ? HA Green Alf-E add-on at v2.3.0 (update in HA UI)"
echo ""
echo "Press Ctrl+C to stop keepalive when done."

wait $CAFF_PID
