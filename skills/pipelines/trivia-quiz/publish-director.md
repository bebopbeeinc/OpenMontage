# Publish Director — Trivia Quiz Pipeline

## When To Use

After `compose` produces `final_quiz.mp4` AND the human has explicitly
approved publication in the current turn.

Per user memory (`feedback_trivia_local_approval`): **never auto-publish**.
The human must say "publish" / "ship it" / "approved" in the current turn.

## Process

### 1. Verify Approval

Check that the user's most recent message contains an explicit approval
phrase. If not, halt and ask. Don't infer approval from earlier turns.

### 2. Upload to Drive

Reuse `scripts/trivia/publish.py` upload logic. Target the Travel Crush
Drive folder. The service account
`claude-sheets-config@travel-crush.iam.gserviceaccount.com` already has
editor access.

### 3. Render Caption + Pinned Comment

From `brief.json.metadata.quiz.captions`, substitute:
- `{smart_link}` → `styles/trivia-quiz.yaml::show_identity.game_lockup.placeholder_url`
  (in v0.1; v0.2 will mint per-row URLs)
- `{score_cta}` → `styles/trivia-quiz.yaml::show_identity.score_cta`

Print the final TikTok caption and the final Pinned Comment template
verbatim so the human can copy-paste at posting time. We don't auto-post
to TikTok/IG — those remain manual until the platform APIs are wired.

### 4. Write `publish_log.json`

```json
{
  "metadata": {
    "drive_link": "https://drive.google.com/file/d/.../view",
    "captions": {
      "tiktok": "...",
      "instagram": "...",
      "pinned_comment": "..."
    },
    "smart_link_v0_1": "play.travelcrush.com",
    "published_at": "2026-05-20T12:34:56Z"
  }
}
```

## Output

`projects/trivia-quiz/<slug>/artifacts/publish_log.json` plus a Drive upload.

## v0.2 (deferred)

- Per-row tracked smart link (e.g. `play.travelcrush.com/t/<slug>?utm_*`)
- Auto-write back to a `Posts_Quiz` Google Sheet row (Final Status + Drive
  link), mirroring trivia-short's publish-director sheet-write logic
