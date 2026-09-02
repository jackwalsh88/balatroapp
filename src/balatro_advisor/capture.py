"""Turn a played hand into a captured fixture.

This closes the gap the project has carried from the start. Every fixture so
far is `hand_computed`: the arithmetic was worked out by hand and checked
against the scorer, which proves the scorer is self-consistent and cannot prove
it matches the game. Both could be wrong the same way.

A captured fixture is different in kind. The score comes from the game, so it
is ground truth the scorer had no hand in producing.

The consequence is deliberate and worth stating plainly: **a captured fixture
that disagrees with the scorer makes the test suite fail, and that failure is
correct.** It means a real scoring bug has been found. The alternative -
recording the scorer's own answer as the expectation - would produce a fixture
that can never fail and therefore never tells you anything.

What is recorded, and why each field:

    expected_score      what the GAME awarded. The only authority here.
    expected_chips/mult what the game displayed, if the player read them off.
                        Optional: the score alone is enough to catch an error,
                        and these narrow down where it is.
    scorer_said         what the scorer predicted AT CAPTURE TIME. Never an
                        expectation - a record, so a later fix can be seen to
                        have changed the disagreement.
    agrees_at_capture   whether they matched when captured.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import schema, scorer

__all__ = ["CaptureError", "build_fixture", "write_fixture", "suggest_name"]

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class CaptureError(RuntimeError):
    """The log entry cannot become a fixture, and why."""


def suggest_name(state: dict[str, Any], hand_type: str, score: int) -> str:
    """A readable, collision-resistant fixture name."""
    ante = (state.get("run") or {}).get("ante")
    blind = (state.get("blind") or {}).get("key") or "no_blind"
    parts = ["captured", f"ante{ante}", blind.replace("bl_", ""), hand_type, str(score)]
    slug = "_".join(str(p) for p in parts if p not in (None, ""))
    return re.sub(r"[^a-z0-9_]+", "_", slug.lower()).strip("_")


def build_fixture(
    entry: dict[str, Any],
    *,
    actual_score: int,
    actual_chips: float | None = None,
    actual_mult: float | None = None,
    name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build a captured fixture from a decision-log entry and the real score."""
    action = (entry.get("advice") or {}).get("action") or {}
    if action.get("kind") != "play":
        raise CaptureError(
            f"entry {entry.get('id')} recommended '{action.get('kind')}', not a play. "
            f"Only a played hand has a score the game can confirm."
        )
    cards = action.get("cards") or []
    if not cards:
        raise CaptureError(f"entry {entry.get('id')} names no cards")

    state = entry.get("state")
    if not state:
        raise CaptureError(f"entry {entry.get('id')} carries no state")

    errors = schema.validate(state)
    if errors:
        raise CaptureError(
            "the logged state is not valid, so a fixture built from it would be "
            "worthless: " + "; ".join(errors[:3])
        )

    loaded = schema.load_state(state)
    if any(c >= len(loaded.get("current_hand") or []) for c in cards):
        raise CaptureError(f"played cards {cards} are not all in the logged hand")

    predicted = scorer.score_play(loaded, cards)
    agrees = predicted.score == actual_score

    fixture: dict[str, Any] = {
        "name": name or suggest_name(state, predicted.hand_type, actual_score),
        "provenance": "captured",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_log_id": entry.get("id"),
        "note": note or (
            "Captured from a real game. expected_score is what the game awarded, "
            "not what this project computed."
        ),
        "state_before": state,
        "cards_played": cards,
        "expected_score": actual_score,
        # What we thought at the time. A record, never an expectation.
        "scorer_said": {
            "chips": round(predicted.chips, 4),
            "mult": round(predicted.mult, 4),
            "score": predicted.score,
            "hand_type": predicted.hand_type,
            "exact": predicted.exact,
            "stochastic": predicted.stochastic,
            "unmodelled": list(predicted.unmodelled),
        },
        "agrees_at_capture": agrees,
    }

    if actual_chips is not None:
        fixture["expected_chips"] = actual_chips
    if actual_mult is not None:
        fixture["expected_mult"] = actual_mult

    if not agrees:
        fixture["disagreement"] = (
            f"The game awarded {actual_score}; the scorer computed "
            f"{predicted.score}"
            + (
                f" (a FLOOR - unmodelled: {', '.join(predicted.unmodelled)})"
                if not predicted.exact
                else ""
            )
            + (
                " (an EXPECTED value - a random effect is in play, so a single "
                "hand is not expected to match exactly)"
                if predicted.stochastic
                else ""
            )
            + ". This fixture will fail until the scorer is fixed, which is the "
            "point of capturing it."
        )

    return fixture


def write_fixture(fixture: dict[str, Any], directory: Path | None = None) -> Path:
    """Write the fixture, refusing to clobber an existing one."""
    directory = directory or FIXTURES
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{fixture['name']}.json"
    suffix = 2
    while path.exists():
        # Two captures of the same board at the same score are still separate
        # observations, and losing one would quietly shrink the evidence.
        path = directory / f"{fixture['name']}_{suffix}.json"
        suffix += 1

    path.write_text(json.dumps(fixture, indent=2) + "\n")
    return path
