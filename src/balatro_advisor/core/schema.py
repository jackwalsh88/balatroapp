"""Canonical state: loading, default application, and validation.

The schema itself lives in ``schema/state.schema.json`` and is the single
source of truth. Nothing in this module restates it.

Ingest policy: refuse to advise on invalid state rather than guessing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

__all__ = [
    "StateInvalid",
    "SCHEMA_PATH",
    "load_schema",
    "validate",
    "normalize",
    "load_state",
    "HAND_TYPES",
]

_PKG_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PKG_ROOT.parent.parent

SCHEMA_PATH = _REPO_ROOT / "schema" / "state.schema.json"

HAND_TYPES = (
    "high_card",
    "pair",
    "two_pair",
    "three_of_a_kind",
    "straight",
    "flush",
    "full_house",
    "four_of_a_kind",
    "straight_flush",
    "five_of_a_kind",
    "flush_house",
    "flush_five",
)


class StateInvalid(ValueError):
    """Raised on ingest of a state document that cannot be trusted.

    Carries every problem found, not just the first, so an adapter can show a
    user all of what needs fixing in one pass.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "invalid state")


_schema_cache: dict[str, Any] | None = None


def load_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(SCHEMA_PATH.read_text())
    return _schema_cache


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------
# jsonschema validates but does not fill defaults, and downstream code should
# never have to write `state.get("resources", {}).get("hand_size") or 8`.
# Normalization happens once, at the boundary.

_CARD_DEFAULTS = {
    "id": None,
    "enhancement": "none",
    "edition": "base",
    "seal": "none",
    "location": "deck",
    "confidence": "high",
}

_JOKER_DEFAULTS = {
    "name": None,
    "edition": "base",
    "stickers": (),
    "sell_value": None,
    "confidence": "high",
}


def _card(raw: dict[str, Any], location: str | None = None) -> dict[str, Any]:
    card = dict(_CARD_DEFAULTS)
    card.update({k: v for k, v in raw.items() if v is not None or k in ("rank", "suit")})
    for key, fallback in _CARD_DEFAULTS.items():
        if card.get(key) is None and fallback is not None:
            card[key] = fallback
    if location is not None:
        card["location"] = location
    return card


