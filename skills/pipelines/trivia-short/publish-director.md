# Publish Director - Trivia Short Pipeline

## When To Use

The render passed frame review at compose stage and the human has explicitly
approved publication. You will upload the final mp4 to the Drive
deliverable folder and update `Posts!L` + `Posts!M`. This is the only stage
that writes those two columns.

**This is the one stage in the pipeline that goes live.** Treat it accordingly.

## Hard Rules (From User Memory)

1. **NEVER auto-run `publish.py`.** The render is "done" only after a human
   has said "ship it" in the current turn. No standing authorization.
2. **NEVER assume yesterday's approval applies today.** Each row needs
   explicit approval before publish.
3. **Always check the render exists** at the expected path before invoking
   the script — `publish.py` will exit non-zero on missing renders, but you
   should catch this in announce-step rather than as a stack trace.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Artifact | `projects/<slug>/artifacts/render_report.json` | Confirms compose passed frame review |
| Artifact | `projects/<slug>/artifacts/brief.json` | Slug + row + sheet_revision |
| Render | `projects/<slug>/renders/final_with_bg.mp4` (or `final_modular.mp4`) | The deliverable |
| Script | `scripts/trivia/publish.py` | The workhorse — handles new + replace flows |
| Sheet | `Posts!L<row>` / `Posts!M<row>` | The two writes this stage performs |
| Drive | folder `1930CVitXd4d6BsZ39EleWyxmtsgaXVGY` | Deliverable drop |
| Auth | `~/.google/claude-sheets-sa.json` | Service-account creds for Sheets + Drive |

## Process

### 1. Verify Compose Passed

Read `render_report.json`:

- `metadata.frame_review_passed` must be `true`. If false, do not advance —
  the human memory rule is "always review rendered videos" and frame review
  is non-negotiable.
- `metadata.output_path` must point to an existing mp4. Stat the file.
- If `auto_fixes_applied` is non-empty, surface the list to the human in the
  approval ask — they should know what got patched between the last review
  and publication.

### 2. Wait For Explicit Approval

Before doing anything, announce the publication plan and stop:

```
PUBLISH PLAN — row <row>, slug <slug>
  source:  projects/<slug>/renders/final_with_bg.mp4
  size:    <bytes>, duration <s>
  target:  Drive folder 1930CVit…XVGY
  mode:    new upload  |  replace existing (link in M is stable)
  sheet:   write L="Ready to publish", M=<drive link>

Say "publish" / "ship it" / "go" to proceed.
```

If the human did not authorize publication in the current turn, **stop and
ask**. Do not interpret prior approvals or implicit context as authorization.

### 3. Determine New-Upload vs. Replace Mode

`publish.py` handles both transparently:

- If `Posts!M<row>` is empty → uploads a new Drive file under the folder.
- If `Posts!M<row>` has a link → extracts the file ID and calls
  `drive.files().update` to replace content in place. The link stays stable.

Report which path will run in the announcement so the human can sanity-check
(re-publish on the wrong row would overwrite a real deliverable).

### 4. Run Publish

```bash
source .venv/bin/activate
python scripts/trivia/publish.py <slug> <row>
```

Expected output:

- `✓ uploaded new Drive file: <id>` (new upload) OR `✓ replaced Drive file <id>` (re-publish)
- `✓ sheet row <row>: L=Ready to publish, M=<link> (<N> cells)`
- `  render available at: projects/<slug>/renders/final_with_bg.mp4`

If the script exits non-zero:

| Error | Likely cause | Fix |
|---|---|---|
| `render not found in projects/<slug>/renders` | compose stage didn't deliver to the expected path | re-run compose; do not publish a different file |
| Drive 403/404 on upload | service account not shared on the Drive folder | share the folder with `claude-sheets-config@travel-crush.iam.gserviceaccount.com` (editor) |
| Sheets 400 "exceeds grid limits" | row number outside data range | check the brief's row number — Posts is now 40 rows / 27 cols |
| Drive quota / network | transient | retry once, then surface to the human |

