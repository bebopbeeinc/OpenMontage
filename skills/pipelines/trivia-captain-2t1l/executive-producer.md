# Trivia Captain 2T1L — Executive Producer

Orchestration control for the `trivia-captain-2t1l` pipeline ("Captain's Two
Truths & a Lie"). Not a stage — it governs the flow, the sheet contract, and
feedback re-entry. See `styles/trivia-captain-2t1l.yaml` for the locked format
(single 15s Seedance clip + the kinetic `TriviaTwoTruthsK3` overlay).

## When to use
Any 2T1L production request: "make a 2-truths-1-lie short", "Captain 2T1L for
<place>", or resuming a row on the Posts_2T1L tab.

## Sheet contract (the source of truth)
The **Posts_2T1L tab** of the @dailytrivia.tc Post Calendar
(`1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg`) is the SoT. Content is **curated
here** — there is no DailyTriviaConfig dependency. Columns A–R:
`# · Slug · Status · Place · Claim 1 · Claim 2 · Claim 3 · Lie # · Lie Model ·
Label 1 · Label 2 · Label 3 · Demographic · OpenArt Prompt · Caption · Drive
Link · Drive Clip · Theme`. Helpers: `scripts/trivia_captain_2t1l/queue_row.py`.

**Lie # is tracking only — it is NEVER rendered or leaked in the caption.**

## Operating loop
`idea → script → assets → edit → compose → publish`

| Stage | Director | Command | Status after |
|---|---|---|---|
| idea | idea-director | `add_row.py` | Draft |
| script | script-director | `build_prompt.py` | Ready to review |
| assets | asset-director | `openart_generate.py` | (unchanged) |
| edit | edit-director | `assemble.py` | (unchanged) |
| compose | compose-director | `render.py` + frame review | Ready to publish |
| publish | publish-director | `publish.py` | (human flips → Published) |

The web UI (`/trivia-captain-2t1l/`) runs script→compose as one **Generate** job;
**Publish** and **Mark as Published** are deliberate, separate buttons.

## Status enum
`Draft → Ready to review → Ready to publish → Published` (`queue_row.STATUS_*`).

## Decision communication
Before any paid Seedance generation, announce the tool/model (OpenArt · Seedance
2.0), that it's a single 15s clip, and the cost. Surface blockers explicitly.

## Hard rules
- **Never auto-publish** — Publish is a human-triggered step.
- **Always review the render** (frame extract + verify banner sync) before
  signalling done; auto-fix defects in the same turn.
- Character is locked to **"Captain Archibald"** — never described from text.
- Brand "Travel Crush" is **not spoken** — it lives in the Remotion banner.

## Budget
~$0.30/episode (one Seedance clip + one Remotion render; overlay assets reused).
Max 3 revisions/stage, 20 min wall time.
