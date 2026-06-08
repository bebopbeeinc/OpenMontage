# Publish Director — Trivia Captain Pipeline

## When To Use

The render has been frame-reviewed and the human has explicitly approved
publication. Your job is to upload to the @captain.archibald Drive folder
and write back to the Queue row.

**This stage NEVER auto-runs.** Per user memory: never auto-run publish
scripts. Always wait for explicit human OK in the current turn.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain/<slug>/artifacts/render_report.json` | Render path + frame_review_passed=true |
| Artifact | `projects/trivia-captain/<slug>/artifacts/brief.json` | Slug, Day |
| Render | `projects/trivia-captain/<slug>/renders/<slug>.mp4` | The deliverable |
| Script | `scripts/trivia_captain/publish.py` | Drive upload + Queue write-back |
| Drive | folder `1sGmMTm0pI8rXCxi8sZjgXyQ2d2PJrl-h` | @captain.archibald |
| Sheet | TriviaCaptainQueue!Queue | Status + Drive Link write-back |
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
python scripts/trivia_captain/publish.py <slug>
```

What it does:

1. Locate both deliverables:
   - `projects/trivia-captain/<slug>/renders/<slug>.mp4` — captioned
     final render (the posted version)
   - `scripts/trivia_captain/library/clips/<slug>.mp4` — raw Seedance
     avatar clip (no captions; secondary reference)
2. Look up the Queue row by slug.
3. For each deliverable: if Queue!I (render) / Queue!L (clip) already
   has a Drive link → `drive.files().update` replaces the file content
   in place (link stays stable, useful for re-renders). Else →
   `drive.files().create` uploads a new file to the @captain.archibald
   folder using `<slug>.mp4` for the render or `<slug>_clip.mp4` for
   the raw clip.
4. Write Queue!C = `Ready to publish`, Queue!I = render webViewLink,
   Queue!L = clip webViewLink.

### 3. Write publish_log.json

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-captain",
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
@captain.archibald on Instagram."

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
