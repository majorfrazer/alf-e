#!/bin/bash
# Alf-E installer for Linux
# Run from terminal:  ./install-linux.sh

set -e
cd "$(dirname "$0")"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Alf-E installer — Linux"
echo "════════════════════════════════════════════════════════════"
echo ""

if ! command -v docker &> /dev/null; then
  echo "✗ Docker is not installed."
  echo ""
  echo "  Install Docker on Ubuntu/Debian:"
  echo "    curl -fsSL https://get.docker.com | sh"
  echo "    sudo usermod -aG docker \$USER"
  echo "    (log out + back in so the group takes effect)"
  echo ""
  echo "  Then run this installer again."
  exit 1
fi

if ! docker info &> /dev/null; then
  echo "✗ Docker is installed but you can't talk to it."
  echo ""
  echo "  Run:  sudo usermod -aG docker \$USER"
  echo "  Then log out and back in."
  exit 1
fi

echo "✓ Docker is running"
echo ""

if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN=$(openssl rand -base64 32)
  sed -i "s|ALFE_API_TOKEN=.*|ALFE_API_TOKEN=$TOKEN|" .env

  echo "✓ Generated your PWA login token (saved to .env)"
  echo ""
  echo "────────────────────────────────────────────────────────────"
  echo "  NOW: paste your two API keys into the .env file"
  echo "────────────────────────────────────────────────────────────"
  echo ""
  echo "  Edit .env with your favourite editor:"
  echo "    nano .env    (or vim, micro, etc.)"
  echo ""
  echo "  Replace:"
  echo "    ANTHROPIC_API_KEY=REPLACE_ME  → your Anthropic key"
  echo "    HA_API_TOKEN=REPLACE_ME                    → your HA token"
  echo ""
  echo "  Save, then run this installer again."
  exit 0
fi

if grep -q "REPLACE_ME" .env; then
  echo "✗ .env still has placeholder values."
  echo ""
  echo "  Edit .env and replace the REPLACE_ME placeholders, then"
  echo "  run this installer again."
  exit 1
fi

echo "✓ .env looks good"
echo ""
echo "Starting Alf-E... (first run downloads ~500MB, be patient)"
echo ""

docker compose up -d

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Alf-E is starting. Give it 30 seconds, then open:"
echo ""
echo "    http://localhost:8099"
echo ""
echo "  (From another machine on your network: http://<this-pc-ip>:8099)"
echo ""
echo "  Your PWA login token is in the .env file (ALFE_API_TOKEN)."
echo "════════════════════════════════════════════════════════════"
echo ""
