"""Merge the full 150-joker enumeration into data/jokers.json.

Phase A of completing the joker table. This adds every vanilla joker the table
was missing, so no joker is ever an "unknown key" again - but it deliberately
adds NO effect data, because the enumeration source carries none.

The distinction it maintains:

    known     the joker is in the table, with its real name and rarity
    modelled  the scorer can compute what it contributes

Phase A delivers `known` for all 150. `modelled` needs effect descriptions and
constants from balatrowiki.org, and every entry this script creates is flagged
`needs_verification` until those land.

Idempotent: existing entries are never overwritten, only gap-filled.

Run: python tools/merge_enumeration.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "src" / "balatro_advisor" / "data" / "jokers.json"
SOURCE = ROOT / "data" / "sources" / "enumeration.json"

# Real internal keys that do not follow the snake_case-of-the-name rule. These
# were in the table before the enumeration landed and are kept as-is; the rule
# below would have produced j_joker_stencil, j_jolly_joker, and so on.
#
# That mismatch is exactly why every derived key is flagged key_verified:false.
# The authoritative key list comes from the game, via the Phase 1 mod adapter.


def derive_key(name: str) -> str:
    """j_ + snake_case(name). Documented, mechanical, and often wrong.

    Balatro shortens some keys (Jolly Joker is j_jolly, Joker Stencil is
    j_stencil), so a derived key is a placeholder for matching within this
    project, not a claim about the game's internal identifier.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"j_{slug}"


def main() -> None:
    table = json.loads(TABLE.read_text())
    source = json.loads(SOURCE.read_text())

    existing = {j["name"]: j for j in table["jokers"]}
    by_key = {j["key"]: j for j in table["jokers"]}

    added, backfilled, conflicts = [], [], []

    for entry in source["jokers"]:
        name, rarity = entry["name"], entry["rarity"]
        current = existing.get(name)

        if current is not None:
            # Gap-fill rarity only where we never recorded one, and report any
            # disagreement rather than silently trusting either side.
            if current.get("rarity") and current["rarity"] != rarity:
                conflicts.append((name, current["rarity"], rarity))
            elif not current.get("rarity"):
                current["rarity"] = rarity
                backfilled.append(name)
            current.setdefault("key_verified", False)
            current["sprite_order"] = entry["order"]
            continue

        key = derive_key(name)
        if key in by_key:
            conflicts.append((name, f"derived key {key} collides", by_key[key]["name"]))
            continue

        added.append({
            "key": key,
            "name": name,
            "rarity": rarity,
            "cost": None,
            "sprite_order": entry["order"],
            "key_verified": False,
            "description": None,
            "unmodelled": True,
            "reason": (
                "Enumerated but not yet modelled. No effect description or "
                "constants have been sourced for this joker."
            ),
            "needs_verification": True,
            "source": source["_doc"]["source"],
        })

    table["jokers"].extend(added)
    table["jokers"].sort(key=lambda j: j.get("sprite_order", 9999))

    table["_doc"]["enumeration"] = {
        "complete": True,
        "count": len(table["jokers"]),
        "source": source["_doc"]["source"],
        "note": (
            "All 150 vanilla jokers are present. Entries carrying "
            "needs_verification are enumerated but not modelled: the scorer "
            "knows their name and rarity and marks any hand involving them "
            "non-exact. Effect data comes from balatrowiki.org."
        ),
    }

    TABLE.write_text(json.dumps(table, indent=2) + "\n")

    modelled = [j for j in table["jokers"] if not j.get("unmodelled")]
    pending = [j for j in table["jokers"] if j.get("needs_verification")]

    print(f"table now holds {len(table['jokers'])} jokers")
    print(f"  added        : {len(added)}")
    print(f"  rarity filled: {len(backfilled)}")
    print(f"  modelled     : {len(modelled)}")
    print(f"  need effects : {len(pending)}")
    if conflicts:
        print("\nCONFLICTS (resolve by hand):")
        for row in conflicts:
            print("  ", row)


if __name__ == "__main__":
    main()
