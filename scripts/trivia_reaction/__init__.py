"""trivia_reaction — OpenMontage pipeline for "I just found out" reaction reels.

Reads daily-trivia rows, drives OpenArt with a locked character reference
(`ellie.travelcrush`), assembles a 14-17s vertical reel with orange-pill
word-by-word captions, and uploads to the ellie.travelcrush Drive folder.

Sister to scripts/trivia/ (trivia-short pipeline). Same patterns:
- Per-row workspace under projects/<slug>/
- Sheet-as-source-of-truth (TriviaReactionQueue) for workflow state
- Shared Playwright OpenArt driver in scripts/common/openart_driver.py
"""
