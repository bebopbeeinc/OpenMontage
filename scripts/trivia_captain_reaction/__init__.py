"""trivia_captain_reaction — OpenMontage pipeline for "I just found out" reaction reels fronted by Captain Archibald.

Sister of scripts/trivia_reaction/ (the ellie.travelcrush reaction pipeline).
Same format, same Seedance mechanics, same Remotion compose path — the ONLY
creative change is the avatar: a locked OpenArt character reference
(`Captain Archibald`, a 70-year-old male lifelong world traveler) instead of
`ellie.travelcrush`. Built to A/B test whether the Captain character itself
is hurting reach on the dailytrivia.tc account, holding ellie's proven
reaction format constant.

Reads daily-trivia rows, drives OpenArt with the locked character reference,
assembles a 13-15s vertical reel with warm-purple-pill word-by-word captions,
and uploads to the dailytrivia.tc Drive folder. Workflow state lives in the
`Posts_Reaction` tab on the dailytrivia.tc Post Calendar (alongside
Posts / Posts_Quiz / Posts_2T1L).

Same patterns as the sister pipelines:
- Per-row workspace under projects/trivia-captain-reaction/<slug>/
- Sheet-as-source-of-truth (Posts_Reaction) for workflow state
- Shared Playwright OpenArt driver in scripts/common/openart_driver.py
"""
