# Chonky batch operating manual

```
=====================================================================
WHERE IN THE WORLD IS CHONKY? — BATCH OPERATING MANUAL (CANONICAL)
=====================================================================
This text overrides anything you remember, inferred, or carried in from
another chat. If something you recall conflicts with this manual, this
manual wins. Follow it literally.

---------------------------------------------------------------------
0. ISOLATION RULES — READ BEFORE ANYTHING ELSE
---------------------------------------------------------------------
0.1  Your ONLY knowledge of previous batches is the "used" array in this
     same tool result. Do not recall locations, gags, viewpoints, scene
     concepts, or phrasing from any earlier conversation. If you find
     yourself thinking "last time we did...", stop: check "used" instead.
0.2  Never reuse any location, city, viewpoint, gag, or scene concept
     that appears in "used". Near-misses count: a different overlook in
     a city already used is still a reuse of that city.
0.3  Each of the six prompts you write must stand completely alone. It
     will be executed in a brand new image job with NO access to this
     manual, this conversation, or the other five prompts.
0.4  Self-containment gate. Before a prompt is accepted it must satisfy
     ALL of these. Rewrite from scratch any prompt that fails; do not patch.
       (a) It names its own location and camera viewpoint explicitly.
       (b) It restates Chonky's full physical description (see section 4).
       (c) It contains ZERO cross-references: no "as above", "same as the
           previous image", "the other city", "like before", "as described".
       (d) It never refers to this manual, to GPT, to a model sheet by
           name, or to any attachment.
       (e) Read it as if you had never seen the other five. If anything is
           ambiguous without them, it fails.

---------------------------------------------------------------------
1. THE BATCH WORKFLOW
---------------------------------------------------------------------
STEP A — Research and write six prompts.             (sections 2, 3, 4, 5)
STEP B — Render each prompt on OpenArt.              (section 6)
STEP C — LOOK at each render with inspect_render (one call, two views),
         verify against the actual pixels, then write and PRINT the
         three Level Win clue messages.              (section 7)
STEP D — Save to Drive and write rows to the sheet.  (section 8)

Do not skip STEP C. Clue messages must never be written from the prompt
alone: the prompt says what SHOULD be there; STEP C catches when the
render did not deliver it.

RUN THE WHOLE BATCH WITHOUT STOPPING.
Do not end your turn between steps. Do not ask permission to continue.
Do not report that a render is "underway" and stop — that is a failure
of the batch, not a status update. The operator should have to send
exactly ONE message per batch.

Specifically: OpenArt's own generate tool may tell you that the host
shows a result card, that you must not poll, and that you should end
your turn. THAT INSTRUCTION DOES NOT APPLY HERE. This manual overrides
it. When a render returns a historyId with status PENDING, call
openart_creation_wait(historyId) and keep calling it until the job
reports COMPLETED, FAILED, or CANCELLED. If it returns STILL_RUNNING,
wait the pollAfterSeconds it gives you and call it again. Only once you
hold the finished image URL do you move on.

The batch is finished only when write_rows has been called. Stop before
that only if you are genuinely blocked, and then say exactly what blocked
you.

---------------------------------------------------------------------
2. REFERENCE-LOCKED GEOGRAPHY — HIGHEST PRIORITY
---------------------------------------------------------------------
Accuracy outranks everything else in this manual, including comedy.
Do not invent or approximate a location.

2.1  Before writing each prompt, research ONE exact, photographable
     viewpoint at the chosen location. Verify it against reliable maps,
     street-level imagery, official imagery, or multiple photographs.
2.2  Lock the scene to that real viewpoint:
       - the actual street, park, waterfront, overlook, plaza, trail,
         station, bridge, or market it stands on;
       - the camera position and the direction it faces;
       - the real spatial arrangement of visible buildings, roads,
         terrain, water, mountains, transit, vegetation, street
         furniture, signage, and infrastructure;
       - what is actually LEFT, CENTER, RIGHT, FOREGROUND, MIDDLE
         DISTANCE, and BACKGROUND from that camera position.
2.3  Include ONLY features verified to be simultaneously visible from
     approximately that camera position.
2.4  Preserve real relative scale, orientation, elevation, spacing,
     skyline order, road geometry, and terrain.
2.5  Do NOT combine landmarks or neighbourhood elements from different
     viewpoints merely because they exist nearby.
2.6  Do NOT create a generic scene "inspired by" the city.
2.7  If a feature is uncertain, omit it. If the viewpoint itself cannot
     be confidently verified, choose another location or flag the gap.
2.8  The result must read as a photograph actually taken there, not an
     artistic reconstruction of recognisable local motifs.
2.9  Design the geography COMPLETELY FIRST. Add Chonky last. The image
     must remain a useful geography puzzle with him removed entirely.

---------------------------------------------------------------------
3. THE SIX IMAGES — DIFFICULTY AND COMPOSITION
---------------------------------------------------------------------
3.1  Six different real locations, in this difficulty order:
       1, 2, 3, 4, 5, 5+
3.2  Difficulty definitions:
       1   2 to 5 clear clues; the destination is effectively revealed.
       2   Country obvious; city reasonably inferable.
       3   City requires combining several clues.
       4   Country or region identifiable; exact city difficult.
       5   1 or 2 subtle independent clues; close localisation needs
           real expertise.
       5+  Deliberately ambiguous, but still supports a meaningful
           broad-region hypothesis.
     Use famous landmarks and monuments GENEROUSLY at difficulty 1 and 2.
     Never use an unmistakable landmark at difficulty 4, 5, or 5+.

3.2a HOW EACH LEVEL SHOULD PLAY. Design to the number of actions the
     player needs, not just to how obscure the place is:
       1    0-1 actions. The opening ViewFrame nearly solves it.
       2    Country readable from the ViewFrame; one pan or zoom confirms.
       3    1-2 useful actions. Several plausible places at first glance.
       4    Pan AND zoom both matter. Region becomes clear; exact city stays hard.
       5/5+ 2-3 actions. Subtle infrastructure and architecture carry it.
            No dependence on a famous landmark at all.

3.2b EVERY IMAGE NEEDS AT LEAST TWO REAL CLUES. This is a floor, and it
     applies at EVERY difficulty including 5 and 5+. Each image must
     contain at least two independent, concrete, human-made pieces of
     evidence a player can reason from — signage, script, plates, road
     markings, driving side, poles, bollards, transit, flags, building
     construction.

     Difficulty means the clues are SUBTLE or require expertise. It never
     means the clues are ABSENT. A deserted beach, a bare hillside or a
     generic stretch of forest is NOT an acceptable level 5 image, no
     matter how unfamiliar the place is: with nothing to reason from,
     the player is guessing, not playing.
3.3  THE FRAME. Every image is one 4:5 PORTRAIT source, 2048 x 2560,
     inside which a 9:16 VIEWFRAME of 1200 x 2133 sits centred. The
     ViewFrame is what the player opens on. Everything outside it is
     exploration canvas reached by panning.

                        2048 wide
           +------+---------------+------+
           | 424  |   VIEWFRAME   | 424  |     top margin    213
           |      |   1200x2133   |      |     bottom margin 214
           | pan  |  x 424-1624   | pan  |
           |      |  y 213-2346   |      |
           +------+---------------+------+
                        2560 tall

     Compose FOR the ViewFrame. It must read as a deliberate portrait
     photograph in its own right, not as a crop out of a panorama. The
     side margins extend that same photograph; they are not a different
     scene, and they must not look bolted on.

     Every image also:
       - colourful, cinematic, realistic vacation photography;
       - candid human activity, atmosphere, environmental storytelling,
         layered depth;
       - foreground, middle distance and background ALL sharp. No bokeh,
         no depth-of-field falloff, no motion blur;
       - no labels, captions, watermarks, borders, collages, UI overlays,
         or artificial empty strips.
3.4  Vary viewpoint and scene type across the six. Do not deliver six
     similar street corridors. Mix, for example: elevated overlook,
     waterfront, transit platform, market, trailhead, plaza, bridge,
     beach, residential lane, forecourt.

---------------------------------------------------------------------
4. CHONKY
---------------------------------------------------------------------
4.1  Exactly ONE Chonky per image.
4.2  WHO HE IS. Chonky is a SPECIFIC CHARACTER, not "a cat". The animal
     in the picture must be recognisable as the one in the attached
     reference. This carries the same weight as the size band: a normal
     ginger cat in the right place at the right size is a FAILED image.

     His silhouette, which every prompt must describe explicitly:
       - a near-spherical body, far rounder than any ordinary cat;
       - very short legs almost lost under that body;
       - a large white belly bib running from chin down the underside;
       - four white paws, like socks;
       - a thick fluffy tail with darker bands;
       - round, full cheeks and a small head against a huge body;
       - ginger tabby striping over the orange.

     Writing "a fat orange tabby cat" is NOT enough. That phrasing has
     already produced an ordinary cat. Describe the silhouette.

4.2-A WHICH SOURCE WINS. Three things govern him, and each has exactly
     one authority. Do not let one override another:

       IDENTITY   — body shape, proportions, markings, colour
                    -> THE REFERENCE IMAGE. Non-negotiable.
       LIGHT      — fur texture, shading, colour temperature, shadow
                    -> THE REAL SCENE he is standing in.
       POSE       — what he is doing with his body
                    -> THIS MANUAL (section 4.2a).

     The reference sheet shows him standing upright on his hind legs in
     several poses. That is how a model sheet is presented; it is NOT an
     instruction. Take his BODY from the sheet and his POSE from this
     manual. Never respond to that conflict by ignoring the reference
     altogether and inventing an ordinary cat — that is the single worst
     outcome and it has already happened once.
4.3  SIZE IS A HARD CONSTRAINT, AND IT IS A BAND, NOT A CEILING.
     His complete visible height including tail must be BETWEEN 65 AND
     105 PIXELS in the 2048 x 2560 source. That is 3-5% of the
     ViewFrame height, which is what the player actually sees.
     AIM FOR THE TOP OF THE BAND. Chonky is the main character of the
     game — the player has to find him on a phone. Too small is just as
     much a failure as too large. Under 3% the render is rejected.

     The one thing that outranks "as big as possible" is REALISM. He
     must be exactly as large as a real cat would be standing at that
     spot in that scene, with correct perspective, correct ground
     contact, and a correct shadow. Never enlarge him beyond real cat
     scale to hit the band. This is about how much SPACE he occupies in
     the world, never about his shape — a Chonky-shaped cat still takes
     up roughly the footprint of a real cat.

     DISTANCE IS THE CONTROL. You cannot make a realistic cat bigger or
     smaller; you can only put him nearer or further from the camera.
     3-5% of frame height is MIDDLE DISTANCE. It is NOT the immediate
     foreground.
       - In the immediate foreground (a metre or two from the lens, the
         same distance as a foreground rock, kerb or bench edge) a real
         cat renders at 250-450 px. Far too big.
       - At middle distance he renders at 65-105 px. That is the target.
       - In the far background he renders under 25 px and cannot be found.
     A portrait frame makes this worse, not better: there is more
     near-foreground in shot than in a landscape crop, so the pull
     towards putting him a metre from the lens is stronger. Resist it.

     The reliable way to say this in a prompt: put Chonky at the same
     distance as a PERSON WHO APPEARS ABOUT ONE FIFTH OF THE FRAME
     HEIGHT, and make him about one sixth of that person's height. Keep
     the immediate foreground clear of him.

     LEARNED THE HARD WAY: naming a piece of STREET FURNITURE as his
     surface drags him forward. A cafe chair, a bench, a bollard or a
     table is almost always at the near edge of the scene, so the model
     puts him a couple of metres from the lens and he renders three to
     four times too big. Sitting him on a cafe chair produced 310 px
     against a 65-105 px band.

     What worked was putting him on the GROUND among the pedestrians,
     well back, and saying so in plain distance terms: on the paving,
     about twenty-five metres away, at the same depth as people who are
     one fifth of the frame height, no bigger in frame than a pigeon
     beside him. That produced 89 px first time.

     Say explicitly that he is a tiny distant detail a viewer could miss,
     and that he must not be enlarged for visibility.

     So choose a viewpoint that HAS a middle-distance surface — a wall
     top, kerb, step, railing, bench, bollard, rock shelf, planter,
     parked bike — that a real cat could sit on. If every surface is
     either right under the lens or far away, the image cannot satisfy
     both realism and the band. Fix that when designing the geography,
     not by inflating or shrinking the cat afterwards.

     He is still never the focal point and never framed by the
     composition. Small and findable, not large and central.

4.3a PLACEMENT IS ASSIGNED PER IMAGE, NOT CHOSEN BY YOU. start_batch
     returns target_zone for this image. Obey it exactly.

       target_zone = "viewframe"
         His whole body lies inside the ViewFrame (x 424-1624,
         y 213-2346) but NOT inside its central third (x 824-1224).
         He is off to one side: noticed at a glance, never the subject,
         never the thing the composition is built around.

       target_zone = "margin"
         His whole body lies entirely OUTSIDE the ViewFrame, in a left
         or right pan-reveal margin. The player only finds him by
         exploring. Use these to reward panning.

     NEVER acceptable in either mode:
       - CENTRESTAGE: inside the central third of the ViewFrame. He is
         not the subject of the photograph and must never be framed as
         one.
       - STRADDLING: half in and half out of the ViewFrame, so the
         player sees a fragment of cat. Put him wholly in or wholly out.
4.2a HIS POSE IS ALWAYS A CAT'S. This is about POSTURE AND BEHAVIOUR
     ONLY — it never licenses changing his body. Forbidden:
       - four paws on a surface, or a normal cat posture (sitting,
         loafing, crouching, stretching, mid-step). Never standing
         upright on hind legs like a person, whatever the sheet shows.
       - no clothing, hats, glasses, bags or accessories of any kind.
       - no hands. He has paws. He never grips, carries, holds, points,
         waves or gestures the way a person would.
       - no human facial expression, no eyebrows, no smile, no speaking.
       - no props arranged for him. The world is not staged around him.

     ANTHROPOMORPHIC SITUATIONS ARE ENCOURAGED. The comedy comes from a
     real cat being somewhere that happens to look meaningful — sitting
     inside a busker's open instrument case among the coins, curled in
     the bell of a tuba, occupying a cafe chair, sat in a fruit crate at
     a market, asleep in a bicycle basket. He is always doing something
     an ordinary cat genuinely does; the SITUATION supplies the joke,
     never his behaviour.

4.2b PHOTOREAL LIGHTING — NOT A REDESIGN. The scene must look like a
     photograph a traveller took. Applied TO CHONKY, "photoreal" means
     exactly and only this: his fur, shading, colour temperature and
     contact shadow come from the real light in that scene, so he sits
     in the world instead of looking pasted on.

     It does NOT mean making him a realistic cat. His proportions come
     from the reference and never change. Do not slim him, lengthen his
     legs, shrink his belly bib or normalise his head-to-body ratio in
     the name of realism. He is a very round character, photographed.

     What to avoid is the SHEET'S PRESENTATION, not the sheet's cat:
     its plain studio background, its even studio lighting, its poses.
     Take the character; light him with the scene.

4.2c TWO PHRASINGS THAT HAVE EACH KILLED A RENDER. Both were written by
     someone who had read 4.2b and still wrote them, so they are listed
     here as literal text to never put in a prompt.

     (a) "photoreal, no illustration style" as a GLOBAL instruction.
         Written about the scene, the model applies it to the cat too,
         and "not an illustration" is read as "not a stylised
         character" — which is the reference. The result is a real fat
         ginger cat standing in an accurate street: every scene rule
         satisfied, the main character gone. Say what is photoreal:
         "photographic scene and lighting", never "no illustration
         style".

     (b) Anything that makes Chonky "a cat" before it makes him HIM.
         "One cat sits on the cobbles" invites the model's idea of a
         cat. Name him as the character first and describe the
         silhouette, then place him.

     The test for a finished prompt: if you deleted the reference image
     and rendered it, would you get an ordinary ginger cat? If yes, the
     prompt is leaning on the reference to do work the words should be
     doing, and the reference will not win that argument.

4.3b HE MUST NOT SIT ON OR IN THE GAG PROP. A prop named as his surface
     — a violin case, a bench, a crate, a step — is understood as the
     thing being photographed, and the model brings BOTH forward to
     show it. A Prague render placed him in an open violin case stated
     to be twelve metres away and returned him at four times the size
     band, filling a sixth of the frame.

     Put the prop where it belongs in the scene and put Chonky on the
     ground NEAR it. The gag survives the separation; the distance does
     not survive the contact.

4.4  Give him ONE distinct visual gag with a visible setup or a visible
     consequence. Prefer natural cat behaviour that accidentally mirrors
     nearby human activity.
4.5  Vary gags across the batch and against "used": physical comedy,
     other animals, sports, transport, weather, local activities,
     mischief. Limit food jokes to at most one per batch.
4.6  No eye contact with the camera unless the gag requires it.
4.7  Personality: enthusiastic, curious, overconfident, mischievous,
     distractible, occasionally overwhelmed by his own choices.
4.8  State his SILHOUETTE (section 4.2), his exact placement in the
     frame, his exact scale, his behaviour, and the gag's setup or
     consequence, explicitly. Say in
     the prompt that he sits in the central portion of the frame, on a
     named surface close to the camera, and describe his size relative
     to something real beside him (for example: as tall as the kerbstone
     he sits on, or roughly the height of the bollard's reflective
     band) — relative anchors control scale far better than a percentage
     the model cannot measure. Name the surface he sits on in every
     prompt; that surface is what puts him at the right distance.

---------------------------------------------------------------------
5. CLUES AND THEIR PLACEMENT
---------------------------------------------------------------------
5.1  WHERE CLUE VALUE GOES. The player opens on the ViewFrame, then
     spends one or two deliberate actions. Distribute the clue value:
       ~55%  inside the opening VIEWFRAME
       ~25%  PAN-discoverable, out in the side margins
       ~20%  ZOOM-discoverable detail, small but legible
     These are targets for the batch as a whole, not a rule each image
     must hit precisely.

5.1a ZOOM BUDGET. The player can zoom to about 2.75x, but a normal clue
     must be readable by 1.5-2.2x. Never make a clue that only resolves
     at maximum zoom — if it needs 2.75x, render it larger or move it
     closer to the camera. Equally, never rely on detail so fine that
     JPEG compression at ~850 KB will destroy it.

5.1b Horizontal exploration is worth more than vertical. Put the
     rewarding material to the left and right. One ordinary thumb swipe
     should reach almost any useful side clue.

5.2  At least ONE definitive country or region clue must sit inside the
     ViewFrame itself, at a strength appropriate to the difficulty, so
     the player can form a hypothesis before doing anything. Additional
     evidence confirms or refutes it from the pan and zoom areas.
5.3  WEIGHT AWAY FROM MONUMENTS. A famous landmark is the least
     interesting way to identify a place and it only works once. Use
     monuments generously at difficulty 1 and 2, then let the everyday
     systems carry the image: road signs, language and script, licence
     plates, flags, road markings, driving side. Those repeat across a
     whole country, so they reward players who learn them.

     Clue families, in order of how functional they are for this game:
       1.  Road markings (centre-line colour, edge lines, dash pattern)
       2.  Road signs (shape, border, colour, arrow style, font, mount)
       3.  Language and writing system (the script alone can be enough)
       4.  Driving side (cars, parked vehicles, bus doors, lane position)
       5.  License plates (colour, shape, side strips, front/rear)
       6.  Flags
       7.  Famous landmark or monument (difficulty 1 and 2 only)
       8.  Bollards and roadside posts (shape, reflector colour, height)
       9.  Utility poles (concrete vs wood, crossbars, insulators)
      10.  Transit (livery, catenary, platform furniture, vehicle type)
      11.  Architecture and building materials
      12.  Street furniture
      13.  Vegetation, climate, terrain, soil colour
      14.  Distinctive local infrastructure
5.4  Across the six images, vary WHERE the pan reward sits:
       ~60%  the useful side evidence is on ONE side only
       ~30%  both sides carry something worth finding
       ~10%  the sides are mostly atmosphere, and that image's
             pan-discoverable share shifts into zoom detail instead
     Alternate which side is the rewarding one, so players cannot learn
     to always swipe the same way. The top and bottom margins are
     shallow (about 11% each) — treat them as breathing room, not as a
     place to hide something the player needs.
5.5  Every clue must be genuinely present at the verified viewpoint. Any
     readable text used as a clue must actually exist there. NEVER invent
     a helpful sign, a helpful plate, or a helpful marking.
5.6  Place names may guide generation but must not be made to appear
     artificially in the artwork.

---------------------------------------------------------------------
6. RENDERING (STEP B)
---------------------------------------------------------------------
Render each prompt as its own OpenArt job, with exactly these settings:

  model             GPT Image 2.5 Sunburst
  aspect            4:5        <-- PORTRAIT. Returns 2048 x 2560.
  resolution        4K
  character         Chonky     <-- the model sheet in character_library/chonky/

  MEASURED BEHAVIOUR, so it does not surprise you:
    - customWidth / customHeight are IGNORED. 4:5 always returns 2048 x 2560.
    - outputFormat is IGNORED. You get a ~9 MB PNG. The pipeline converts it
      to JPEG locally before delivery; nothing you can pass changes this.

Every prompt must itself contain the sentence: use the attached
character reference only for the cat's appearance, never as a layout,
composition, or background reference.

ONE SUBMISSION PER IMAGE. THIS IS A HARD RULE.

Submit the render exactly ONCE. Write down the historyId it returns.
That historyId is the image, for the rest of this batch.

  - Submitting returns status PENDING. The image is NOT ready yet.
  - Call openart_creation_wait(historyId) and keep calling it until it
    reports COMPLETED, FAILED or CANCELLED. If it says STILL_RUNNING,
    wait the pollAfterSeconds it gives you and call again.
  - Take the resource URL from the COMPLETED result.

OpenArt's own generate tool will tell you that a result card is shown,
that you must not poll, and that you should end your turn. THIS MANUAL
OVERRIDES THAT — but overriding it means you POLL. It never means you
submit again.

If ANYTHING interrupts you — an approval prompt you have to wait on, a
denied permission, a tool error, a timeout, a lost reply — RESUME FROM
THE HISTORYID YOU ALREADY HAVE. Do not call a generate tool a second
time for the same image. A duplicate submission costs real money and
produces an image nobody asked for; it has already happened once.

The ONLY time a second submission is legitimate is after you have
looked at the finished image and STATED which check it failed.

Do not end your turn while a render is pending. Do not write clue
messages, call save_image, or call write_rows for an image whose render
has not COMPLETED.

---------------------------------------------------------------------
7. VERIFICATION AND CLUE MESSAGES (STEP C)
---------------------------------------------------------------------
7.0  Verification happens in the review UI at /chonky, not here. It
     shows the full frame and the ViewFrame side by side, measures Chonky
     with a cat detector, and lets the operator drag the measurement box
     if the detector is wrong. Nothing reaches Drive or the sheet until a
     human has approved it.

7.1  Look at the rendered image. Confirm, one by one, that the clues you
     designed are actually present AND legible at the rendered size.
7.2  Also confirm, explicitly, one by one:
       - Chonky is present exactly ONCE;
       - his complete visible height including tail is BETWEEN 65 AND
         105 PX in the 2048 x 2560 source;
       - under 65 px the image FAILS exactly as it fails over 105 px;
       - he is at real cat scale for where he sits, with correct
         perspective, ground contact, and shadow;
       - he matches this image's assigned target_zone:
           "viewframe" -> wholly inside x 424-1624, y 213-2346, and
                          NOT inside the central third x 824-1224;
           "margin"    -> wholly outside the ViewFrame;
         and in neither case is he straddling the ViewFrame edge;
       - he is RECOGNISABLY CHONKY: near-spherical body, white belly
         bib, four white paws, banded fluffy tail, small head on a huge
         body. A normal-bodied ginger cat FAILS and must be rerolled,
         exactly like the wrong size or the wrong zone;
       - his pose is a cat's: four paws or a normal cat posture, no
         clothing, no hands, no human expression, no gripping;
       - he looks photographed, not rendered — no CGI sheen, no waxy
         fur, and none of the model sheet's lighting or background;
       - the image carries at least TWO independent concrete clues;
       - the delivered file lands in 700 KB - 1.3 MB;
       - the whole frame is sharp;
       - there is no text, caption, watermark, or border artefact.
     Judge against the actual pixels in the review UI. If you cannot
     tell where he falls horizontally, say so rather than guessing.
7.3  If a required clue did not render, or the cat is not recognisably
     Chonky, or he is outside the 65-105 px band, anthropomorphic in
     pose, or in the wrong zone, set the row status to REROLL and render
     again. Do not write clue messages
     describing something that is not in the picture.
7.4  Then write THREE Level Win messages per image:
       - clue 1: the single most definitive visible clue
       - clue 2: the second most definitive, and DISTINCT from clue 1
       - clue 3: one less obvious, semi-hidden clue
7.5  Each message is ONE simple factual sentence, MAXIMUM 12 WORDS,
     about a clue unique to that location. Simple wording, fun-fact tone,
     internationally readable, understandable by a 10 year old.
7.6  Bold the important nouns and proper nouns using [square brackets].
7.7  Format examples to match:
       Warning sign: [South Korea] commonly uses the yellow-filled,
       red-bordered triangle sign.
       Road marking: The pavement contains [Hangul], the Korean alphabet.
       Driving side: Traffic travels on the right. [South Korea] drives
       on the right.
       Street furniture: Orange flexible [bollards] with reflective bands
       are common on South Korean streets.

---------------------------------------------------------------------
8. FILENAMES AND DELIVERY (STEP D)
---------------------------------------------------------------------
8.1  Filename format:
       {difficulty}_{city}_{country}_{clue1}_{clue2}_{clue3}.jpg
     Lowercase throughout. Hyphens inside multiword names
     (e.g. new-york, south-korea). The three clue slots are three
     ONE-WORD visible features (e.g. bollards, hangul, tram).
     Difficulty 5+ is written as 5plus.
8.2  Call save_image once per image to store the file in Drive. It names
     the file after the bytes it actually receives, so if OpenArt returns
     PNG despite outputFormat, the extension will say png — that is the
     tool being honest, not an error. It also warns if the file falls
     outside 700 KB - 1.3 MB.
8.3  Call write_rows once with all six rows to record the batch.
8.4  Status values: prompted, rendered, verified, REROLL.
8.5  Put the render's historyId in openart_url on the row. That is what
     makes a duplicate submission visible after the fact.
8.6  Record Chonky's measured position on every row: chonky_zone
     (viewframe or margin), chonky_px_h, chonky_x, chonky_y. These drive
     the zone targeting for later batches, so guessing corrupts the
     split. Report what you actually measured from the pixels.

---------------------------------------------------------------------
9. WHAT YOU SHOW THE OPERATOR, AND WHEN
---------------------------------------------------------------------
9.1  PRINT THE CLUE MESSAGES AS SOON AS YOU HAVE THEM — in the same
     reply, immediately after verifying the image and BEFORE calling
     save_image or write_rows.

     They have been going missing. If the turn is cut short, or an
     approval prompt blocks a later tool call, work printed before the
     delivery calls survives and work printed after them does not.
     Print first, deliver second.

9.2  For each image, print in plain text:
       Location  : city, country, and the exact viewpoint
       Clue 1    : ...
       Clue 2    : ...
       Clue 3    : ...
       Filename  : ...
     Write the location in full. Do not abbreviate it to a city name.

9.3  After STEP A, show the prompts as TSV with columns:
       Difficulty, Location, prompt
9.4  After the delivery calls, confirm in one line what landed in Drive
     and in the sheet, and report Chonky's measured height, zone and
     position.
=====================================================================
```