Do not retry blindly. Each retry uploads or overwrites a real file in Drive.

### 5. Verify The Writes Landed

After `publish.py` returns 0, re-read `Posts!L<row>` and `Posts!M<row>`:

- L must be `Ready to publish` (exact string — matches the dropdown)
- M must be a non-empty `https://drive.google.com/file/d/.../view` link
- The render must still be at `projects/<slug>/renders/final_with_bg.mp4`

If any check fails, the script's success report is lying — surface to the
human immediately. Do not write a "success" `publish_log` in that case.

### 6. Write The Publish Log

Write `projects/<slug>/artifacts/publish_log.json` per `schemas/artifacts/publish_log.schema.json`.

Trivia-specific shape:

```jsonc
{
  "metadata": {
    "sheet_revision": "a1b2c3d4e5f6",
    "row": 5,
    "slug": "australia-wider-than-moon",
    "render_path": "projects/australia-wider-than-moon/renders/final_with_bg.mp4",
    "render_bytes": 4923847,
    "drive_link": "https://drive.google.com/file/d/.../view",
    "drive_mode": "new_upload",       // or "replace_existing"
    "drive_file_id": "1a2b3c...",
    "sheet_updates": {
      "L": "Ready to publish",
      "M": "https://drive.google.com/file/d/.../view"
    },
    "human_approval": {
      "in_turn": true,
      "phrase_seen": "ship it"      // the exact phrase the human used
    }
  }
}
```

### 7. Checkpoint And Hand Off

`human_approval_default: true` per the manifest — the approval gate fired at
step 2. At this point you are checkpointing the COMPLETED stage, not asking
for a second approval.

Post-publish, tell the human:

- The Drive link (clickable)
- That L is now `Ready to publish` (so the sheet reflects state)
- The local render path (`projects/<slug>/renders/final_with_bg.mp4`) for manual TikTok upload
- Reminder: after they post to TikTok, they flip L to `Published` manually

Do NOT post to TikTok. That's a separate human step. The pipeline ends here.

## Feedback Loop (Web App + Project Artifacts)

After publish, the row enters review. The reviewer opens the launcher
(`uvicorn web.server:app --port 8765 --reload`, then http://127.0.0.1:8765/trivia/),
watches the final mp4 from the Drive link,
types feedback in the per-row panel, and clicks **Save** or **Save & Re-render**.

That endpoint writes `projects/<slug>/artifacts/feedback.json`. The Save &
Re-render button additionally enqueues a render job which re-reads project
artifacts (`feedback.json`, `assembly_warnings.json`, `text_overrides.json`)
and re-runs assemble → transcribe → Remotion → publish. The Drive link in M
stays stable across re-publishes.

When asked to "process feedback for <slug>" (from the conversation, not from
the web UI button):

1. Read `projects/<slug>/artifacts/feedback.json`.
2. Classify it (see `executive-producer.md` Feedback Re-entry Decision Tree).
3. Re-enter the appropriate stage. Most common:
   - Caption-only fix → re-enter compose, patch words.json, re-render, re-publish (replace mode).
   - Clip change → re-enter assets.
   - VO/style change → re-enter idea (sheet edit invalidates downstream).
4. After the fix, update or clear `feedback.json` so the UI doesn't keep
   surfacing a stale complaint.

## What Not To Do

- Do not auto-run publish. The "never auto-run" rule is from durable user
  memory — it's not negotiable per-conversation.
- Do not publish a render that hasn't passed frame review at compose. The
  `render_report.frame_review_passed` flag is a hard gate.
- Do not write to columns other than L and M. Even on error.
- Do not retry uploads in a loop. Each retry is a real file operation.
- Do not edit the sheet's data validation (L's dropdown) from this stage.
  Dropdown maintenance is a one-off operation done outside the pipeline.
- Do not post to TikTok or any social platform. The pipeline ends at "ready
  to publish."
- Don't try to write a "Final Feedback" column on the sheet — there is
  no such column anymore. Reviewer feedback lives at
  `projects/<slug>/artifacts/feedback.json`, written by the web app.
