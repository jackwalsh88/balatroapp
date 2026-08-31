"""Candidate play and discard enumeration.

Spec section 4: given 8 cards, enumerate every subset of size 1-5, classify it,
and score it. That is 218 subsets and costs nothing.

Ranking is fixed and deterministic:
  1. clears the blind (boolean, dominant)
  2. score, descending
  3. gold/steel value forfeited, ascending (tiebreak)
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterator

from . import data
from .scorer import ScoreResult, score_play

__all__ = [
    "enumerate_plays",
    "enumerate_discards",
    "rank_candidates",
    "legal_play_sizes",
    "MAX_PLAY_SIZE",
]

MAX_PLAY_SIZE = 5


def legal_play_sizes(state: dict[str, Any]) -> range:
    """Play sizes the current blind actually permits.

    The Psychic forces exactly five cards. Enumerating plays it would reject
    and then recommending one is a legality bug the validator would have to
    catch; not generating them is cheaper and clearer.
    """
    entry = data.blind((state.get("blind") or {}).get("key"))
    if entry and entry.get("constraint") == "min_played_cards_5":
        return range(5, 6)
    return range(1, MAX_PLAY_SIZE + 1)


def _remaining_to_clear(state: dict[str, Any]) -> int | None:
    blind = state.get("blind")
    if not blind or blind.get("requirement") is None:
        return None
    return max(0, blind["requirement"] - (blind.get("current_score") or 0))


def _clears(result: ScoreResult, needed: int | None) -> bool | None:
    """None means unknowable, and must not be reported as False.

    A non-exact score cannot support a clears/does-not-clear claim in either
    direction unless it already exceeds the requirement on its floor.
    """
    if needed is None:
        return None
    if result.exact:
        return result.score >= needed
    return True if result.score >= needed else None


def _sort_key(cand: dict[str, Any]) -> tuple:
    return (
        0 if cand.get("clears_blind") else (1 if cand.get("clears_blind") is None else 2),
        -cand["score"],
        # Spec section 4's tiebreak is "Gold/Steel value forfeited, ascending".
        # Gold is $3 given up; Steel is a x1.5 given up by playing the card
        # instead of holding it. Both are real costs the score does not show.
        cand.get("gold_forfeited", 0) + cand.get("steel_forfeited", 0),
        # Beyond the spec's three criteria, prefer playing fewer cards. Two
        # plays scoring identically are not equivalent: the shorter one keeps
        # cards in hand. Without this the ordering among ties is arbitrary.
        len(cand["cards"]),
    )


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the spec's ranking. Stable, so equal candidates keep hand order."""
    return sorted(candidates, key=_sort_key)


def _subsets(n: int, sizes: range) -> Iterator[tuple[int, ...]]:
    for size in sizes:
        if size > n:
            break
        yield from combinations(range(n), size)


def enumerate_plays(state: dict[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    """Every legal play from ``current_hand``, scored and ranked.

    ``limit`` truncates the returned list *after* ranking - it never changes
    which play comes first.
    """
    hand = state.get("current_hand") or []
    if not hand:
        return []
    needed = _remaining_to_clear(state)

    out = []
    for combo in _subsets(len(hand), legal_play_sizes(state)):
        result = score_play(state, list(combo))
        out.append(result.as_candidate(list(combo), _clears(result, needed)))

    ranked = rank_candidates(out)
    return ranked[:limit] if limit else ranked


def enumerate_discards(state: dict[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    """Discard options, each reporting the guaranteed floor from what is kept.

    Spec section 4 deliberately defers draw evaluation to a later version:
    ``expected_score`` and ``p_clears_blind`` stay null in v1 rather than being
    filled with a guess. When they are added they must be Monte Carlo sampled
    (~200 draws), never enumerated over the draw space.
    """
    hand = state.get("current_hand") or []
    if not hand or (state.get("resources") or {}).get("discards_remaining", 0) <= 0:
        return []

    sizes = range(1, MAX_PLAY_SIZE + 1)
    play_sizes = legal_play_sizes(state)
    out = []

    for combo in _subsets(len(hand), sizes):
        discard = set(combo)
        keep = [i for i in range(len(hand)) if i not in discard]
        if not keep:
            continue

        kept_cards = [hand[i] for i in keep]
        best: ScoreResult | None = None
        best_idx: tuple[int, ...] = ()
        for sub in _subsets(len(kept_cards), play_sizes):
            result = score_play(state, list(sub), hand=kept_cards)
            if best is None or result.score > best.score:
                best, best_idx = result, sub

        if best is None:
            # Every play size is illegal under this blind given what is kept.
            out.append({
                "discard": sorted(discard),
                "keep": keep,
                "floor_score": 0,
                "floor_hand_type": None,
                "expected_score": None,
                "p_clears_blind": None,
                "samples": None,
                "exact": True,
                "unmodelled": [],
            })
            continue

        out.append({
            "discard": sorted(discard),
            "keep": keep,
            "floor_score": best.score,
            "floor_hand_type": best.hand_type,
            "expected_score": None,
            "p_clears_blind": None,
            "samples": None,
            "exact": best.exact,
            "unmodelled": list(best.unmodelled),
            "floor_play": [keep[i] for i in best_idx],
        })

    out.sort(key=lambda d: (-d["floor_score"], len(d["discard"])))
    return out[:limit] if limit else out
