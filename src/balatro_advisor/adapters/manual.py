"""Manual entry adapter.

Spec section 7. Lowest fidelity, zero dependencies, and per section 0 the only
mode that runs on the constrained machine. Section 0's recommendation is to
build it first and treat it as the reference implementation, which is what this
project does.

Three things make it usable rather than merely possible:

- **Run state persists between invocations.** Ante, money, jokers, hand levels
  and deck do not change between one decision and the next, so each round only
  asks for deltas. Retyping five jokers every hand is how a tool like this
  stops being used by round three.
- **Jokers are fuzzy-matched** against the static table, so "blue joker",
  "blue" and "j_blue_joker" all land.
- **Scaling jokers prompt for their counter.** The scorer refuses to guess it
  (a missing counter degrades to ``exact: false``), so the adapter has to ask.
  This is the one place where the honesty rule costs the user typing, and it is
  worth it.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable

from ..core import cards as card_utils
from ..core import data, schema

__all__ = ["ManualSession", "match_joker", "DEFAULT_SESSION_PATH"]

DEFAULT_SESSION_PATH = Path(".balatro-session.json")

Ask = Callable[[str], str]


def match_joker(text: str, limit: int = 5) -> list[dict[str, Any]]:
    """Fuzzy-match free text against the joker table, best first.

    Exact key and exact name win outright; otherwise it is a substring pass
    followed by difflib on the names. Returning a ranked list rather than a
    single guess lets the caller disambiguate instead of silently picking wrong
    - a misidentified joker is a wrong score, which is the failure mode this
    whole project is organized against.
    """
    needle = (text or "").strip().lower()
    if not needle:
        return []

    table = data.jokers()
    if needle in table:
        return [table[needle]]

    by_name = {j["name"].lower(): j for j in table.values()}
    if needle in by_name:
        return [by_name[needle]]

    word = re.compile(rf"\b{re.escape(needle)}\b")

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for joker in table.values():
        name = joker["name"].lower()
        if word.search(name):
            # A whole-word hit beats a longer prefix hit: "blue" means Blue
            # Joker, not Blueprint, even though Blueprint is the shorter name.
            score = 3.0
        elif name.startswith(needle):
            score = 2.0
        elif needle in name:
            score = 1.5
        elif needle in joker["key"].lower():
            score = 1.2
        else:
            ratio = difflib.SequenceMatcher(None, needle, name).ratio()
            if ratio <= 0.6:
                continue
            score = ratio
        # Shorter names break ties, so "joker" prefers Joker over Jolly Joker.
        scored.append((score, -len(name), joker))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [joker for _, _, joker in scored[:limit]]


def _effect_sources(entry: dict[str, Any]) -> set[str]:
    """Which context values this joker's effects read."""
    sources = set()
    for effect in entry.get("effects", []):
        for key in ("chips", "mult", "xmult"):
            spec = effect.get(key)
            if isinstance(spec, dict) and spec.get("from"):
                sources.add(spec["from"])
    return sources


def _needs_counter(entry: dict[str, Any]) -> bool:
    """True when an effect reads internal_state.counter."""
    return "counter" in _effect_sources(entry)


# Sources the manual adapter can obtain by asking a single question. Anything
# outside this set (steel_in_deck, enhanced_in_deck) needs the full deck list,
# which manual entry does not collect - those jokers stay honestly unmodelled.
_ASKABLE = {"deck_remaining"}