def normalize(state: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``state`` with every optional field materialized.

    Idempotent. Does not validate - call :func:`validate` first, or use
    :func:`load_state` which does both in the right order.
    """
    out: dict[str, Any] = dict(state)

    out.setdefault("captured_at", None)

    run = dict(out.get("run") or {})
    run.setdefault("ante_max", 8)
    run.setdefault("round", None)
    run.setdefault("deck_name", None)
    run.setdefault("stake", None)
    run.setdefault("vouchers_redeemed", [])
    out["run"] = run

    res = dict(out.get("resources") or {})
    res.setdefault("hand_size", 8)
    res.setdefault("joker_slots_total", 5)
    res.setdefault("consumable_slots_total", 2)
    out["resources"] = res

    jokers = []
    for raw in out.get("jokers") or []:
        j = dict(_JOKER_DEFAULTS)
        j.update(raw)
        j["stickers"] = list(j.get("stickers") or [])
        j["internal_state"] = dict(j.get("internal_state") or {})
        j["internal_state"].setdefault("counter", None)
        contrib = j.get("current_contribution")
        if contrib is not None:
            contrib = {
                "chips": contrib.get("chips", 0) or 0,
                "mult": contrib.get("mult", 0) or 0,
                "xmult": 1 if contrib.get("xmult") is None else contrib["xmult"],
            }
        j["current_contribution"] = contrib
        jokers.append(j)
    jokers.sort(key=lambda j: j["position"])
    out["jokers"] = jokers

    out["consumables"] = [dict(c) for c in (out.get("consumables") or [])]
    out["hand_levels"] = dict(out.get("hand_levels") or {})
    out["current_hand"] = [_card(c, "hand") for c in (out.get("current_hand") or [])]

    deck = out.get("deck")
    if deck is not None:
        deck = dict(deck)
        deck["cards"] = [_card(c) for c in deck.get("cards") or []]
        deck.setdefault("total", len(deck["cards"]) or None)
        deck.setdefault("remaining", None)
    out["deck"] = deck

    blind = out.get("blind")
    if blind is not None:
        blind = dict(blind)
        for key in ("key", "name", "requirement", "effect_description", "face_down_count"):
            blind.setdefault(key, None)
        blind.setdefault("current_score", 0)
        if blind["current_score"] is None:
            blind["current_score"] = 0
    out["blind"] = blind

    shop = out.get("shop")
    if shop is not None:
        shop = dict(shop)
        shop.setdefault("reroll_cost", None)
        shop.setdefault("voucher", None)
        shop["packs"] = list(shop.get("packs") or [])
        items = []
        for raw in shop.get("items") or []:
            item = dict(raw)
            item.setdefault("name", None)
            item.setdefault("edition", "base")
            item["stickers"] = list(item.get("stickers") or [])
            items.append(item)
        shop["items"] = items
    out["shop"] = shop

    pack = out.get("pack_open")
    if pack is not None:
        pack = dict(pack)
        pack.setdefault("name", None)
        pack["cards"] = [dict(c) for c in pack.get("cards") or []]
    out["pack_open"] = pack

    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _schema_errors(state: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema())
    errors = []
    for err in sorted(validator.iter_errors(state), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{where}: {err.message}")
    return errors


def _cross_field_errors(state: dict[str, Any]) -> list[str]:
    """Checks the JSON Schema cannot express.

    Deliberately absent: any check that would flag a legal Balatro state.
    Wild cards make suit totals exceed the card count and stone cards make
    rank tallies fall short of ``deck.total``; both are correct.
    """
    errors: list[str] = []

    positions = [j.get("position") for j in state.get("jokers") or []]
    if len(positions) != len(set(positions)):
        errors.append("jokers: duplicate position values")
    slots = (state.get("resources") or {}).get("joker_slots_total")
    if slots is not None and len(positions) > slots:
        errors.append(
            f"jokers: {len(positions)} jokers held but joker_slots_total is {slots}"
        )

    hand = state.get("current_hand") or []
    hand_size = (state.get("resources") or {}).get("hand_size")
    if hand_size is not None and len(hand) > hand_size:
        errors.append(f"current_hand: {len(hand)} cards exceeds hand_size {hand_size}")

    for i, card in enumerate(hand + [c for c in ((state.get("deck") or {}).get("cards") or [])]):
        stone = card.get("enhancement") == "stone"
        if stone and card.get("rank") is not None:
            errors.append(f"card[{i}]: stone cards have no rank, got {card['rank']!r}")
        if not stone and card.get("rank") is None:
            errors.append(f"card[{i}]: rank is null but enhancement is not 'stone'")

    deck = state.get("deck")
    if deck:
        total, remaining = deck.get("total"), deck.get("remaining")
        if total is not None and remaining is not None and remaining > total:
            errors.append(f"deck: remaining {remaining} exceeds total {total}")

    phase = state.get("phase")
    if phase == "shop" and not state.get("shop"):
        errors.append("phase is 'shop' but no shop block is present")
    if phase == "pack_open" and not state.get("pack_open"):
        errors.append("phase is 'pack_open' but no pack_open block is present")
    if phase == "playing" and not hand:
        errors.append("phase is 'playing' but current_hand is empty")

    n_hand = len(hand)
    for i, cand in enumerate(state.get("candidate_plays") or []):
        idxs = cand.get("cards") or []
        if any(ix >= n_hand for ix in idxs):
            errors.append(f"candidate_plays[{i}]: card index out of range for current_hand")
        if len(set(idxs)) != len(idxs):
            errors.append(f"candidate_plays[{i}]: duplicate card index")
        if cand.get("unmodelled") and cand.get("exact", True):
            errors.append(f"candidate_plays[{i}]: unmodelled effects present but exact is true")

    return errors


def validate(state: dict[str, Any]) -> list[str]:
    """Return every problem with ``state``. Empty list means valid."""
    errors = _schema_errors(state)
    if errors:
        # Cross-field checks assume the shape already holds; running them on a
        # structurally broken document produces noise, not information.
        return errors
    return _cross_field_errors(state)


def load_state(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load, validate, and normalize a canonical state document.

    Raises :class:`StateInvalid` rather than returning something half-trusted.
    """
    if isinstance(source, (str, Path)):
        raw = json.loads(Path(source).read_text())
    else:
        raw = source
    errors = validate(raw)
    if errors:
        raise StateInvalid(errors)
    return normalize(raw)
