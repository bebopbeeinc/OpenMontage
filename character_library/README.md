# Character library

How a character's identity gets into an OpenArt generation.

OpenArt's saved characters ("Captain Archibald", "ellie.travelcrush") live in
the OpenArt web UI, and its MCP API has no tool that resolves one by name —
even the `element` reference form requires the image URL. So `character=` is
addressed from this directory: the drivers turn it into `visualReferences` and
pass those to the model.

One directory per character, named by slug — lowercase, non-alphanumerics
collapsed to `-`. `"Captain Archibald"` → `captain-archibald`,
`"ellie.travelcrush"` → `ellie-travelcrush`.

## Seeding it: the exporter

OpenArt's web app has an authenticated `/suite/api/character/list` endpoint
that the MCP API doesn't expose. `openart_character_export.py` borrows the
Playwright session the old drivers left behind to read it, and writes the
result here:

```bash
python scripts/common/openart_character_export.py            # list what exists
python scripts/common/openart_character_export.py --write    # download all
python scripts/common/openart_character_export.py --write --only "Captain Archibald"
```

This is the only remaining OpenArt use of Playwright, and it runs only when you
ask. If the saved session has expired it says so; log in again with
`python scripts/common/openart_driver_playwright.py --probe`.

Note that what comes back depends on the *browser* session's active workspace,
which is separate from the MCP one — if a character you expect is missing,
switch workspace in the OpenArt UI and re-run.

Alongside the stills it writes `character.json` with OpenArt's own
`characterDescription` — the character's look in words, useful both as
provenance and as prompt material.

## Option 1 — stills (recommended)

What the exporter writes by default:

```
character_library/
  captain-archibald/
    01-captain-archibald.png
    character.json
```

Files are used in filename order, so prefix them — models weight earlier
references more heavily.

Reference images are often full *scenes* rather than clean character sheets
(Captain Archibald's is an airport gate). That has proven fine — a prompt that
names the new location carries the identity without dragging the old
background along — but it's worth a look when adding a character.

## Option 2 — pointers

Name media that is already in OpenArt, and nothing is uploaded:

```
character_library/
  ellie-travelcrush/
    refs.json
```

```json
[
  "https://cdn.openart.ai/openart-uploads/.../ellie-face.png",
  {"url": "https://cdn.openart.ai/.../ellie-outfit.png", "label": "outfit"}
]
```

Entries may be bare URL strings, `{"url", "label"}` objects, or complete
visualReference objects (`type`/`id`/`url`/`label`), which are passed through
untouched with no API call at all. Anything else is resolved once via
`openart_upload_metadata_get` and cached.

## Which to use

`refs.json` wins when a character has both.

Pointers keep binaries out of the repo — the whole directory is ~24KB instead
of ~5.4MB — and are the right pick when the art is large and changes rarely.
Two things to know about them:

- **They are workspace-bound.** An OpenArt asset belongs to the workspace it
  was uploaded into, so a pointer resolves only while generating *there*.
  Captain Archibald and ellie.travelcrush both live in **R N**, so jobs using
  them must run in R N. Generating elsewhere fails with an error naming the
  owning workspace. Stills have no such coupling: they upload into whichever
  workspace runs the job. The owner is recorded in each `character.json`.
- **They can go stale silently.** Edit the character in OpenArt and the URL may
  keep serving the old image — generations drift with no error. Stills can't
  drift, because what you exported is what gets used.

Re-exporting without `--pointers` converts a character back to stills at any
time (and vice versa); each mode clears the other's files.

## Checking it

```bash
python scripts/common/openart_characters.py
```

Prints each resolvable character and which source it uses. A character with
neither fails loudly at generation time with the same information.

`.uploads.json` caches the resolved reference per workspace so the same assets
aren't re-uploaded or re-resolved on every row. It's a cache, not source —
gitignored, and safe to delete.
