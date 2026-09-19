#!/usr/bin/env bash
# Setup Delta API keys via environment — DO NOT put secrets in git/code.
#
# Usage:
#   source scripts/setup_delta_keys.sh
#   # then paste key/secret when prompted
#
# Or export manually:
#   export DELTA_API_KEY="your_key"
#   export DELTA_API_SECRET="your_secret"

set -e
echo "=== TradAI Finder — Delta API key setup ==="
echo "WARNING: Never share API secrets in chat/screenshots."
echo "If you already leaked a key, REVOKE it on Delta and create a new one."
echo ""

if [ -n "$DELTA_API_KEY" ] && [ -n "$DELTA_API_SECRET" ]; then
  echo "Keys already set in environment."
  echo "DELTA_API_KEY length: ${#DELTA_API_KEY}"
  exit 0
fi

read -r -p "DELTA_API_KEY: " KEY
read -r -s -p "DELTA_API_SECRET: " SECRET
echo ""
export DELTA_API_KEY="$KEY"
export DELTA_API_SECRET="$SECRET"
echo "Exported for this shell session only."
echo "To persist, add to ~/.bashrc or a local .env (gitignored)."
