"""scripts/trivia_quiz — v0.1 builder for the trivia-quiz pipeline.

Reads a per-row fixture at projects/trivia-quiz/<slug>/inputs/quiz_row.yaml, generates the
canonical artifacts (brief.json, script.json, asset_manifest.json,
edit_decisions.json, quiz_meta.json), builds bg.mp4, and stages everything for
the Remotion `TriviaQuiz` composition.

v0.1 scope: solid-color backdrops by default (FLUX optional via --with-flux),
no VO / music / SFX by default (each behind a flag), no Google Sheets, no
smart link. The point of v0.1 is to validate the visual FORMAT. The
production-quality layers stack on top once the format is proven.

Entry point: scripts.trivia_quiz.build
"""
