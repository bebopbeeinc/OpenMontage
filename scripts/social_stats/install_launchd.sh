#!/usr/bin/env bash
# Install a launchd LaunchAgent that runs the TikTok stats sync twice a day
# (09:00 and 21:00). Run this ON THE SERVER (the Mac where the sync should run).
#
# Why a LaunchAgent (not a Daemon): the sync needs the logged-in user's files —
# the age key, the TikTok token in .secrets/, and the Google service account —
# all of which live in the user's home. Why launchd (not cron): if the box is
# asleep at 09:00/21:00, launchd runs the job on the next wake; cron just skips.
#
# Each account gets its own LaunchAgent (label + log are derived from the
# account slug), so run it once per account to schedule several side by side:
#   scripts/social_stats/install_launchd.sh dailytrivia.tc
#   scripts/social_stats/install_launchd.sh ellie.travelcrush
#
# Usage (on the server):
#   scripts/social_stats/install_launchd.sh [account]      # default: dailytrivia.tc
#   DRY_RUN=1 scripts/social_stats/install_launchd.sh      # print the plist, don't install
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCOUNT="${1:-dailytrivia.tc}"
# Per-account label + log so multiple accounts each get their own LaunchAgent
# instead of the second `install` overwriting the first. Slug the account
# (dots/other → dash) to form a valid label segment, e.g. dailytrivia.tc →
# com.bebopbee.openmontage.stats-sync.dailytrivia-tc.
SLUG="$(printf '%s' "$ACCOUNT" | tr -c 'A-Za-z0-9' '-' | sed -E 's/-+/-/g; s/^-|-$//g')"
LABEL="com.bebopbee.openmontage.stats-sync.$SLUG"
RUNNER="$REPO/scripts/social_stats/cron_sync.sh"
LOG="$REPO/scripts/social_stats/out/launchd-$SLUG.log"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$REPO/scripts/social_stats/out"

DEST="$PLIST"
if [ "${DRY_RUN:-}" = "1" ]; then DEST="$(mktemp)"; else mkdir -p "$HOME/Library/LaunchAgents"; fi

cat > "$DEST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$RUNNER</string>
    <string>$ACCOUNT</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

if [ "${DRY_RUN:-}" = "1" ]; then
  echo "[dry-run] would write $PLIST and load it. Generated plist:"
  echo "------------------------------------------------------------"
  cat "$DEST"; rm -f "$DEST"
  exit 0
fi

# (re)load
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "✓ installed + loaded: $PLIST"
echo "  schedule : 09:00 and 21:00 daily   (account=$ACCOUNT)"
echo "  logs     : $LOG"
echo "  test now : launchctl start $LABEL && tail -f \"$LOG\""
echo "  status   : launchctl list | grep stats-sync"
echo "  remove   : launchctl unload \"$PLIST\" && rm \"$PLIST\""
