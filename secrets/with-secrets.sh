#!/usr/bin/env bash
# Run a command with the team's shared secrets decrypted into its environment.
#
# sops decrypts secrets/secrets.sops.env and injects the values as env vars
# into the child process ONLY — nothing is written to disk in plaintext.
#
# Example:
#   secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.tiktok_api fetch --account dailytrivia
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENC="$REPO/secrets/secrets.sops.env"

if ! command -v sops >/dev/null 2>&1; then
  echo "✗ sops not installed. Run:  brew install sops age" >&2
  exit 1
fi
if [ ! -f "$ENC" ]; then
  echo "✗ $ENC not found — the encrypted secrets file doesn't exist yet." >&2
  echo "  See secrets/README.md (Bootstrap section) to create it." >&2
  exit 1
fi
if [ "$#" -eq 0 ]; then
  echo "usage: secrets/with-secrets.sh <command...>" >&2
  echo "e.g.:  secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.tiktok_api fetch --account dailytrivia" >&2
  exit 2
fi

# Locate the age key if it isn't where sops looks by default. sops uses the
# OS user-config dir (macOS: ~/Library/Application Support/sops/age; Linux:
# ~/.config/sops/age), so a key left in the "other" location would be missed.
if [ -z "${SOPS_AGE_KEY_FILE:-}" ]; then
  for k in \
    "$HOME/Library/Application Support/sops/age/keys.txt" \
    "$HOME/.config/sops/age/keys.txt"; do
    if [ -f "$k" ]; then export SOPS_AGE_KEY_FILE="$k"; break; fi
  done
fi

# sops exec-env takes the command as a single string.
exec sops exec-env "$ENC" "$*"
