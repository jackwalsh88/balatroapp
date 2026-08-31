"""Translate the remaining jokers' effects into the declarative grammar.

Every constant here is read from the joker's own `config` block, which came out
of game.lua - nothing is typed from memory. The script asserts that: if a
constant it is about to write is not present in that joker's config, it fails
rather than writing a number with no source.

Three outcomes per joker:

  effects: [...]   modelled - the scorer computes its contribution
  effects: []      modelled as contributing NOTHING TO SCORE. Economy and
                   utility jokers genuinely add no chips or mult, so scoring
                   them as zero is correct rather than a silent gap.
  unmodelled       the canonical state does not carry what the effect needs.
                   Each one names the missing field.

Run: python tools/model_effects.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "src" / "balatro_advisor" / "data" / "jokers.json"


def cfg(joker: dict[str, Any], *path: Any) -> Any:
    """Read a value out of the joker's game.lua config, or fail loudly."""
    node = joker.get("config") or {}
    for step in path:
        if not isinstance(node, dict) or step not in node:
            raise KeyError(
                f"{joker['key']}: config has no {'.'.join(map(str, path))} "
                f"(config={node}). Refusing to write a constant with no source."
            )
        node = node[step]
    return node


# --------------------------------------------------------------------------
# Jokers with no effect on the score.
#
# Economy, deck manipulation, hand-size and consumable-generation jokers do not
# add chips or mult. Modelling them as [] is a positive statement - "this
# contributes nothing to a hand's score" - not an admission of ignorance, and
# it is what lets a board full of economy jokers still produce an exact score.
# --------------------------------------------------------------------------

NO_SCORING_EFFECT = {
    # economy
    "j_egg": "Gains sell value at end of round. No scoring effect.",
    "j_faceless": "Earns money on discard. No scoring effect.",
    "j_todo_list": "Earns money for a hand type. No scoring effect.",
    "j_cloud_9": "Earns money at end of round. No scoring effect.",
    "j_rocket": "Earns money at end of round. No scoring effect.",
    "j_gift": "Adds sell value at end of round. No scoring effect.",
    "j_reserved_parking": "Chance of money for held face cards. No scoring effect.",
    "j_mail": "Earns money on discard. No scoring effect.",
    "j_to_the_moon": "Extra interest at end of round. No scoring effect.",
    "j_golden": "Earns money at end of round. No scoring effect.",
    "j_trading": "Earns money on discard. No scoring effect.",
    "j_ticket": "Played Gold cards earn money. No scoring effect.",
    "j_matador": "Earns money when the boss ability triggers. No scoring effect.",
    "j_satellite": "Earns money at end of round. No scoring effect.",
    # hand size / hands / discards
    "j_juggler": "Increases hand size. No scoring effect.",
    "j_drunkard": "Adds a discard. No scoring effect.",
    "j_merry_andy": "Adds discards, reduces hand size. No scoring effect.",
    "j_troubadour": "Adds hand size, removes a hand. No scoring effect.",
    "j_turtle_bean": "Adds hand size, decaying each round. No scoring effect.",
    "j_burglar": "Trades discards for hands on blind select. No scoring effect.",
    # card and consumable generation
    "j_marble": "Adds a Stone card to the deck on blind select. Changes future "
                "deck composition, not this hand's score.",
    "j_dna": "Copies a card into the deck. No scoring effect.",
    "j_sixth_sense": "Creates a Spectral card. No scoring effect.",
    "j_superposition": "Creates a Tarot card. No scoring effect.",
    "j_seance": "Creates a Spectral card. No scoring effect.",
    "j_riff_raff": "Creates Jokers on blind select. No scoring effect.",
    "j_vagabond": "Creates a Tarot card. No scoring effect.",
    "j_cartomancer": "Creates a Tarot card on blind select. No scoring effect.",
    "j_hallucination": "Chance of a Tarot card on opening a pack. No scoring effect.",
    "j_certificate": "Adds a card to hand at round start. No scoring effect.",
    "j_perkeo": "Copies a consumable at end of shop. No scoring effect.",
    "j_invisible": "Duplicates a Joker when sold. No scoring effect.",
    # shop and meta
    "j_chaos": "Free shop reroll. No scoring effect.",
    "j_astronomer": "Makes Planet cards free. No scoring effect.",
    "j_ring_master": "Allows duplicate cards in pools. No scoring effect.",
    "j_diet_cola": "Creates a Double Tag when sold. No scoring effect.",
    "j_luchador": "Disables the boss blind when SOLD. Holding it does nothing, "
                  "so it has no effect on a hand scored while held.",
    "j_mr_bones": "Prevents death. No scoring effect.",
    # level manipulation - affects future hands, not this one
    "j_space": "May upgrade the played hand's level AFTER it scores. Does not "
               "change the score of the hand that triggers it.",
    "j_burnt": "Upgrades the level of a discarded hand. No scoring effect on "
               "the hand being scored.",
}


# --------------------------------------------------------------------------
# Effects the canonical state cannot supply. Each names the missing field, so
# closing the gap is a schema change with a known shape rather than a mystery.
# --------------------------------------------------------------------------

