"""Static data tables, loaded once and shared by every mode.

Spec section 7: "A static data table of jokers, vouchers, blinds, and hand base
values is needed regardless of mode. Build it in Phase 1 and share it across all
three."
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

__all__ = [
    "hand_table",
    "rank_chips",
    "enhancement_table",
    "edition_table",
    "jokers",
    "joker",
    "blinds",
    "blind",
    "vouchers",
    "voucher",
    "mechanics",
    "RANKS",
    "SUITS",
    "FACE_RANKS",
]

RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = ("hearts", "diamonds", "clubs", "spades")
FACE_RANKS = frozenset({"J", "Q", "K"})


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text())


def hand_table() -> dict[str, dict[str, Any]]:
    return _load("hand_levels.json")["hands"]


def rank_chips() -> dict[str, int]:
    return _load("hand_levels.json")["rank_chips"]


def enhancement_table() -> dict[str, dict[str, Any]]:
    return _load("hand_levels.json")["enhancements"]


def edition_table() -> dict[str, dict[str, Any]]:
    return _load("hand_levels.json")["editions"]


@functools.lru_cache(maxsize=None)
def jokers() -> dict[str, dict[str, Any]]:
    return {j["key"]: j for j in _load("jokers.json")["jokers"]}


def joker(key: str) -> dict[str, Any] | None:
    """Return the static entry for a joker key, or None if unknown.

    An unknown key is not an error - Balatro gets new jokers, and mods add more.
    It is a reason to mark a score non-exact, which is what the scorer does.
    """
    return jokers().get(key)


@functools.lru_cache(maxsize=None)
def blinds() -> dict[str, dict[str, Any]]:
    return {b["key"]: b for b in _load("blinds.json")["blinds"]}


def blind(key: str | None) -> dict[str, Any] | None:
    return blinds().get(key) if key else None


@functools.lru_cache(maxsize=None)
def vouchers() -> dict[str, dict[str, Any]]:
    return {v["key"]: v for v in _load("vouchers.json")["vouchers"]}


def voucher(key: str) -> dict[str, Any] | None:
    return vouchers().get(key)


@functools.lru_cache(maxsize=None)
def mechanics() -> list[dict[str, Any]]:
    return _load("mechanics.json")["mechanics"]


def joker_name(key: str) -> str:
    """Display name for a joker key, falling back to the key itself."""
    entry = joker(key)
    return entry["name"] if entry else key


def joker_description(key: str) -> str | None:
    """Plain-English effect text. Beginner mode (spec 5a) depends on this."""
    entry = joker(key)
    return entry["description"] if entry else None
