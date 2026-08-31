"""Card shorthand parsing and formatting.

Spec section 7: "Accept shorthand card entry: `KH QD 10S 4D` etc."

Grammar, kept deliberately small because it is typed by a human mid-game:

    KH              King of hearts
    10S             Ten of spades (two-character rank)
    TS              Ten of spades (T is accepted for 10)
    KH:gold         with an enhancement
    KH:steel:foil   enhancement and edition, order-independent
    KH:red          with a red seal
    stone           a Stone card, which has no rank or suit

Modifier tokens are matched against the enhancement, edition and seal
vocabularies, so the player does not have to remember which is which.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["parse_card", "parse_hand", "format_card", "format_hand", "CardSyntaxError"]

_ENHANCEMENTS = frozenset(
    {"gold", "steel", "glass", "bonus", "mult", "wild", "lucky", "stone"}
)
_EDITIONS = frozenset({"foil", "holographic", "polychrome"})
_SEALS = frozenset({"red", "blue", "gold", "purple"})

_SUIT_LETTERS = {
    "h": "hearts", "d": "diamonds", "c": "clubs", "s": "spades",
}

_CARD = re.compile(r"^(10|[2-9TJQKA])([HDCS])$", re.IGNORECASE)

# "gold" is both an enhancement and a seal. Enhancement wins on a bare token,
# because Gold cards are far more common in play than Gold seals; ":goldseal"
# disambiguates.
_EXPLICIT_SEAL = re.compile(r"^(red|blue|gold|purple)seal$", re.IGNORECASE)


class CardSyntaxError(ValueError):
    """Raised on shorthand that cannot be read. Carries the offending token."""

    def __init__(self, token: str, reason: str) -> None:
        self.token = token
        super().__init__(f"{token!r}: {reason}")


def parse_card(token: str, location: str = "hand") -> dict[str, Any]:
    """Parse one shorthand token into a canonical card object."""
    raw = token.strip()
    if not raw:
        raise CardSyntaxError(token, "empty")

    parts = re.split(r"[:/]", raw)
    head, mods = parts[0].strip(), [p.strip().lower() for p in parts[1:] if p.strip()]

    card: dict[str, Any] = {
        "rank": None, "suit": None, "enhancement": "none",
        "edition": "base", "seal": "none", "location": location,
    }

    if head.lower() == "stone":
        card["enhancement"] = "stone"
    else:
        match = _CARD.match(head)
        if not match:
            raise CardSyntaxError(
                token,
                "expected a rank and a suit like KH, 10S or 4D (or the word 'stone')",
            )
        rank = match.group(1).upper()
        card["rank"] = "10" if rank == "T" else rank
        card["suit"] = _SUIT_LETTERS[match.group(2).lower()]

    for mod in mods:
        seal_match = _EXPLICIT_SEAL.match(mod)
        if seal_match:
            card["seal"] = seal_match.group(1).lower()
        elif mod in _ENHANCEMENTS:
            card["enhancement"] = mod
        elif mod in _EDITIONS:
            card["edition"] = mod
        elif mod in _SEALS:
            card["seal"] = mod
        else:
            raise CardSyntaxError(
                token,
                f"unknown modifier {mod!r}. Enhancements: "
                f"{', '.join(sorted(_ENHANCEMENTS))}. Editions: "
                f"{', '.join(sorted(_EDITIONS))}. Seals: "
                f"{', '.join(s + 'seal' for s in sorted(_SEALS))}.",
            )

    if card["enhancement"] == "stone":
        # Stone cards have no rank or suit even if one was typed. Silently
        # keeping a rank here would corrupt every rank tally downstream.
        card["rank"] = card["suit"] = None

    return card


def parse_hand(text: str, location: str = "hand") -> list[dict[str, Any]]:
    """Parse a whitespace- or comma-separated run of shorthand tokens."""
    tokens = [t for t in re.split(r"[\s,]+", text.strip()) if t]
    return [parse_card(t, location) for t in tokens]


_SUIT_GLYPH = {"hearts": "H", "diamonds": "D", "clubs": "C", "spades": "S"}


def format_card(card: dict[str, Any], *, verbose: bool = False) -> str:
    """Render a card back to shorthand. Round-trips through parse_card."""
    if card.get("enhancement") == "stone":
        base = "stone"
    else:
        base = f"{card.get('rank')}{_SUIT_GLYPH.get(card.get('suit'), '?')}"

    if not verbose:
        return base

    mods = []
    if card.get("enhancement") not in (None, "none", "stone"):
        mods.append(card["enhancement"])
    if card.get("edition") not in (None, "base"):
        mods.append(card["edition"])
    if card.get("seal") not in (None, "none"):
        mods.append(f"{card['seal']}seal")
    return base + ("".join(f":{m}" for m in mods) if mods else "")


def format_hand(cards: list[dict[str, Any]], *, verbose: bool = False, index: bool = False) -> str:
    if index:
        return " ".join(
            f"[{i}]{format_card(c, verbose=verbose)}" for i, c in enumerate(cards)
        )
    return " ".join(format_card(c, verbose=verbose) for c in cards)
