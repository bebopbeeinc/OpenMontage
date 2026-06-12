# Trivia Captain 2T1L — Publish Director

**Stage:** `publish` → Drive upload + Queue write-back. Human-approval default:
**true**. **NEVER auto-runs** (hard user rule).

## Prerequisite (one-time)
The service account cannot create Drive files. A human must create a renders
folder, share it (Editor) with `claude-sheets-config@travel-crush.iam.gserviceaccount.com`,
and paste its ID into `scripts/trivia_captain_2t1l/publish.py` → `DRIVE_FOLDER_ID`.
publish.py refuses to run while the placeholder is unset.

## Command (only after explicit approval)
```
python scripts/trivia_captain_2t1l/publish.py <slug>
```
- Uploads `renders/<slug>.mp4` (and the raw `clip.mp4` as a secondary deliverable)
  to the Drive folder; replaces in place if a link already exists (stable links).
- Writes Queue!P (Drive Link), Queue!Q (Drive Clip), and status → **Ready to publish**.

## Hard rules
- Wait for an explicit approval phrase ("publish", "ship it", "yes go") in the
  current turn. The render must have passed frame review first.
- Do **not** flip to **Published** here — that's the separate "Mark as Published"
  button/step, the human's signal the post is actually live.
- Do not delete Drive files on re-publish — replace in place.

The human posts to Instagram/TikTok manually (no auto-posting in scope).