class ManualSession:
    """Builds canonical state from prompts, remembering what does not change.

    ``ask`` is injected so the whole flow is testable without a terminal.
    """

    def __init__(
        self,
        path: str | Path | None = DEFAULT_SESSION_PATH,
        ask: Ask | None = None,
        out: Callable[[str], None] = print,
    ) -> None:
        self.path = Path(path) if path else None
        self.ask = ask or input
        self.out = out
        self.state: dict[str, Any] = self._load() or self._blank()

    # -- persistence -------------------------------------------------------

    def _blank(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "seq": 0,
            "source": "manual",
            "captured_at": None,
            "phase": "playing",
            "run": {"ante": 1, "ante_max": 8, "round": 1, "money": 4,
                    "deck_name": None, "stake": None, "vouchers_redeemed": []},
            "resources": {"hands_remaining": 4, "discards_remaining": 3,
                          "hand_size": 8, "joker_slots_total": 5,
                          "consumable_slots_total": 2},
            "jokers": [],
            "consumables": [],
            "hand_levels": {},
            "current_hand": [],
            "deck": None,
            "blind": None,
            "shop": None,
            "pack_open": None,
        }

    def _load(self) -> dict[str, Any] | None:
        if not self.path or not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.path)

    def reset(self) -> None:
        self.state = self._blank()
        self.save()

    # -- prompting helpers -------------------------------------------------

    def _prompt(self, label: str, current: Any = None, parser: Callable[[str], Any] | None = None) -> Any:
        """Ask, showing the remembered value; blank input keeps it."""
        suffix = f" [{current}]" if current not in (None, "") else ""
        while True:
            raw = self.ask(f"{label}{suffix}: ").strip()
            if not raw:
                return current
            if parser is None:
                return raw
            try:
                return parser(raw)
            except (ValueError, card_utils.CardSyntaxError) as exc:
                self.out(f"  ! {exc}")

    def _prompt_int(self, label: str, current: int | None) -> int | None:
        return self._prompt(label, current, int)

    # -- the round flow ----------------------------------------------------

    def collect(self, *, phase: str | None = None) -> dict[str, Any]:
        """Walk the player through one decision point and return valid state."""
        state = self.state
        state["seq"] = int(state.get("seq", 0)) + 1

        state["phase"] = phase or self._prompt(
            "Phase (playing/shop/blind_select/pack_open)", state.get("phase", "playing")
        )

        run, res = state["run"], state["resources"]
        run["ante"] = self._prompt_int("Ante", run.get("ante"))
        run["money"] = self._prompt_int("Money ($)", run.get("money"))

        if state["phase"] == "playing":
            res["hands_remaining"] = self._prompt_int("Hands remaining", res.get("hands_remaining"))
            res["discards_remaining"] = self._prompt_int("Discards remaining", res.get("discards_remaining"))
            self._collect_blind(state)
            self._collect_hand(state)
            self._collect_hand_levels(state)

        if self._yes(f"Update jokers? ({len(state['jokers'])} remembered)", default=not state["jokers"]):
            self._collect_jokers(state)

        self._collect_deck_if_needed(state)

        if state["phase"] == "shop":
            self._collect_shop(state)
        if state["phase"] == "pack_open":
            self._collect_pack(state)

        errors = schema.validate(state)
        if errors:
            self.out("\nThat state is not valid, so no advice will be given on it:")
            for error in errors:
                self.out(f"  - {error}")
            raise schema.StateInvalid(errors)

        self.save()
        return schema.normalize(state)

    def _yes(self, question: str, default: bool = False) -> bool:
        hint = "Y/n" if default else "y/N"
        raw = self.ask(f"{question} [{hint}]: ").strip().lower()
        if not raw:
            return default
        return raw.startswith("y")

    def _collect_blind(self, state: dict[str, Any]) -> None:
        blind = state.get("blind") or {"type": "small"}
        blind["type"] = self._prompt("Blind type (small/big/boss)", blind.get("type", "small"))
        if blind["type"] == "boss":
            raw = self._prompt("Boss blind name or key (blank = unknown)", blind.get("key"))
            entry = self._match_blind(raw) if raw else None
            if entry:
                blind["key"], blind["name"] = entry["key"], entry["name"]
                blind["effect_description"] = entry["description"]
                self.out(f"  -> {entry['name']}: {entry['description']}")
            else:
                blind["key"] = blind["name"] = None
        else:
            blind["key"] = f"bl_{blind['type']}"
            blind["name"] = f"{blind['type'].title()} Blind"
        blind["requirement"] = self._prompt_int("Score to beat", blind.get("requirement"))
        blind["current_score"] = self._prompt_int("Score so far this round", blind.get("current_score") or 0) or 0
        state["blind"] = blind

    @staticmethod
    def _match_blind(text: str) -> dict[str, Any] | None:
        needle = text.strip().lower()
        table = data.blinds()
        if needle in table:
            return table[needle]
        best, best_ratio = None, 0.0
        for entry in table.values():
            if entry["type"] != "boss":
                continue
            name = entry["name"].lower()
            ratio = 1.0 if needle in name else difflib.SequenceMatcher(None, needle, name).ratio()
            if ratio > best_ratio:
                best, best_ratio = entry, ratio
        return best if best_ratio > 0.6 else None

    def _collect_hand(self, state: dict[str, Any]) -> None:
        current = card_utils.format_hand(state.get("current_hand") or [], verbose=True)
        self.out(
            "\nCards in hand. Shorthand: KH QD 10S 4D. Add modifiers with a colon:"
            "\n  KH:gold  5S:steel  AH:polychrome  QD:redseal  stone"
        )
        hand = self._prompt("Hand", current, lambda text: card_utils.parse_hand(text, "hand"))
        if isinstance(hand, str):  # unchanged: reparse the remembered value
            hand = card_utils.parse_hand(hand, "hand")
        state["current_hand"] = hand or []
        if state["current_hand"]:
            self.out(f"  -> {card_utils.format_hand(state['current_hand'], index=True)}")

    def _collect_hand_levels(self, state: dict[str, Any]) -> None:
        if not self._yes("Set poker hand levels? (affects every score)", default=False):
            return
        self.out("  Blank to skip a hand. Levels come from Planet cards.")
        table = data.hand_table()
        for hand_type in schema.HAND_TYPES:
            existing = (state["hand_levels"].get(hand_type) or {}).get("level")
            level = self._prompt_int(f"  {hand_type} level", existing)
            if not level:
                continue
            spec = table[hand_type]
            state["hand_levels"][hand_type] = {
                "level": level,
                "chips": spec["base_chips"] + (level - 1) * spec["chips_per_level"],
                "mult": spec["base_mult"] + (level - 1) * spec["mult_per_level"],
                "played": (state["hand_levels"].get(hand_type) or {}).get("played"),
            }

    def _collect_jokers(self, state: dict[str, Any]) -> None:
        self.out("\nJokers, left to right. Blank line when done. '?' lists nothing - just type a name.")
        jokers: list[dict[str, Any]] = []
        position = 0
        while True:
            raw = self.ask(f"  Joker {position} (blank to finish): ").strip()
            if not raw:
                break
            matches = match_joker(raw)
            if not matches:
                if not self._yes(f"    No match for {raw!r}. Record it as an unknown joker?", default=True):
                    continue
                entry = {"key": raw, "name": raw, "description": None}
            elif len(matches) == 1:
                entry = matches[0]
            else:
                self.out("    Which one?")
                for i, candidate in enumerate(matches):
                    self.out(f"      {i}) {candidate['name']} - {candidate['description']}")
                choice = self._prompt_int("    Number", 0) or 0
                entry = matches[min(choice, len(matches) - 1)]

            joker: dict[str, Any] = {
                "position": position,
                "key": entry["key"],
                "name": entry["name"],
                "edition": "base",
                "stickers": [],
                "sell_value": None,
                "internal_state": {"counter": None},
                "current_contribution": None,
            }
            if entry.get("description"):
                self.out(f"    -> {entry['name']}: {entry['description']}")

            edition = self.ask("    Edition (base/foil/holographic/polychrome/negative) [base]: ").strip().lower()
            if edition in ("foil", "holographic", "polychrome", "negative"):
                joker["edition"] = edition
            stickers = self.ask("    Stickers (eternal/perishable/rental, comma separated) []: ").strip().lower()
            joker["stickers"] = [
                s.strip() for s in stickers.split(",")
                if s.strip() in ("eternal", "perishable", "rental")
            ]

            if entry.get("key") in data.jokers() and _needs_counter(data.jokers()[entry["key"]]):
                self.out(
                    "    This joker scales. Type the number the game shows on it right now"
                    "\n    (for an Xmult joker that is the multiplier itself, e.g. 2.4)."
                    "\n    Leave blank and its contribution will be reported as unknown"
                    "\n    rather than guessed."
                )
                raw_counter = self.ask("    Current value: ").strip()
                if raw_counter:
                    try:
                        joker["internal_state"]["counter"] = float(raw_counter)
                    except ValueError:
                        self.out("    ! not a number; leaving it unknown")

            jokers.append(joker)
            position += 1

        state["jokers"] = jokers

    def _collect_deck_if_needed(self, state: dict[str, Any]) -> None:
        """Ask for deck size only when a held joker's score depends on it.

        Blue Joker is common and reads cards-remaining, so without this a very
        ordinary board scores as a floor forever. Asking unconditionally would
        be one more question every round for the majority of boards that do not
        need it.
        """
        wanted: set[str] = set()
        for joker in state.get("jokers") or []:
            entry = data.joker(joker["key"])
            if entry:
                wanted |= _effect_sources(entry)
        if not (wanted & _ASKABLE):
            return

        names = ", ".join(
            data.joker_name(j["key"]) for j in state["jokers"]
            if (data.joker(j["key"]) or {}) and
            "deck_remaining" in _effect_sources(data.joker(j["key"]) or {})
        )
        deck = state.get("deck") or {"total": None, "remaining": None, "cards": []}
        self.out(f"\n{names} scores from your deck size.")
        deck["remaining"] = self._prompt_int("Cards remaining in deck", deck.get("remaining"))
        deck["cards"] = deck.get("cards") or []
        state["deck"] = deck if deck["remaining"] is not None else None

    def _collect_shop(self, state: dict[str, Any]) -> None:
        shop: dict[str, Any] = {"reroll_cost": None, "items": [], "voucher": None, "packs": []}
        shop["reroll_cost"] = self._prompt_int("Reroll cost", 5)
        self.out("Shop items. Blank name to finish.")
        slot = 0
        while True:
            raw = self.ask(f"  Slot {slot} name (blank to finish): ").strip()
            if not raw:
                break
            matches = match_joker(raw)
            entry = matches[0] if matches else None
            kind = "joker" if entry else self._prompt("    Kind (joker/consumable/playing_card)", "consumable")
            price = self._prompt_int("    Price", (entry or {}).get("cost", 4))
            item = {
                "slot": slot,
                "kind": kind,
                "key": (entry or {}).get("key", raw),
                "name": (entry or {}).get("name", raw),
                "price": price,
                "edition": "base",
                "stickers": [],
            }
            stickers = self.ask("    Stickers (eternal/perishable/rental) []: ").strip().lower()
            item["stickers"] = [
                s.strip() for s in stickers.split(",")
                if s.strip() in ("eternal", "perishable", "rental")
            ]
            if entry:
                self.out(f"    -> {entry['name']}: {entry['description']}")
            shop["items"].append(item)
            slot += 1
        state["shop"] = shop
        state["blind"] = None

    def _collect_pack(self, state: dict[str, Any]) -> None:
        key = self._prompt("Pack key (e.g. p_celestial_jumbo)", "p_celestial_jumbo")
        self.out(
            "How many cards may you pick? Read it off the game - it is NOT derivable"
            "\nfrom pack size. Jumbo packs hold more cards but still allow one pick;"
            "\nMega allows two."
        )
        allowed = self._prompt_int("Picks allowed", 1) or 1
        raw = self._prompt("Cards in the pack (comma separated names)", "")
        state["pack_open"] = {
            "key": key,
            "name": None,
            "choices_allowed": allowed,
            "cards": [
                {"key": name.strip(), "name": name.strip()}
                for name in str(raw or "").split(",") if name.strip()
            ],
        }
        state["blind"] = None
