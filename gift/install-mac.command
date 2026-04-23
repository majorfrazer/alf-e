#!/bin/bash
# Alf-E installer for macOS
# Double-click this file in Finder.

set -e
cd "$(dirname "$0")"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Alf-E installer — macOS"
echo "════════════════════════════════════════════════════════════"
echo ""

if ! command -v docker &> /dev/null; then
  echo "✗ Docker is not installed."
  echo ""
  echo "  Install Docker Desktop first:"
  echo "    https://www.docker.com/products/docker-desktop"
  echo ""
  echo "  Opening the download page now. Run this installer again"
  echo "  once Docker Desktop is running (whale icon in menu bar)."
  echo ""
  open "https://www.docker.com/products/docker-desktop"
  read -p "Press Enter to close..."
  exit 1
fi

if ! docker info &> /dev/null; then
  echo "✗ Docker is installed but not running."
  echo ""
  echo "  Start Docker Desktop (whale icon in menu bar), wait for it"
  echo "  to say 'Docker is running', then run this installer again."
  echo ""
  open -a Docker
  read -p "Press Enter to close..."
  exit 1
fi

echo "✓ Docker is running"
echo ""

if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN=$(openssl rand -base64 32)
  sed -i '' "s|ALFE_API_TOKEN=.*|ALFE_API_TOKEN=$TOKEN|" .env

  echo "✓ Generated your PWA login token (saved to .env)"
  echo ""
  echo "────────────────────────────────────────────────────────────"
  echo "  NOW: paste your two API keys into the .env file"
  echo "────────────────────────────────────────────────────────────"
  echo ""
  echo "  Opening .env in TextEdit. Replace:"
  echo "    ANTHROPIC_API_KEY=REPLACE_ME  → your Anthropic key"
  echo "    HA_API_TOKEN=REPLACE_ME                    → your HA token"
  echo ""
  echo "  Save the file (Cmd+S), close it, then run this installer again."
  echo ""
  open -e .env
  read -p "Press Enter to close..."
  exit 0
fi

if grep -q "REPLACE_ME" .env; then
  echo "✗ .env still has placeholder values."
  echo ""
  echo "  Edit .env and replace the REPLACE_ME placeholders with your"
  echo "  real API keys, then run this installer again."
  echo ""
  open -e .env
  read -p "Press Enter to close..."
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
echo "  Your PWA login token is in the .env file (ALFE_API_TOKEN)."
echo "════════════════════════════════════════════════════════════"
echo ""

sleep 5
open "http://localhost:8099"

read -p "Press Enter to close..."
