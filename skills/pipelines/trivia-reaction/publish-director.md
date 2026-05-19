# Publish Director — Trivia Reaction Pipeline

## When To Use

The render has been frame-reviewed and the human has explicitly approved
publication. Your job is to upload to the ellie.travelcrush Drive folder
and write back to the Queue row.

**This stage NEVER auto-runs.** Per user memory: never auto-run publish
scripts. Always wait for explicit human OK in the current turn.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-reaction/<slug>/artifacts/render_report.json` | Render path + frame_review_passed=true |
| Artifact | `projects/trivia-reaction/<slug>/artifacts/brief.json` | Slug, Day |
| Render | `projects/trivia-reaction/<slug>/renders/<slug>.mp4` | The deliverable |
| Script | `scripts/trivia_reaction/publish.py` | Drive upload + Queue write-back |
| Drive | folder `1uDneOUH21xUqh4oifQTh5sqgIVk6EREg` | ellie.travelcrush |
| Sheet | TriviaReactionQueue!Queue | Status + Drive Link write-back |
| Auth | `~/.google/claude-sheets-sa.json` | Drive + Sheets RW |
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |

## Process

### 1. Verify Approval

The user must have explicitly approved publication in the current turn.
Phrases that count: "ship it", "publish", "yes go", "approved, publish".

If unclear, ask: "Want me to publish `<slug>` to Drive now?"

Do not pre-publish. Do not publish based on a stale approval from an
earlier conversation.

### 2. Run publish.py

```bash
python scripts/trivia_reaction/publish.py <slug>
```

What it does:

1. Locate `projects/trivia-reaction/<slug>/renders/<slug>.mp4`. The
   per-pipeline namespace + slug-named render means there's no risk of
   confusion with the legacy trivia-short `final_with_bg.mp4` naming.
2. Look up the Queue row by slug.
3. If Queue!J already has a Drive link → `drive.files().update` replaces
   the file content in place (link stays stable, useful for re-renders).
   Else → `drive.files().create` uploads a new file to the
   ellie.travelcrush folder.
4. Write Queue!C = `Ready to publish` and Queue!J = `<webViewLink>`.

### 3. Write publish_log.json

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-reaction",
  "metadata": {
    "sheet_revision": "...",
    "slug": "<slug>",
    "day": 2
  },
  "drive_link": "https://drive.google.com/file/d/<id>/view?usp=drivesdk",
  "sheet_updates": [
    { "range": "Queue!C<row>", "value": "Ready to publish" },
    { "range": "Queue!J<row>", "value": "https://drive.google.com/..." }
  ],
  "action": "created" | "replaced",
  "drive_file_id": "<id>",
  "uploaded_at": "<iso8601>"
}
```

### 4. Hand Off To Social

`publish.py` writes the Drive link to Queue!J. The human picks it up
from there and posts to Instagram / TikTok manually for ship-1. (Future:
auto-post via Instagram Graph API — out of scope for this pipeline
version.)

Tell the user: "Drive link in Queue row <N>, ready for you to post to
ellie.travelcrush on Instagram."

### 5. Self-Review + Checkpoint

`human_approval_default: true`. Present:

- Slug + Drive link
- Action: created vs replaced
- Queue row updates summary

After the user acknowledges, set Queue!C = `Published` in a follow-up
update (or leave the human to flip it manually once the post is live —
prefer the latter, since the pipeline doesn't actually know when the
post lands).

## What Not To Do

- Do not auto-publish. Always wait for explicit human OK.
- Do not delete the Drive file on re-render. Replace in place.
- Do not write to DailyTriviaConfig or LocalizedTextConfig.
- Do not flip Queue!C to `Published` unless the human says the post is live.
- Do not skip publish_log.json. The artifact is the audit trail.
