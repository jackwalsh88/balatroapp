"""Deterministic scorer. Pure code, no LLM, no estimates.

Spec section 1: arithmetic and judgment are separate stages, because a language
model asked to estimate a score produced 36,000 against an actual 20,700. This
module is the arithmetic stage. It computes what it can compute exactly and is
loudly honest about what it cannot.

The honesty mechanism is :attr:`ScoreResult.exact`. An effect the scorer cannot
model - an unknown joker, a copy chain, a random trigger, a scaling joker whose
counter the adapter did not read - does not silently contribute zero. It sets
``exact = False`` and lands in ``unmodelled``, and every downstream stage
(advisor prompt, validator, output) treats a non-exact score as a number that
may not be quoted as fact. Silently scoring an unknown joker as zero would
reproduce the exact failure this architecture exists to prevent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import data
from .handtype import RANK_VALUE, Classification, HandFlags, classify

__all__ = ["ScoreResult", "score_play", "ScoringContext"]

_EVEN = frozenset({"2", "4", "6", "8", "10"})
_ODD = frozenset({"A", "3", "5", "7", "9"})


@dataclass
class ScoreResult:
    hand_type: str
    scoring_indices: list[int]
    base_chips: float
    base_mult: float
    chips: float
    mult: float
    score: int
    exact: bool = True
    unmodelled: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    gold_forfeited: int = 0
    steel_forfeited: int = 0
    steps: list[str] = field(default_factory=list)

    def as_candidate(self, cards: list[int], clears_blind: bool | None) -> dict[str, Any]:
        """Serialize into the ``candidate_play`` shape from the schema."""
        return {
            "cards": list(cards),
            "hand_type": self.hand_type,
            "chips": round(self.chips, 4),
            "mult": round(self.mult, 4),
            "score": self.score,
            "clears_blind": clears_blind,
            "gold_forfeited": self.gold_forfeited,
            "steel_forfeited": self.steel_forfeited,
            "triggers": list(self.triggers),
            "exact": self.exact,
            "unmodelled": list(self.unmodelled),
        }


@dataclass
class ScoringContext:
    """Everything an effect might read, resolved once per state."""

    money: int
    hands_remaining: int
    discards_remaining: int
    joker_count: int
    joker_slots_total: int
    deck_remaining: int | None
    steel_in_deck: int | None
    enhanced_in_deck: int | None
    hand_levels: dict[str, Any]
    hand_played_count: int | None = None
    """How many times the hand type being scored has been played this run.
    Depends on the classification, so it is filled in per candidate play."""

    def source(self, name: str, counter: float | None) -> float | None:
        """Resolve an effect value source. None means 'cannot be known'."""
        if name == "counter":
            return counter
        if name == "money":
            return self.money
        if name == "hands_remaining":
            return self.hands_remaining
        if name == "discards_remaining":
            return self.discards_remaining
        if name == "joker_count":
            return self.joker_count
        if name == "empty_joker_slots_incl_self":
            return max(0, self.joker_slots_total - self.joker_count) + 1
        if name == "deck_remaining":
            return self.deck_remaining
        if name == "steel_in_deck":
            return self.steel_in_deck
        if name == "enhanced_in_deck":
            return self.enhanced_in_deck
        if name == "hand_played_count":
            return self.hand_played_count
        return None


def _context(state: dict[str, Any]) -> ScoringContext:
    run, res = state["run"], state["resources"]
    deck = state.get("deck")
    deck_remaining = steel = enhanced = None
    if deck:
        cards = deck.get("cards") or []
        if deck.get("remaining") is not None:
            deck_remaining = deck["remaining"]
        elif cards:
            deck_remaining = sum(1 for c in cards if c.get("location") == "deck")
        if cards:
            steel = sum(1 for c in cards if c.get("enhancement") == "steel")
            enhanced = sum(
                1 for c in cards if c.get("enhancement") not in (None, "none")
            )
    return ScoringContext(
        money=run["money"],
        hands_remaining=res["hands_remaining"],
        discards_remaining=res["discards_remaining"],
        joker_count=len(state.get("jokers") or []),
        joker_slots_total=res["joker_slots_total"],
        deck_remaining=deck_remaining,
        steel_in_deck=steel,
        enhanced_in_deck=enhanced,
        hand_levels=state.get("hand_levels") or {},
    )


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------


def _is_face(card: dict[str, Any], pareidolia: bool) -> bool:
    if card.get("enhancement") == "stone":
        return False
    if pareidolia:
        return True
    return card.get("rank") in data.FACE_RANKS


def _card_suits(card: dict[str, Any], smeared: bool) -> set[str]:
    if card.get("enhancement") == "stone":
        return set()
    if card.get("enhancement") == "wild":
        return set(data.SUITS)
    suit = card.get("suit")
    if suit is None:
        return set()
    if smeared:
        pairing = {
            "hearts": {"hearts", "diamonds"},
            "diamonds": {"hearts", "diamonds"},
            "spades": {"spades", "clubs"},
            "clubs": {"spades", "clubs"},
        }
        return pairing[suit]
    return {suit}


def _card_matches(pred: dict[str, Any], card: dict[str, Any], flags: HandFlags) -> bool:
    if "suit" in pred and not (set(pred["suit"]) & _card_suits(card, flags.smeared)):
        return False
    if "rank" in pred and card.get("rank") not in pred["rank"]:
        return False
    cls = pred.get("rank_class")
    if cls == "face" and not _is_face(card, flags.pareidolia):
        return False
    if cls == "even" and card.get("rank") not in _EVEN:
        return False
    if cls == "odd" and card.get("rank") not in _ODD:
        return False
    if cls == "ace" and card.get("rank") != "A":
        return False
    if cls == "number" and card.get("rank") in data.FACE_RANKS | {"A"}:
        return False
    if "enhancement" in pred and card.get("enhancement") not in pred["enhancement"]:
        return False
    if "seal" in pred and card.get("seal") not in pred["seal"]:
        return False
    if "edition" in pred and card.get("edition") not in pred["edition"]:
        return False
    return True


def _independent_holds(
    pred: dict[str, Any],
    cls: Classification,
    played: list[dict[str, Any]],
    held: list[dict[str, Any]],
    ctx: ScoringContext,
    flags: HandFlags,
) -> bool:
    if "hand_contains" in pred and not (set(pred["hand_contains"]) & cls.contains):
        return False
    if "max_played" in pred and len(played) > pred["max_played"]:
        return False
    if "discards_remaining" in pred and ctx.discards_remaining != pred["discards_remaining"]:
        return False
    if "hands_remaining" in pred and ctx.hands_remaining != pred["hands_remaining"]:
        return False
    if "all_held_suit" in pred:
        wanted = set(pred["all_held_suit"])
        if not held:
            return False
        for card in held:
            if card.get("enhancement") == "stone":
                continue  # no suit; does not break the condition
            if not (_card_suits(card, flags.smeared) & wanted):
                return False
    return True


def _resolve(spec: Any, ctx: ScoringContext, counter: float | None) -> float | None:
    """A literal number, or {from, scale, base}. None means unknowable."""
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    value = ctx.source(spec["from"], counter)
    if value is None:
        return None
    return spec.get("base", 0.0) + spec.get("scale", 1.0) * value


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


class _Accumulator:
    def __init__(self, chips: float, mult: float, steps: list[str]) -> None:
        self.chips = chips
        self.mult = mult
        self.steps = steps

    def add(self, chips: float = 0.0, mult: float = 0.0, xmult: float = 1.0, why: str = "") -> None:
        if not chips and not mult and xmult == 1.0:
            return
        self.chips += chips
        self.mult += mult
        self.mult *= xmult
        if why:
            parts = []
            if chips:
                parts.append(f"+{chips:g} chips")
            if mult:
                parts.append(f"+{mult:g} mult")
            if xmult != 1.0:
                parts.append(f"x{xmult:g} mult")
            self.steps.append(f"{why}: {', '.join(parts)} -> {self.chips:g} x {self.mult:g}")


def _base_values(state: dict[str, Any], hand_type: str) -> tuple[float, float, int]:
    """Base chips/mult for the hand at its current level."""
    level_info = (state.get("hand_levels") or {}).get(hand_type)
    if level_info:
        return float(level_info["chips"]), float(level_info["mult"]), int(level_info["level"])
    table = data.hand_table()[hand_type]
    return float(table["base_chips"]), float(table["base_mult"]), 1


def _level_values(hand_type: str, level: int) -> tuple[float, float]:
    table = data.hand_table()[hand_type]
    level = max(1, level)
    return (
        table["base_chips"] + (level - 1) * table["chips_per_level"],
        table["base_mult"] + (level - 1) * table["mult_per_level"],
    )


def score_play(
    state: dict[str, Any],
    played_indices: list[int],
    *,
    hand: list[dict[str, Any]] | None = None,
) -> ScoreResult:
    """Score one candidate play exactly.

    ``played_indices`` index into ``state['current_hand']`` (or ``hand`` when
    scoring a hypothetical hand, as the discard enumerator does). Order is the
    play order, which matters: several jokers only fire on the first scored card.
    """
    source_hand = hand if hand is not None else state.get("current_hand") or []
    played = [source_hand[i] for i in played_indices]
    held = [c for i, c in enumerate(source_hand) if i not in set(played_indices)]

    joker_states = state.get("jokers") or []
    flags = HandFlags.from_jokers(joker_states)
    cls = classify(played, flags)

    unmodelled: list[str] = []
    triggers: list[str] = []
    steps: list[str] = []

    blind_entry = data.blind((state.get("blind") or {}).get("key"))
    effect = (blind_entry or {}).get("scoring_effect")
    debuff_suit = (blind_entry or {}).get("scoring_arg") if effect == "debuff_suit" else None

    base_chips, base_mult, level = _base_values(state, cls.hand_type)
    printed_base = f"{cls.hand_type} (level {level}) base: {base_chips:g} x {base_mult:g}"
    if effect == "level_down":
        base_chips, base_mult = _level_values(cls.hand_type, level - 1)
        steps.append(f"{blind_entry['name']}: hand level {level} -> {max(1, level - 1)}")
    if effect == "halve_base":
        base_chips, base_mult = base_chips / 2, base_mult / 2
        steps.append(f"{blind_entry['name']}: base halved to {base_chips:g} x {base_mult:g}")

    acc = _Accumulator(base_chips, base_mult, steps)
    steps.insert(0, printed_base)

    ctx = _context(state)
    ctx.hand_played_count = (ctx.hand_levels.get(cls.hand_type) or {}).get("played")
    rank_chips = data.rank_chips()
    enh_table = data.enhancement_table()
    ed_table = data.edition_table()

    def _debuffed(card: dict[str, Any]) -> bool:
        if debuff_suit and debuff_suit in _card_suits(card, flags.smeared):
            return True
        if effect == "debuff_face" and _is_face(card, flags.pareidolia):
            return True
        return False

    # Static entries for held jokers, and the ones we cannot model at all.
    entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for js in joker_states:
        entry = data.joker(js["key"])
        if entry is None:
            unmodelled.append(js["key"])
            steps.append(f"UNKNOWN joker {js['key']}: contribution unknown")
        elif entry.get("unmodelled"):
            unmodelled.append(js["key"])
            steps.append(f"{entry['name']}: unmodelled - {entry.get('reason', '')}")
        entries.append((js, entry))

    # ---- retrigger counts -------------------------------------------------
    def _extra_triggers(card: dict[str, Any], when: str, position: int | None) -> int:
        extra = 0
        if when == "scored" and card.get("seal") == "red":
            extra += 1
        for js, entry in entries:
            if not entry or entry.get("unmodelled"):
                continue
            for eff in entry.get("effects", []):
                if eff.get("when") != f"retrigger_{when}":
                    continue
                pred = dict(eff.get("if") or {})
                if "hands_remaining" in pred and ctx.hands_remaining != pred.pop("hands_remaining"):
                    continue
                if pred.get("first_only") and position != 0:
                    continue
                pred.pop("first_only", None)
                if pred and not _card_matches(pred, card, flags):
                    continue
                extra += eff.get("times", 1)
                if entry["name"] not in triggers:
                    triggers.append(entry["name"])
        return extra

    # ---- scored cards, left to right --------------------------------------
    first_match_pos: dict[int, int] = {}
    gold_forfeited = 0
    steel_forfeited = 0

    steel_forfeited = sum(1 for c in played if c.get("enhancement") == "steel")

    for position, idx in enumerate(cls.scoring):
        card = played[idx]
        if card.get("enhancement") == "gold":
            gold_forfeited += enh_table["gold"]["money_if_held_at_round_end"]
        if _debuffed(card):
            steps.append(f"card {_card_label(card)}: debuffed by {blind_entry['name']}, scores nothing")
            continue
        if card.get("enhancement") == "lucky":
            unmodelled.append("lucky_card")
            steps.append(f"card {_card_label(card)}: Lucky - random trigger, not scored")

        repeats = 1 + _extra_triggers(card, "scored", position)
        for _ in range(repeats):
            chips = 0.0
            if card.get("rank"):
                chips += rank_chips[card["rank"]]
            enh = enh_table.get(card.get("enhancement", "none"), {})
            acc.add(
                chips=chips + enh.get("chips", 0),
                mult=enh.get("mult", 0),
                xmult=enh.get("xmult", 1) if card.get("enhancement") != "steel" else 1,
                why=f"card {_card_label(card)}",
            )
            ed = ed_table.get(card.get("edition", "base"), {})
            acc.add(
                chips=ed.get("chips", 0),
                mult=ed.get("mult", 0),
                xmult=ed.get("xmult", 1),
                why=f"card {_card_label(card)} {card.get('edition')}",
            )
            _apply_card_effects(
                acc, entries, card, "on_scored", position, first_match_pos,
                ctx, flags, triggers, unmodelled,
            )

    # ---- cards held in hand ----------------------------------------------
    for position, card in enumerate(held):
        if _debuffed(card):
            continue
        repeats = 1 + _extra_triggers(card, "held", position)
        for _ in range(repeats):
            if card.get("enhancement") == "steel":
                acc.add(xmult=enh_table["steel"]["xmult"], why=f"held {_card_label(card)} steel")
            _apply_card_effects(
                acc, entries, card, "on_held", position, first_match_pos,
                ctx, flags, triggers, unmodelled,
            )

    # ---- jokers, left to right -------------------------------------------
    for js, entry in entries:
        if entry is None or entry.get("unmodelled"):
            continue
        counter = (js.get("internal_state") or {}).get("counter")
        for eff in entry.get("effects", []):
            if eff.get("when") != "independent":
                continue
            pred = eff.get("if") or {}
            if pred and not _independent_holds(pred, cls, played, held, ctx, flags):
                continue
            if eff.get("special") == "raised_fist":
                ranked = [c for c in held if c.get("rank")]
                if not ranked:
                    continue
                lowest = min(ranked, key=lambda c: RANK_VALUE[c["rank"]])
                acc.add(mult=2 * rank_chips[lowest["rank"]], why=entry["name"])
                triggers.append(entry["name"])
                continue

            chips = _resolve(eff.get("chips"), ctx, counter)
            mult = _resolve(eff.get("mult"), ctx, counter)
            xmult = _resolve(eff.get("xmult"), ctx, counter)
            if _needs_unknown(eff, chips, mult, xmult):
                unmodelled.append(js["key"])
                steps.append(
                    f"{entry['name']}: effect depends on a value the state does not "
                    f"provide (counter or deck composition), contribution unknown"
                )
                continue

            acc.add(
                chips=chips or 0.0,
                mult=mult or 0.0,
                xmult=1.0 if xmult is None else xmult,
                why=entry["name"],
            )
            if entry["name"] not in triggers:
                triggers.append(entry["name"])

        ed = ed_table.get(js.get("edition", "base"), {})
        acc.add(
            chips=ed.get("chips", 0),
            mult=ed.get("mult", 0),
            xmult=ed.get("xmult", 1),
            why=f"{entry['name']} ({js.get('edition')})",
        )

    # Observatory multiplies a held Planet's own hand type; the scorer declares
    # rather than guesses (data/vouchers.json marks it affects_scoring).
    if "v_observatory" in (state["run"].get("vouchers_redeemed") or []) and state.get("consumables"):
        unmodelled.append("v_observatory")
        steps.append("Observatory is redeemed and consumables are held: its X1.5 is not modelled")

    exact = not unmodelled
    score = math.floor(round(acc.chips * acc.mult, 6))

    return ScoreResult(
        hand_type=cls.hand_type,
        scoring_indices=cls.scoring,
        base_chips=base_chips,
        base_mult=base_mult,
        chips=acc.chips,
        mult=acc.mult,
        score=score,
        exact=exact,
        unmodelled=sorted(set(unmodelled)),
        triggers=triggers,
        gold_forfeited=gold_forfeited,
        steel_forfeited=steel_forfeited,
        steps=steps,
    )


def _needs_unknown(
    eff: dict[str, Any], chips: float | None, mult: float | None, xmult: float | None
) -> bool:
    """True when the effect declared a value we could not resolve."""
    for key, value in (("chips", chips), ("mult", mult), ("xmult", xmult)):
        if key in eff and value is None:
            return True
    return False


def _apply_card_effects(
    acc: _Accumulator,
    entries: list[tuple[dict[str, Any], dict[str, Any] | None]],
    card: dict[str, Any],
    when: str,
    position: int,
    first_match_pos: dict[int, int],
    ctx: ScoringContext,
    flags: HandFlags,
    triggers: list[str],
    unmodelled: list[str],
) -> None:
    for slot, (js, entry) in enumerate(entries):
        if entry is None or entry.get("unmodelled"):
            continue
        counter = (js.get("internal_state") or {}).get("counter")
        for eff in entry.get("effects", []):
            if eff.get("when") != when:
                continue
            pred = dict(eff.get("if") or {})
            first_only = pred.pop("first_only", False)
            if pred and not _card_matches(pred, card, flags):
                continue
            if first_only:
                # The first MATCHING card claims the effect. Retriggers of that
                # same card fire it again; a later matching card never does.
                claimed = first_match_pos.setdefault(slot, position)
                if claimed != position:
                    continue

            chips = _resolve(eff.get("chips"), ctx, counter)
            mult = _resolve(eff.get("mult"), ctx, counter)
            xmult = _resolve(eff.get("xmult"), ctx, counter)
            if _needs_unknown(eff, chips, mult, xmult):
                unmodelled.append(js["key"])
                continue
            acc.add(
                chips=chips or 0.0,
                mult=mult or 0.0,
                xmult=1.0 if xmult is None else xmult,
                why=f"{entry['name']} on {_card_label(card)}",
            )
            if entry["name"] not in triggers:
                triggers.append(entry["name"])


_SUIT_GLYPH = {"hearts": "H", "diamonds": "D", "clubs": "C", "spades": "S"}


def _card_label(card: dict[str, Any]) -> str:
    if card.get("enhancement") == "stone":
        return "Stone"
    return f"{card.get('rank')}{_SUIT_GLYPH.get(card.get('suit'), '?')}"
