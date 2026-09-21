"""Decide which zone the next image's Chonky belongs in.

Compares the configured split against what the ledger actually holds, so a run
converges on the target instead of drifting whichever way the renders happen
to lean.
"""
from __future__ import annotations


def next_zone(rows: list[dict], viewframe_pct: int = 70) -> dict:
    vf = sum(1 for r in rows if r.get("chonky_zone") == "viewframe")
    margin = sum(1 for r in rows if r.get("chonky_zone") == "margin")
    total = vf + margin
    actual = round(100 * vf / total) if total else None

    if total == 0:
        target = "viewframe" if viewframe_pct >= 50 else "margin"
        reason = (f"No history yet; following the configured majority of "
                  f"{viewframe_pct}% ViewFrame.")
    else:
        target = "viewframe" if actual < viewframe_pct else "margin"
        reason = (f"Ledger is {actual}% ViewFrame against a target of "
                  f"{viewframe_pct}%, so this image goes in the {target} zone.")

    return {
        "target_zone": target,
        "stats": {"viewframe": vf, "margin": margin, "total": total,
                  "actual_viewframe_pct": actual},
        "reason": reason,
    }
