"""trivia_captain — OpenMontage pipeline for "I just found out" reaction reels.

Reads daily-trivia rows, drives OpenArt with a locked character reference
(`Captain Archibald`), assembles a 14-17s vertical reel with orange-pill
word-by-word captions, and uploads to the Captain Archibald Drive folder.

Sister to scripts/trivia/ (trivia-short pipeline). Same patterns:
- Per-row workspace under projects/<slug>/
- Sheet-as-source-of-truth (TriviaCaptainQueue) for workflow state
- Shared Playwright OpenArt driver in scripts/common/openart_driver.py
"""
