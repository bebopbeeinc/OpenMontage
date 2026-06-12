"""trivia_captain_2t1l — "Captain's Two Truths & a Lie" pipeline.

A higher-traction twist on trivia-quiz. Captain Archibald hosts a 2-truths-1-lie
round about a destination in a single 15s Seedance clip (numbered facts + finger
counting, in-prompt game-show music). A Remotion overlay (TriviaTwoTruthsK3,
goldround theme) adds a full-width top title + place banner and bottom-stacking
fact banners. The lie is NEVER revealed on screen — comments are the game.

Sister to scripts/trivia_captain/ (same Seedance + OpenArt driver + Drive/Sheet
patterns), but content is curated 2T1L sets authored directly in the Queue sheet
(no DailyTriviaConfig dependency), and the compose path is the kinetic full-bleed
TriviaTwoTruthsK3 composition (not TriviaWithBg).

See styles/trivia-captain-2t1l.yaml for the locked format contract.
"""
