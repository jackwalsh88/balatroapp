"""Poker hand classification for a set of played cards.

Separated from the scorer because classification and arithmetic fail
differently: a misclassified hand is a rules bug, a mis-added chip is an
arithmetic bug, and the spec's whole architecture rests on being able to tell
those apart.

Handles the four card properties that break naive poker logic:
  - Stone cards have no rank or suit, never form part of a rank or suit match,
    and always score anyway.
  - Wild cards count as every suit at once.
  - Smeared Joker collapses hearts/diamonds and spades/clubs into two suits.
  - Four Fingers drops the flush/straight length requirement to 4; Shortcut
    lets straights skip one rank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import data

__all__ = ["HandFlags", "Classification", "classify", "RANK_VALUE"]

RANK_VALUE = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}

# Ordered best to worst. classify() returns the first that matches.
_PRECEDENCE = (
    "flush_five",
    "flush_house",
    "five_of_a_kind",
    "straight_flush",
    "four_of_a_kind",
    "full_house",
    "flush",
    "straight",
    "three_of_a_kind",
    "two_pair",
    "pair",
    "high_card",
)


@dataclass(frozen=True)
class HandFlags:
    """Joker flags that change how a hand is *classified*, not how it scores."""

    four_fingers: bool = False
    shortcut: bool = False
    smeared: bool = False
    pareidolia: bool = False
    splash: bool = False
    disable_boss: bool = False

    @classmethod
    def from_jokers(cls, joker_states: Iterable[dict[str, Any]]) -> "HandFlags":
        flags = set()
        for js in joker_states:
            entry = data.joker(js.get("key", ""))
            if entry and entry.get("flag"):
                flags.add(entry["flag"])
        return cls(
            four_fingers="four_fingers" in flags,
            shortcut="shortcut" in flags,
            smeared="smeared" in flags,
            pareidolia="pareidolia" in flags,
            splash="splash" in flags,
            disable_boss="disable_boss" in flags,
        )


@dataclass
class Classification:
    hand_type: str
    scoring: list[int]
    """Indices (into the played list) of cards that actually score."""
    contains: set[str] = field(default_factory=set)
    """Every hand type present in the played cards. Jokers like Jolly Joker ask
    'does the hand CONTAIN a pair', which is true of a full house too."""


def _suit_groups(cards: list[dict[str, Any]], smeared: bool) -> dict[str, list[int]]:
    """Map a suit-group key to the indices of cards that can count as it."""
    if smeared:
        alias = {"hearts": "red", "diamonds": "red", "spades": "black", "clubs": "black"}
        keys = ("red", "black")
    else:
        alias = {s: s for s in data.SUITS}
        keys = data.SUITS

    groups: dict[str, list[int]] = {k: [] for k in keys}
    for i, card in enumerate(cards):
        if card.get("enhancement") == "stone":
            continue  # no suit at all
        if card.get("enhancement") == "wild":
            for k in keys:
                groups[k].append(i)
            continue
        suit = card.get("suit")
        if suit in alias:
            groups[alias[suit]].append(i)
    return groups


def _best_flush(cards: list[dict[str, Any]], flags: HandFlags) -> list[int]:
    need = 4 if flags.four_fingers else 5
    best: list[int] = []
    for idxs in _suit_groups(cards, flags.smeared).values():
        if len(idxs) >= need and len(idxs) > len(best):
            best = idxs
    return best


def _best_run(cards: list[dict[str, Any]], flags: HandFlags) -> list[int]:
    """Longest straight among the played cards, as card indices.

    Straights need distinct ranks, so stone cards (rankless) can never be part
    of one. Ace is tried both high and low.
    """
    need = 4 if flags.four_fingers else 5
    max_step = 2 if flags.shortcut else 1

    best: list[int] = []
    for ace_low in (False, True):
        by_value: dict[int, int] = {}
        for i, card in enumerate(cards):
            rank = card.get("rank")
            if rank is None:
                continue
            value = 1 if (rank == "A" and ace_low) else RANK_VALUE[rank]
            by_value.setdefault(value, i)  # duplicate ranks cannot both be used

        values = sorted(by_value)
        run: list[int] = []
        for value in values:
            if run and value - run[-1] <= max_step:
                run.append(value)
            else:
                run = [value]
            if len(run) > len(best) and len(run) >= need:
                best = [by_value[v] for v in run]
    return best


def _rank_counts(cards: list[dict[str, Any]]) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for i, card in enumerate(cards):
        rank = card.get("rank")
        if rank is None:
            continue  # stone cards match nothing
        counts.setdefault(rank, []).append(i)
    return counts


def classify(cards: list[dict[str, Any]], flags: HandFlags | None = None) -> Classification:
    """Classify played ``cards`` and report which of them score."""
    flags = flags or HandFlags()
    if not cards:
        raise ValueError("cannot classify an empty hand")

    counts = _rank_counts(cards)
    groups = sorted(counts.values(), key=len, reverse=True)
    largest = len(groups[0]) if groups else 0
    pairs = [g for g in groups if len(g) >= 2]

    flush = _best_flush(cards, flags)
    run = _best_run(cards, flags)
    flush_set = set(flush)

    # A hand is a flush house / flush five only if the ranked cards making the
    # full house or five-of-a-kind are themselves the flush.
    def _within_flush(idxs: list[int]) -> bool:
        return bool(flush) and set(idxs).issubset(flush_set)

    full_house: list[int] = []
    if largest >= 3 and len(pairs) >= 2:
        trips = next(g for g in groups if len(g) >= 3)
        pair = next(g for g in pairs if g is not trips)
        full_house = trips[:3] + pair[:2]

    five = groups[0][:5] if largest >= 5 else []
    four = groups[0][:4] if largest >= 4 else []
    three = next((g[:3] for g in groups if len(g) >= 3), [])
    two_pair = (pairs[0][:2] + pairs[1][:2]) if len(pairs) >= 2 else (
        groups[0][:4] if largest >= 4 else []
    )
    one_pair = pairs[0][:2] if pairs else []

    straight_flush = run if (run and flush and set(run).issubset(flush_set)) else []

    candidates: dict[str, list[int]] = {
        "flush_five": five if _within_flush(five) else [],
        "flush_house": full_house if _within_flush(full_house) else [],
        "five_of_a_kind": five,
        "straight_flush": straight_flush,
        "four_of_a_kind": four,
        "full_house": full_house,
        "flush": flush,
        "straight": run,
        "three_of_a_kind": three,
        "two_pair": two_pair,
        "pair": one_pair,
        "high_card": [],
    }

    contains = {name for name, idxs in candidates.items() if idxs}
    contains.add("high_card")

    hand_type = "high_card"
    scoring: list[int] = []
    for name in _PRECEDENCE:
        if name == "high_card":
            break
        if candidates[name]:
            hand_type, scoring = name, candidates[name]
            break

    if hand_type == "high_card":
        ranked = [(RANK_VALUE[c["rank"]], i) for i, c in enumerate(cards) if c.get("rank")]
        scoring = [max(ranked)[1]] if ranked else []

    scoring_set = set(scoring)
    if flags.splash:
        scoring_set = set(range(len(cards)))
    # Stone cards always score, whatever hand was made.
    scoring_set |= {i for i, c in enumerate(cards) if c.get("enhancement") == "stone"}

    return Classification(
        hand_type=hand_type,
        scoring=sorted(scoring_set),
        contains=contains,
    )
