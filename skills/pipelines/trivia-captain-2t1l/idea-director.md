# Trivia Captain 2T1L — Idea Director

**Stage:** `idea` → produces `brief` + a Draft Queue row. Human-approval default: **true**.

Content is curated by hand (no DailyTriviaConfig). Your job: author one strong
2-truths-1-lie set for a destination and queue it.

## Command
```
python scripts/trivia_captain_2t1l/add_row.py \
  --slug <kebab> --place "<Destination>" \
  --claim1 "<fact>" --claim2 "<fact>" --claim3 "<fact>" \
  --lie <1|2|3> --lie-model <myth|invented> \
  --demographic "<group>" [--label1 .. --label3 ..]
```
Writes `projects/trivia-captain-2t1l/<slug>/artifacts/brief.json` and appends a
Draft row to Posts_2T1L. (Or author the row directly in the sheet / via the web
"Add" form.)

## Quality bar
- **Exactly 3 claims: 2 true, 1 lie.** The two TRUE claims should sound *fake*
  (jaw-droppers); the LIE should sound *plausible*. That inversion is the hook.
- **lie_model:** `myth` = a widely-believed travel myth the Captain "fell for
  too"; `invented` = one he made up. Pick whatever the facts support (mix per row).
- **Place** is a real destination with vivid, surprising facts.
- **Demographic taunt** chosen and **rotated** (no two consecutive rows taunt the
  same group); warm needling, never hostile (stays inside the never-angry register).
- **Lie # is tracking only** — confirm it never leaks into labels/caption later.

## Checkpoint
Present the 3 claims (mark which is the lie, for the human only) + place +
demographic. Get approval before moving to script. See [[trivia-captain-2t1l]]
and `styles/trivia-captain-2t1l.yaml`.
