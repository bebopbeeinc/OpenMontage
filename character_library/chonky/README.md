# Chonky

The mascot of "Where in the World Is Chonky?" — a very round orange tabby who
hides in geography-puzzle photographs.

Every image in this directory is attached to the render as a visual reference:
`stills()` in `scripts/common/openart_characters.py` globs the folder and
`visual_references()` attaches all of them. Adding or removing a file changes
what OpenArt sees, with no code change anywhere.

`01-model-sheet.png` is the authored five-pose model sheet and the identity
reference: body shape, proportions, markings and colour come from it and are
not negotiable. A normal-bodied ginger cat is a failed render, exactly as a
wrongly-sized one is.

`02-standing-wave.png` … `06-sitting.png` are the five figures of that sheet,
each cropped tight to one cat. They are not new artwork — they are the same
poses, carrying more Chonky and less backdrop. In the full sheet any one figure
is about **8-12% of the pixels**; in its own crop it is about **83%**. Renders
kept coming back as ordinary ginger tabbies while the only reference attached
was a 1672x941 canvas that was mostly empty studio background, so most of what
the model received carried no information about him at all.

Each crop was checked to contain exactly one complete cat — tail, paw pads and
whiskers intact, no limb belonging to the cat beside it.

Two things these are **not** a reference for:

* **Pose.** Four of the five figures stand upright on hind legs and the fifth
  sits upright. That is model sheet presentation, not instruction. Chonky is
  always on four paws in an ordinary cat posture.

  **There is no four-paws reference, and cropping cannot create one.** Every
  render is therefore asking the model to take the body from these images while
  ignoring the posture in all of them. Until a quadruped pose is authored and
  dropped in here, the pose rule is fighting the reference instead of being
  supported by it — and that is the most likely remaining cause of a render
  that comes back standing, or that abandons the character entirely.

* **Rendering style.** It is a studio render on a plain background. His fur,
  lighting and contact shadow must come from the scene he is placed in.
  Copying the sheet's studio look is the main reason a render ends up feeling
  AI-generated.

Uploads are cached per workspace in `character_library/.uploads.json`, keyed by
file path — new files upload on first use, and an edited file needs its cache
entry cleared to re-upload.
