#!/bin/bash
# Bundles the gift/ folder into a clean ZIP for Google Drive / USB / email.
# Strips anything recipient-specific: .env, .git, secrets, macOS junk.
# Outputs:  ../alf-e-gift-v<DATE>.zip
#
# Usage:    ./make-zip.sh

set -e
cd "$(dirname "$0")"

STAMP=$(date +%Y-%m-%d)
OUT="../alf-e-gift-$STAMP.zip"

# Safety check — refuse if a real .env exists
if [ -f .env ]; then
  echo "✗ A real .env exists in gift/ — refusing to bundle."
  echo "  Delete it first:  rm gift/.env"
  exit 1
fi

# Safety check — refuse if README / TOML / env.example mention anything personal
if grep -riE "scholz|brotherhood|nelec|apple_blossom|192\.168|r7hge7|majorfrazer" . \
     --exclude-dir=.git --exclude=make-zip.sh --exclude=docker-compose.yml >/dev/null 2>&1; then
  echo "✗ Personal reference found in gift/ — refusing to bundle."
  echo "  Run this to see what's leaking:"
  echo "    grep -riE 'scholz|brotherhood|nelec|apple_blossom|192\\.168|r7hge7|majorfrazer' gift/"
  exit 1
fi

echo "Bundling gift/ → $OUT"

# Remove any prior ZIP so we don't ship stale
rm -f "$OUT"

# Zip everything in the current folder, excluding junk
zip -r "$OUT" . \
  -x "*.DS_Store" \
  -x "__MACOSX*" \
  -x ".git/*" \
  -x ".git" \
  -x "*.log" \
  -x "make-zip.sh" \
  -x ".env"

echo ""
echo "✓ Created $(cd .. && pwd)/$(basename $OUT)"
echo "  Size: $(du -h "$OUT" | cut -f1)"
echo ""
echo "Next:"
echo "  1. Drag the ZIP into Google Drive"
echo "  2. Right-click → Share → 'Anyone with the link can view'"
echo "  3. Copy the share link and send to recipient"