STILL_UNMODELLED = {
    "j_blueprint": "Copy semantics and Blueprint/Brainstorm chain ordering are "
                   "listed as unverified in spec section 9.",
    "j_brainstorm": "See j_blueprint. Chain ordering unverified (spec section 9).",
    "j_card_sharp": "Needs which poker hands have been played THIS ROUND; the "
                    "canonical state records lifetime counts only.",
    "j_ancient": "The active suit changes every round and the state carries no "
                 "field for it.",
    "j_idol": "The active rank and suit change every round and the state "
              "carries no field for them.",
    "j_hiker": "Permanently adds chips to individual cards. The card schema has "
               "no accumulated-bonus-chips field, so repeated use would be "
               "undercounted.",
    "j_midas_mask": "Converts played face cards to Gold mid-scoring, replacing "
                    "whatever enhancement they had. Modelling it needs the "
                    "scorer to mutate cards during evaluation.",
    "j_oops": "Doubles every listed probability, so it changes the expected "
              "value of every other random joker rather than contributing "
              "itself. Needs a scorer-wide probability multiplier.",
}


def build(joker: dict[str, Any]) -> dict[str, Any] | None:
    """Return the fields to merge into this joker, or None to leave it alone."""
    key = joker["key"]

    if key in NO_SCORING_EFFECT:
        return {"effects": [], "scoring_note": NO_SCORING_EFFECT[key]}

    if key in STILL_UNMODELLED:
        return {"unmodelled": True, "reason": STILL_UNMODELLED[key]}

    # -- counter-driven: the game shows the current value on the joker -----
    counter_stat = {
        "j_ceremonial": "mult", "j_red_card": "mult", "j_fortune_teller": "mult",
        "j_flash": "mult", "j_trousers": "mult",
        "j_runner": "chips", "j_square": "chips",
        "j_hit_the_road": "xmult", "j_caino": "xmult", "j_yorick": "xmult",
        "j_loyalty_card": "xmult",
    }
    if key in counter_stat:
        return {"effects": [
            {"when": "independent", counter_stat[key]: {"from": "counter"}}
        ]}

    # -- everything else, one at a time, each constant read from config ----
    if key == "j_stone":
        return {"effects": [{
            "when": "independent",
            "chips": {"from": "stone_in_deck", "scale": cfg(joker, "extra")},
        }]}

    if key == "j_swashbuckler":
        return {"effects": [{
            "when": "independent",
            "mult": {"from": "other_joker_sell_total"},
        }]}

    if key == "j_shoot_the_moon":
        return {"effects": [{
            "when": "on_held", "if": {"rank": ["Q"]},
            "mult": cfg(joker, "extra"),
        }]}

    if key == "j_bootstraps":
        return {"effects": [{
            "when": "independent",
            "mult": {"from": "money_div_5", "scale": cfg(joker, "extra", "mult")},
        }]}

    if key == "j_erosion":
        return {"effects": [{
            "when": "independent",
            "mult": {"from": "cards_removed_from_deck", "scale": cfg(joker, "extra")},
        }]}

    if key == "j_baseball":
        return {"effects": [{
            "when": "independent",
            "xmult": {"from": "uncommon_joker_count", "pow": cfg(joker, "extra")},
        }]}

    if key == "j_acrobat":
        return {"effects": [{
            "when": "independent", "if": {"hands_remaining": 1},
            "xmult": cfg(joker, "extra"),
        }]}

    if key == "j_flower_pot":
        return {"effects": [{
            "when": "independent", "if": {"all_suits_in_scoring": True},
            "xmult": cfg(joker, "extra"),
        }]}

    if key == "j_seeing_double":
        return {"effects": [{
            "when": "independent", "if": {"scoring_suit_plus_other": "clubs"},
            "xmult": cfg(joker, "extra"),
        }]}

    if key == "j_drivers_license":
        # "at least 16 Enhanced cards": the 16 is in the unlock condition, not
        # in config, so it is taken from the wiki's effect text and recorded.
        return {"effects": [{
            "when": "independent", "if": {"min_enhanced_in_deck": 16},
            "xmult": cfg(joker, "extra"),
        }], "constant_note": "The threshold of 16 comes from the wiki effect "
                             "text; game.lua keeps it in unlock_condition."}

    if key == "j_selzer":
        return {"effects": [{
            "when": "retrigger_scored", "if": {"counter_positive": True},
            "times": 1,
        }], "constant_note": "Retriggers while its remaining-hands counter is "
                             "above zero; the adapter reads that counter."}

    # -- random: expected value from the game's own odds -------------------
    if key == "j_misprint":
        return {"effects": [{
            "when": "independent",
            "random": {"stat": "mult",
                       "min": cfg(joker, "extra", "min"),
                       "max": cfg(joker, "extra", "max")},
        }]}

    if key == "j_bloodstone":
        return {"effects": [{
            "when": "on_scored", "if": {"suit": ["hearts"]},
            "random": {"stat": "xmult",
                       "odds": cfg(joker, "extra", "odds"),
                       "value": cfg(joker, "extra", "Xmult")},
        }]}

    if key == "j_chicot":
        return {"flag": "disable_boss"}

    return None


def main() -> None:
    table = json.loads(TABLE.read_text())
    changed = 0

    for joker in table["jokers"]:
        if not joker.get("unmodelled"):
            continue
        update = build(joker)
        if update is None:
            continue
        joker.pop("unmodelled", None)
        joker.pop("reason", None)
        joker.update(update)
        changed += 1

    TABLE.write_text(json.dumps(table, indent=2) + "\n")

    modelled = [j for j in table["jokers"] if not j.get("unmodelled")]
    zero = [j for j in table["jokers"] if j.get("effects") == []]
    left = [j for j in table["jokers"] if j.get("unmodelled")]

    print(f"updated {changed} jokers")
    print(f"  modelled          : {len(modelled)}/150")
    print(f"    of which zero   : {len(zero)} (economy/utility - no scoring effect)")
    print(f"  still unmodelled  : {len(left)}")
    for j in left:
        print(f"    {j['key']:16} {j['reason'][:80]}")


if __name__ == "__main__":
    main()
