#!/usr/bin/env bash
# Cron-friendly TikTok → Posts_Quiz stats sync.
#
# cron runs with a minimal PATH and no shell profile, so this script sets up a
# sane environment before delegating to the SOPS wrapper. Point a crontab line
# at it; everything else (cd, PATH, age-key discovery, logging) is handled here.
#
#   crontab -e, then e.g. twice daily at 09:00 and 21:00:
#   0 9,21 * * * /ABS/PATH/OpenMontage/scripts/social_stats/cron_sync.sh dailytrivia.tc >> /ABS/PATH/OpenMontage/scripts/social_stats/out/cron.log 2>&1
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

# cron's PATH is bare — add the usual Homebrew (Intel + Apple Silicon) and
# system locations so sops/age/ffmpeg resolve.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Don't rely on HOME/OS defaults under cron: point sops at the age key directly.
if [ -z "${SOPS_AGE_KEY_FILE:-}" ]; then
  for k in \
    "$HOME/Library/Application Support/sops/age/keys.txt" \
    "$HOME/.config/sops/age/keys.txt"; do
    if [ -f "$k" ]; then export SOPS_AGE_KEY_FILE="$k"; break; fi
  done
fi

ACCOUNT="${1:-dailytrivia.tc}"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') sync start (account=$ACCOUNT) ==="
secrets/with-secrets.sh ".venv/bin/python -m scripts.social_stats.quiz_stats_sync --account $ACCOUNT --apply"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') sync done ==="
