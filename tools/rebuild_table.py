"""Rebuild data/jokers.json from the authoritative sources.

Takes data/sources/merged.json (game.lua constants + wiki semantics) as the
spine, and carries across the hand-written `effects` specs from the existing
table where they exist.

Key remapping is the reason this is a rebuild rather than a patch. 13 of the
previously-modelled keys were wrong - derived by a plausible rule that the game
does not follow (j_abstract not j_abstract_joker, j_duo not j_the_duo) and in
one case cannot follow, since game.lua misspells Gluttonous Joker as
`j_gluttenous_joker`. Effects are matched across by NAME, which is stable,
and the key is taken from the game.

Run: python tools/rebuild_table.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "src" / "balatro_advisor" / "data" / "jokers.json"
MERGED = ROOT / "data" / "sources" / "merged.json"

SOURCE_NOTE = (
    "game.lua Game:init_item_prototypes (key, rarity, cost, config constants); "
    "balatrowiki.org Jokers table (effect text, type, activation)"
)


def main() -> None:
    merged = json.loads(MERGED.read_text())
    old = json.loads(TABLE.read_text())

    # Effects are matched by name: names are stable across the sources, keys
    # were not.
    old_by_name = {j["name"]: j for j in old["jokers"]}
    old_by_proto = {}
    for j in merged["jokers"]:
        if j["prototype_name"] in old_by_name:
            old_by_proto[j["name"]] = old_by_name[j["prototype_name"]]

    jokers, remapped, carried = [], [], 0

    for entry in merged["jokers"]:
        previous = old_by_name.get(entry["name"]) or old_by_proto.get(entry["name"])

        joker: dict[str, Any] = {
            "key": entry["key"],
            "name": entry["name"],
            "rarity": entry["rarity"],
            "cost": entry["cost"],
            "order": entry["order"],
            "description": entry["effect_text"],
            "type": entry["type"],
            "activation": entry["activation"],
            "config": entry["config"],
            "unlock": entry["unlock"],
            "blueprint_compat": entry["blueprint_compat"],
            "perishable_compat": entry["perishable_compat"],
            "eternal_compat": entry["eternal_compat"],
            "source": SOURCE_NOTE,
        }
        if entry["enhancement_gate"]:
            joker["enhancement_gate"] = entry["enhancement_gate"]
        if entry["prototype_name"] != entry["name"]:
            joker["prototype_name"] = entry["prototype_name"]

        if previous:
            if previous["key"] != entry["key"]:
                remapped.append((entry["name"], previous["key"], entry["key"]))
            if previous.get("flag"):
                joker["flag"] = previous["flag"]
                carried += 1
            elif previous.get("effects") is not None and not previous.get("unmodelled"):
                joker["effects"] = previous["effects"]
                carried += 1
            elif previous.get("unmodelled"):
                joker["unmodelled"] = True
                joker["reason"] = previous.get("reason", "")
            else:
                joker["unmodelled"] = True
                joker["reason"] = "Effect not yet translated into the effect grammar."
        else:
            joker["unmodelled"] = True
            joker["reason"] = "Effect not yet translated into the effect grammar."

        jokers.append(joker)

    doc = old["_doc"]
    doc["sources"] = merged["_doc"]["sources"]
    doc["keys"] = (
        "Internal keys come from game.lua and are authoritative. They do NOT "
        "follow from the display name: the game shortens many (j_duo, "
        "j_abstract, j_smiley) and misspells one (j_gluttenous_joker for "
        "Gluttonous Joker). Never derive a key from a name."
    )
    doc["enumeration"] = {
        "complete": True,
        "count": len(jokers),
        "source": SOURCE_NOTE,
    }
    doc.pop("counter_convention", None) or None

    TABLE.write_text(json.dumps({"_doc": doc, "jokers": jokers}, indent=2) + "\n")

    modelled = [j for j in jokers if not j.get("unmodelled")]
    print(f"rebuilt {len(jokers)} jokers")
    print(f"  effects carried across: {carried}")
    print(f"  modelled              : {len(modelled)}")
    print(f"  still to model        : {len(jokers) - len(modelled)}")
    print(f"\nkeys corrected: {len(remapped)}")
    for name, before, after in remapped:
        print(f"  {name:24} {before:28} -> {after}")


if __name__ == "__main__":
    main()
