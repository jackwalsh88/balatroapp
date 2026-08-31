"""Per-session term glossary for beginner mode.

Spec section 5a: beginner mode glosses every term on first use in a session,
and "a per-session term glossary avoids re-explaining 'xmult' on every turn.
Track which terms have been glossed."

Terms are detected in the rendered output rather than declared by the model,
so the tracking cannot drift from what was actually said.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

__all__ = ["Glossary", "TERMS"]

# The vocabulary spec 5a lists as expert-mode-unglossed, which is exactly the
# set beginner mode must gloss.
TERMS = {
    "xmult": "a multiplier that multiplies your score rather than adding to it",
    "mult": "the number your chips get multiplied by",
    "chips": "the base points a hand is worth before the multiplier",
    "retrigger": "making a card score a second time",
    "scaling": "a joker that gets stronger as the run goes on",
    "dead slot": "a joker that is currently contributing nothing",
    "interest cap": "the most interest you can earn per round, however much money you hold",
    "sell value": "the money you get back for selling a joker",
    "pool": "the set of items the game can offer you",
    "seed": "the number that determines a run's randomness",
    "ante": "a set of three blinds; clearing all three moves you up one",
    "blind": "the score target you have to beat this round",
    "eternal": "a sticker meaning the joker can never be sold or destroyed",
    "perishable": "a sticker meaning the joker stops working after a few rounds",
    "enhancement": "a modification to a playing card, like Gold or Steel",
    "edition": "a shiny finish on a card or joker that adds to scoring",
}

_WORD = re.compile(r"[a-z][a-z ]*")


class Glossary:
    """Tracks which terms have been explained this session.

    Persisted to disk so a session survives separate CLI invocations, which is
    how the manual adapter is actually used - one command per decision, not one
    long-running process.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._glossed: set[str] = set()
        if self.path and self.path.exists():
            try:
                self._glossed = set(json.loads(self.path.read_text()))
            except (json.JSONDecodeError, OSError):
                self._glossed = set()

    @property
    def glossed(self) -> list[str]:
        return sorted(self._glossed)

    def pending(self) -> list[str]:
        """Terms not yet explained."""
        return sorted(set(TERMS) - self._glossed)

    def note_used(self, text: str) -> list[str]:
        """Mark every known term appearing in ``text`` as glossed.

        Returns the terms newly marked, so a caller can report what was
        explained this turn.
        """
        lowered = (text or "").lower()
        newly = [
            term for term in TERMS
            if term not in self._glossed and re.search(rf"\b{re.escape(term)}\b", lowered)
        ]
        self._glossed.update(newly)
        self._save()
        return newly

    def reset(self) -> None:
        self._glossed.clear()
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._glossed), indent=2))
